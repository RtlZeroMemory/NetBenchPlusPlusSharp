#pragma once
#include <winsock2.h>

#include <windows.h>

#include <simdjson.h>

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace bench
{
constexpr uint64_t fnv_offset = 14695981039346656037ull;
constexpr uint64_t fnv_prime = 1099511628211ull;
constexpr size_t header_bytes = 16;
constexpr size_t summary_bytes = 1056;
constexpr size_t hard_limit = size_t(16) << 20;
constexpr uint64_t epoch_record_limit = 1000000000;
constexpr uint64_t stage_sample_every = 1024;

[[noreturn]] __declspec(noinline) void fail(std::string_view reason); // out of line, like the managed Fail

inline void require(bool ok, std::string_view reason)
{
    if (!ok)
    {
        fail(reason);
    }
}

inline uint64_t read_be(const uint8_t *p, size_t n)
{
    uint64_t x = 0;
    for (size_t i = 0; i < n; ++i)
    {
        x = (x << 8) | p[i];
    }
    return x;
}

inline void write_be(uint8_t *p, uint64_t x, size_t n)
{
    for (size_t i = n; i > 0; --i)
    {
        p[i - 1] = uint8_t(x);
        x >>= 8;
    }
}

inline void hash_byte(uint64_t &h, uint8_t v)
{
    h = (h ^ v) * fnv_prime;
}

// Big-endian FNV-1a over n bytes of v; n is a compile-time constant at every call site.
inline void hash_int(uint64_t &h, uint64_t v, size_t n)
{
    for (size_t i = n; i > 0; --i)
    {
        hash_byte(h, uint8_t(v >> ((i - 1) * 8)));
    }
}

// QueryPerformanceCounter ticks: the only benchmark clock.
int64_t now();
int64_t frequency();
uint64_t to_ns(int64_t ticks);
int64_t to_ticks(double seconds);
double to_seconds(int64_t ticks);

enum class Mode
{
    aggregate,
    retain_reuse,
    retain_allocate,
    transport
};

inline bool retains(Mode m)
{
    return m == Mode::retain_reuse || m == Mode::retain_allocate;
}

std::string_view mode_name(Mode m);

struct Options
{
    std::string command, corpus, output, arrival = "steady";
    Mode mode = Mode::aggregate;
    int port = 9000, max_connections = 32, workers = 4, connections = 4, window = 8;
    int idle_ms = 5000, frame_ms = 30000, socket_buffer = 262144, pause_every = 0, pause_ms = 0;
    int io_cap = 0, control_stdin = 0;
    size_t max_frame = 1048576, retain_batches = 8, retain_bytes = 67108864;
    size_t inflight_bytes = 67108864;
    double duration = 10, warmup = 2, rate = 0, drain = 30;
    uint64_t seed = 42;
    std::array<uint8_t, 32> manifest{};
    bool fast_parser = false; // --parser fast (default simdjson, or the BENCH_PARSER environment variable)
};

Options parse_options(int argc, char **argv);

// Minimal streaming JSON writer: commas and nesting are tracked, values are written as given.
class Json
{
    std::string out;
    std::vector<bool> first;

    void key(std::string_view k);

  public:
    Json &open(std::string_view k = {});
    Json &open_array(std::string_view k = {});
    Json &close();
    Json &close_array();
    Json &raw(std::string_view k, std::string_view json);
    Json &str(std::string_view k, std::string_view v);
    Json &num(std::string_view k, uint64_t v);
    Json &num(std::string_view k, int64_t v);

    Json &num(std::string_view k, int v)
    {
        return num(k, int64_t(v));
    }

    Json &num(std::string_view k, double v);
    Json &boolean(std::string_view k, bool v);
    Json &null(std::string_view k);
    std::string take();
};

std::string quoted(std::string_view s);

// Shared log-linear histogram: exact ns through 1023, then 256 buckets per power of two up
// to 2^36 ns. Values of at least 60 s count as overflow and have no finite bucket.
struct Histogram
{
    static constexpr size_t bins = 1024 + 26 * 256;
    static constexpr uint64_t overflow_ns = 60000000000ull;
    std::vector<uint64_t> counts = std::vector<uint64_t>(bins);
    uint64_t count = 0, maximum = 0, overflow = 0;

    static size_t index(uint64_t ns);
    static uint64_t upper(size_t index);
    void add(uint64_t ns);
    void merge(const Histogram &other);
    void clear();
    std::optional<uint64_t> quantile(double q) const;
    // Stage histograms suppress p99 below 10,000 and p99.9 below 1,000,000 samples.
    void write(Json &json, std::string_view key, bool stage = false) const;
};

struct Result
{
    uint64_t records = 0, digest = fnv_offset;
    std::array<uint64_t, 64> counts{};
    std::array<int64_t, 64> sums{};
    bool operator==(const Result &) const = default;
};

struct Epoch
{
    uint64_t batches = 0, bytes = 0, records = 0, digest = fnv_offset;
    std::array<uint64_t, 64> counts{};
    std::array<int64_t, 64> sums{};
    void check(const Result &r) const;
    void add(const Result &r, size_t payload, uint64_t sequence);
    std::array<uint8_t, summary_bytes> encode() const;
};

// Owned, canonical record. `begin`/`length` index the batch's own text storage.
struct Row
{
    uint64_t id = 0, timestamp = 0;
    uint32_t source = 0, flags = 0, begin = 0, length = 0;
    int64_t value = 0;
    uint8_t kind = 0;
};
static_assert(sizeof(Row) == 48, "owned-capacity accounting assumes 48-byte rows in both implementations");

// Chunked like the managed side, so no single block grows large: the first chunk grows geometrically
// from 16 rows / 256 bytes, later chunks are allocated full (1,024 rows, 64 KiB of text). A message
// (at most 4 KiB) never straddles chunks; Row::begin is chunk * 65,536 + position.
struct Batch
{
    static constexpr size_t row_chunk = 1024, text_chunk = 65536;
    std::vector<std::vector<Row>> rows;
    std::vector<std::vector<uint8_t>> text;
    size_t count = 0, current = 0; // records; text chunk being filled
    uint64_t canonical = 0, digest = fnv_offset;
    void clear()
    {
        for (auto &r : rows)
            r.clear();
        for (auto &t : text)
            t.clear();
        count = current = 0;
        canonical = 0;
    }
};

// Cooperative processing budget: shutdown plus the earlier of frame/idle deadlines.
struct Budget
{
    const std::atomic<bool> *stop = nullptr;
    int64_t deadline = 0;
    std::string_view reason = "frame_timeout";
    void check() const;
};

// Canonical accounting shared by both parsers (processing.cpp).
void add_row(Result &r, const Row &row, std::string_view message);
void append(Batch &b, const Row &row, std::string_view text, uint64_t limit);
// The schema-specific parser and its UTF-8 validator (fastjson.cpp). scratch holds scratch_bytes.
constexpr size_t fast_scratch_bytes = 24576;
Result fast_parse(const uint8_t *data, size_t length, Batch *batch, const Budget &budget, uint8_t *scratch,
                  uint64_t retain_bytes);
bool utf8_valid(const uint8_t *data, size_t length);
const char *fast_kernel(); // "avx512" or "avx2": the widest scan the fast parser uses here
bool fast_supported();     // the fast parser needs AVX2
uint64_t fuzz_selftest(); // selftest_fuzz.cpp

class Processor
{
    Mode mode;
    bool fast_parser;
    std::vector<uint8_t> fast_scratch; // fast parser only
    size_t retain_batches;
    uint64_t retain_bytes;
    simdjson::ondemand::parser parser;
    std::vector<Batch> pool;   // retain_batches + 1 slots in retained modes
    std::vector<size_t> live;  // committed slots, oldest first
    std::vector<size_t> spare; // uncommitted slots; back() is the next scratch
    Result parse(
        const uint8_t *data, size_t length, size_t capacity, Batch *batch, const Budget &budget);
    Result visit(const Batch &batch, const Budget &budget) const;
    void observe_owned();

  public:
    uint64_t retained = 0, peak_retained = 0, peak_owned = 0;
    uint64_t last_decode_ticks = 0, last_visit_ticks = 0;
    Processor(Mode mode, size_t max_frame, size_t retain_batches, uint64_t retain_bytes, bool fast = false);
    std::vector<uint8_t> &scratch_for_tests() // selftest hook: fast-parser canaries
    {
        return fast_scratch;
    }
    // Validates, processes and commits one batch, or throws with no committed change.
    Result apply(const uint8_t *data,
                 size_t length,
                 size_t capacity,
                 Epoch &epoch,
                 uint64_t sequence,
                 const Budget &budget = {},
                 bool sample = false);
    void verify(const Budget &budget = {}) const;
    uint64_t owned_capacity() const;

    Batch &live_batch(size_t i) // selftest hook
    {
        return pool[live.at(i)];
    }
};

struct Frame
{
    std::vector<uint8_t> payload; // length + SIMDJSON_PADDING bytes
    size_t length = 0;
    Result expected;
};

struct Corpus
{
    std::vector<Frame> frames;
    uint64_t payload_bytes = 0, max_frame = 0;
};

Corpus load_corpus(const std::string &path);
Result transport_result(size_t length);

// Timestamped process snapshot; `new_calls`/`new_bytes` count global operator new.
struct Resources
{
    int64_t at = 0;
    double cpu_seconds = 0;
    uint64_t peak_working_set = 0, new_calls = 0, new_bytes = 0;
    static Resources sample();
};

void write_build(Json &json, bool fast_parser = false);

struct Socket
{
    SOCKET value = INVALID_SOCKET;
    Socket() = default;

    explicit Socket(SOCKET s) : value(s) {}

    Socket(const Socket &) = delete;
    Socket &operator=(const Socket &) = delete;
    ~Socket();
};

// TCP_NODELAY always; buffer sizes only when positive (0 keeps Windows autotuning).
void configure_socket(SOCKET s, int buffer);
std::pair<int, int> socket_buffers(SOCKET s); // effective SO_SNDBUF, SO_RCVBUF
sockaddr_in loopback(int port);
std::array<uint8_t, header_bytes> make_header(uint16_t type, uint64_t sequence, size_t length);

// Diagnostic delay: a budget-checked busy spin, so sub-millisecond pauses are honoured.
void spin_pause(int milliseconds, const Budget &budget);

int run_server(const Options &o);
int run_client(const Options &o);
int run_process(const Options &o);
int run_selftest(const std::string &corpus);
void client_selftest();
void emit(const Options &o, const std::string &json);
} // namespace bench
