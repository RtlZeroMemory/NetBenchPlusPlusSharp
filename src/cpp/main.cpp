#include "bench.hpp"

#include <psapi.h>

#include <algorithm>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <new>

static_assert(__cplusplus >= 202302L, "C++23 or later is required");
static_assert(sizeof(void *) == 8, "x64 is required");

#ifndef BENCH_ASAN
// Counts every global operator new call; array, nothrow and sized forms forward here.
// Per-thread cache-line shards keep counting off a shared cache line. ASAN builds keep the
// sanitizer's own allocator hooks and report no counts.
struct alignas(64) AllocationShard
{
    std::atomic<uint64_t> calls = 0, bytes = 0;
    char padding[48]; // exactly one cache line
};

static AllocationShard allocation_shards[16];
static std::atomic<unsigned> next_shard = 0;
static thread_local const unsigned shard = next_shard.fetch_add(1) % 16;

void *operator new(size_t n)
{
    allocation_shards[shard].calls.fetch_add(1, std::memory_order_relaxed);
    allocation_shards[shard].bytes.fetch_add(n, std::memory_order_relaxed);
    if (void *p = std::malloc(n ? n : 1))
    {
        return p;
    }
    throw std::bad_alloc();
}

void operator delete(void *p) noexcept
{
    std::free(p);
}
#endif

namespace bench
{
int64_t now()
{
    LARGE_INTEGER v;
    QueryPerformanceCounter(&v);
    return v.QuadPart;
}

int64_t frequency()
{
    static const int64_t f = [] {
        LARGE_INTEGER v;
        QueryPerformanceFrequency(&v);
        return v.QuadPart;
    }();
    return f;
}

uint64_t to_ns(int64_t ticks)
{
    if (ticks <= 0)
    {
        return 0;
    }
    auto t = uint64_t(ticks), f = uint64_t(frequency());
    return t / f * 1000000000ull + t % f * 1000000000ull / f;
}

int64_t to_ticks(double seconds)
{
    return int64_t(seconds * double(frequency()));
}

double to_seconds(int64_t ticks)
{
    return double(ticks) / double(frequency());
}

std::string_view mode_name(Mode m)
{
    switch (m)
    {
    case Mode::aggregate:
        return "aggregate";
    case Mode::retain_reuse:
        return "retain-reuse";
    case Mode::retain_allocate:
        return "retain-allocate";
    default:
        return "transport";
    }
}

Options parse_options(int argc, char **argv)
{
    require(argc >= 2, "usage: tcpbench server|client|process|selftest [--name value]");
    Options o;
    o.command = argv[1];
    std::string_view allowed;
    if (o.command == "server")
    {
        allowed = " port mode max-frame max-connections workers manifest-hash idle-timeout-ms "
                  "frame-timeout-ms retain-batches retain-bytes socket-buffer output "
                  "pause-every pause-ms io-cap control-stdin parser ";
    }
    else if (o.command == "client")
    {
        allowed = " port corpus mode connections duration warmup window inflight-bytes rate "
                  "arrival seed manifest-hash socket-buffer drain-seconds output io-cap ";
    }
    else if (o.command == "process")
    {
        allowed = " corpus mode duration warmup output retain-batches retain-bytes parser ";
    }
    else if (o.command == "selftest")
    {
        allowed = " corpus ";
    }
    else
    {
        fail("unknown command");
    }
    std::map<std::string, std::string, std::less<>> args;
    for (int i = 2; i < argc; i += 2)
    {
        std::string_view name = argv[i];
        require(name.starts_with("--") && name.find(' ') == std::string_view::npos && i + 1 < argc,
                "options require --name value");
        name.remove_prefix(2);
        require(allowed.find(" " + std::string(name) + " ") != std::string_view::npos &&
                    args.emplace(name, argv[i + 1]).second,
                "unknown or duplicate option: " + std::string(name));
    }
    auto text = [&](const char *name, std::string &value) {
        if (auto it = args.find(name); it != args.end())
        {
            value = it->second;
        }
    };
    auto number = [&](const char *name, auto &value, double min, double max) {
        auto it = args.find(name);
        if (it == args.end())
        {
            return;
        }
        std::remove_reference_t<decltype(value)> parsed{};
        const std::string &s = it->second;
        auto [end, ec] = std::from_chars(s.data(), s.data() + s.size(), parsed);
        require(ec == std::errc{} && end == s.data() + s.size() && !s.starts_with('+') &&
                    std::isfinite(double(parsed)) && double(parsed) >= min && double(parsed) <= max,
                "invalid --" + std::string(name));
        value = parsed;
    };
    std::string mode = "aggregate", manifest(64, '0'), parser = "simdjson";
    // BENCH_PARSER lets the shared test suites run the fast parser; results record the effective parser.
    if (const char *env = std::getenv("BENCH_PARSER"); env && std::string_view(env) == "fast")
    {
        parser = "fast";
    }
    text("parser", parser);
    require(parser == "simdjson" || parser == "fast", "invalid parser");
    o.fast_parser = parser == "fast";
    require(!o.fast_parser || fast_supported(), "--parser fast needs an AVX2 CPU");
    text("mode", mode);
    text("corpus", o.corpus);
    text("output", o.output);
    text("arrival", o.arrival);
    text("manifest-hash", manifest);
    number("port", o.port, 1, 65535);
    number("max-frame", o.max_frame, 1, double(hard_limit));
    number("max-connections", o.max_connections, 1, 1024);
    number("workers", o.workers, 1, 256);
    number("idle-timeout-ms", o.idle_ms, 1, 3600000);
    number("frame-timeout-ms", o.frame_ms, 1, 3600000);
    number("retain-batches", o.retain_batches, 1, 65536);
    number("retain-bytes", o.retain_bytes, 1, 2147483648.0);
    number("socket-buffer", o.socket_buffer, 0, 16777216);
    number("connections", o.connections, 1, 1024);
    number("window", o.window, 1, 1048576);
    number("inflight-bytes", o.inflight_bytes, 1, 2147483648.0);
    number("duration", o.duration, 0.001, 86400);
    number("warmup", o.warmup, 0, 86400);
    number("rate", o.rate, 0, 1e9);
    number("drain-seconds", o.drain, 0.001, 86400);
    number("pause-every", o.pause_every, 0, 2147483647);
    number("pause-ms", o.pause_ms, 0, 60000);
    number("io-cap", o.io_cap, 0, double(hard_limit));
    number("control-stdin", o.control_stdin, 0, 1);
    number("seed", o.seed, 0, 1.8446744073709552e19);
    if (mode == "aggregate")
    {
        o.mode = Mode::aggregate;
    }
    else if (mode == "retain-reuse")
    {
        o.mode = Mode::retain_reuse;
    }
    else if (mode == "retain-allocate")
    {
        o.mode = Mode::retain_allocate;
    }
    else
    {
        require(mode == "transport", "invalid mode");
        o.mode = Mode::transport;
    }
    require(o.arrival == "steady" || o.arrival == "poisson" || o.arrival == "burst",
            "invalid arrival");
    require(manifest.size() == 64, "manifest hash must have 64 hex digits");
    for (size_t i = 0; i < 32; ++i)
    {
        unsigned byte = 0;
        auto [end, ec] = std::from_chars(&manifest[2 * i], &manifest[2 * i] + 2, byte, 16);
        require(ec == std::errc{} && end == &manifest[2 * i] + 2, "invalid --manifest-hash");
        o.manifest[i] = uint8_t(byte);
    }
    require(o.socket_buffer == 0 || o.socket_buffer >= 1024, "invalid --socket-buffer");
    require((o.pause_every == 0) == (o.pause_ms == 0),
            "pause-every and pause-ms must both be positive or both zero");
    require(!(o.command == "client" || o.command == "process") || !o.corpus.empty(),
            "--corpus required");
    if (o.command == "client" && o.rate == 0)
    {
        require(o.window % o.connections == 0,
                "closed loop requires window to be a multiple of connections");
    }
    if (o.command == "server")
    {
        require(uint64_t(o.max_frame) * uint64_t(o.max_connections) <= (uint64_t(1) << 30) &&
                    (!retains(o.mode) ||
                     uint64_t(o.retain_bytes) * uint64_t(o.max_connections) <= (uint64_t(1) << 31)),
                "scenario exceeds application memory envelope");
    }
    return o;
}

std::string quoted(std::string_view s)
{
    std::string r = "\"";
    for (unsigned char c : s)
    {
        if (c == '"' || c == '\\')
        {
            r += '\\';
            r += char(c);
        }
        else if (c < 32)
        {
            char b[8];
            std::snprintf(b, sizeof(b), "\\u%04x", c);
            r += b;
        }
        else
        {
            r += char(c);
        }
    }
    return r + '"';
}

void Json::key(std::string_view k)
{
    if (!first.empty())
    {
        if (!first.back())
        {
            out += ',';
        }
        first.back() = false;
    }
    if (!k.empty())
    {
        out += quoted(k);
        out += ':';
    }
}

Json &Json::open(std::string_view k)
{
    key(k);
    out += '{';
    first.push_back(true);
    return *this;
}

Json &Json::open_array(std::string_view k)
{
    key(k);
    out += '[';
    first.push_back(true);
    return *this;
}

Json &Json::close()
{
    out += '}';
    first.pop_back();
    return *this;
}

Json &Json::close_array()
{
    out += ']';
    first.pop_back();
    return *this;
}

Json &Json::raw(std::string_view k, std::string_view json)
{
    key(k);
    out += json;
    return *this;
}

Json &Json::str(std::string_view k, std::string_view v)
{
    return raw(k, quoted(v));
}

Json &Json::num(std::string_view k, uint64_t v)
{
    return raw(k, std::to_string(v));
}

Json &Json::num(std::string_view k, int64_t v)
{
    return raw(k, std::to_string(v));
}

Json &Json::num(std::string_view k, double v)
{
    if (!std::isfinite(v))
    {
        return null(k);
    }
    char b[32];
    auto [end, ec] = std::to_chars(b, b + sizeof(b), v);
    return raw(k, std::string_view(b, size_t(end - b)));
}

Json &Json::boolean(std::string_view k, bool v)
{
    return raw(k, v ? "true" : "false");
}

Json &Json::null(std::string_view k)
{
    return raw(k, "null");
}

std::string Json::take()
{
    return std::move(out);
}

size_t Histogram::index(uint64_t ns)
{
    if (ns < 1024)
    {
        return size_t(ns);
    }
    unsigned e = unsigned(std::bit_width(ns)) - 1;
    return 1024 + (e - 10) * 256 + size_t((ns >> (e - 8)) - 256);
}

uint64_t Histogram::upper(size_t i)
{
    if (i < 1024)
    {
        return i;
    }
    size_t n = i - 1024;
    unsigned shift = unsigned(n / 256) + 2;
    return ((257 + n % 256) << shift) - 1;
}

void Histogram::add(uint64_t ns)
{
    ++count;
    maximum = std::max(maximum, ns);
    if (ns >= overflow_ns)
    {
        ++overflow;
    }
    else
    {
        ++counts[index(ns)];
    }
}

void Histogram::merge(const Histogram &h)
{
    for (size_t i = 0; i < bins; ++i)
    {
        counts[i] += h.counts[i];
    }
    count += h.count;
    maximum = std::max(maximum, h.maximum);
    overflow += h.overflow;
}

void Histogram::clear()
{
    std::fill(counts.begin(), counts.end(), 0);
    count = maximum = overflow = 0;
}

std::optional<uint64_t> Histogram::quantile(double q) const
{
    auto target = uint64_t(std::ceil(q * double(count)));
    uint64_t seen = 0;
    for (size_t i = 0; count && i < bins; ++i)
    {
        if ((seen += counts[i]) >= target)
        {
            return upper(i);
        }
    }
    return std::nullopt; // empty, or the rank falls in overflow
}

void Histogram::write(Json &json, std::string_view key, bool stage) const
{
    auto q = [&](const char *name, double p, bool gated) {
        auto v = gated ? std::nullopt : quantile(p);
        v ? json.num(name, *v) : json.null(name);
    };
    json.open(key).num("count", count);
    q("p50_ns", .5, false);
    q("p90_ns", .9, false);
    q("p99_ns", .99, stage && count < 10000);
    q("p999_ns", .999, stage && count < 1000000);
    json.num("max_ns", maximum).num("overflow_60s", overflow).open_array("buckets");
    for (size_t i = 0; i < bins; ++i)
    {
        if (counts[i])
        {
            json.raw({}, "[" + std::to_string(upper(i)) + "," + std::to_string(counts[i]) + "]");
        }
    }
    json.close_array().close();
}

Resources Resources::sample()
{
    Resources r;
    r.at = now();
    FILETIME create, exit, kernel, user;
    require(GetProcessTimes(GetCurrentProcess(), &create, &exit, &kernel, &user) != 0,
            "process_times");
    auto t = [](FILETIME f) {
        return double((uint64_t(f.dwHighDateTime) << 32) | f.dwLowDateTime) / 1e7;
    };
    r.cpu_seconds = t(kernel) + t(user);
    PROCESS_MEMORY_COUNTERS_EX m{};
    m.cb = sizeof(m);
    require(GetProcessMemoryInfo(GetCurrentProcess(),
                                 reinterpret_cast<PROCESS_MEMORY_COUNTERS *>(&m),
                                 sizeof(m)) != 0,
            "process_memory");
    r.peak_working_set = m.PeakWorkingSetSize;
#ifndef BENCH_ASAN
    for (const AllocationShard &s : allocation_shards)
    {
        r.new_calls += s.calls.load(std::memory_order_relaxed);
        r.new_bytes += s.bytes.load(std::memory_order_relaxed);
    }
#endif
    return r;
}

void fail(std::string_view reason)
{
    throw std::runtime_error(std::string(reason));
}

void write_build(Json &json, bool fast_parser)
{
    DWORD_PTR process_mask = 0, system_mask = 0;
    GetProcessAffinityMask(GetCurrentProcess(), &process_mask, &system_mask);
    json.open("build")
        .str("compiler", "MSVC " + std::to_string(_MSC_FULL_VER))
        .num("cplusplus", int64_t(__cplusplus))
        .str("configuration", BENCH_CONFIG)
        .str("windows_sdk", BENCH_WINDOWS_SDK)
        .str("parser", fast_parser ? "FastJson port (schema-specific, SIMD scanning)" : "simdjson 3.12.3 On-Demand")
        .str("parser_kernel", fast_parser ? fast_kernel() : simdjson::get_active_implementation()->name())
        .str("parser_builtin", simdjson::builtin_implementation()->name()) // On-Demand front end, fixed at compile time
        .str("arch", BENCH_ARCH_FLAG)
        .num("qpc_frequency", frequency())
        .num("affinity_mask", uint64_t(process_mask))
#ifdef BENCH_ASAN
        .boolean("sanitized", true)
#else
        .boolean("sanitized", false)
#endif
        .close();
}

void emit(const Options &o, const std::string &json)
{
    if (!o.output.empty())
    {
        std::ofstream f(o.output, std::ios::binary | std::ios::trunc);
        f << json << '\n';
        require(bool(f), "output_write");
    }
    std::cout << json << std::endl;
}

Socket::~Socket()
{
    if (value != INVALID_SOCKET)
    {
        closesocket(value);
    }
}

void configure_socket(SOCKET s, int buffer)
{
    int one = 1;
    require(setsockopt(
                s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char *>(&one), sizeof(one)) ==
                0,
            "TCP_NODELAY");
    if (buffer > 0)
    {
        require(setsockopt(s,
                           SOL_SOCKET,
                           SO_SNDBUF,
                           reinterpret_cast<const char *>(&buffer),
                           sizeof(buffer)) == 0 &&
                    setsockopt(s,
                               SOL_SOCKET,
                               SO_RCVBUF,
                               reinterpret_cast<const char *>(&buffer),
                               sizeof(buffer)) == 0,
                "socket_buffer");
    }
}

std::pair<int, int> socket_buffers(SOCKET s)
{
    int send = 0, receive = 0, length = sizeof(int);
    getsockopt(s, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<char *>(&send), &length);
    length = sizeof(int);
    getsockopt(s, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<char *>(&receive), &length);
    return {send, receive};
}

sockaddr_in loopback(int port)
{
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(uint16_t(port));
    return address;
}

std::array<uint8_t, header_bytes> make_header(uint16_t type, uint64_t sequence, size_t length)
{
    std::array<uint8_t, header_bytes> b{};
    write_be(b.data(), length, 4);
    write_be(b.data() + 4, 1, 2);
    write_be(b.data() + 6, type, 2);
    write_be(b.data() + 8, sequence, 8);
    return b;
}

void spin_pause(int milliseconds, const Budget &budget)
{
    const int64_t finish = now() + to_ticks(milliseconds / 1000.0);
    while (now() < finish)
    {
        budget.check();
        YieldProcessor();
    }
    budget.check();
}
} // namespace bench

int main(int argc, char **argv)
{
    try
    {
        auto o = bench::parse_options(argc, argv);
        if (o.command == "selftest")
        {
            return bench::run_selftest(o.corpus);
        }
        if (o.command == "process")
        {
            return bench::run_process(o);
        }
        WSADATA w{};
        bench::require(WSAStartup(MAKEWORD(2, 2), &w) == 0, "WSAStartup");
        int result = o.command == "server" ? bench::run_server(o) : bench::run_client(o);
        WSACleanup();
        return result;
    }
    catch (const std::exception &e)
    {
        // Also replace any stale --output file, so a failed run never leaves an old result.
        bench::Options failed;
        for (int i = 2; i + 1 < argc; i += 2)
        {
            failed.output = std::string_view(argv[i]) == "--output" ? argv[i + 1] : failed.output;
        }
        std::string json =
            "{\"event\":\"error\",\"valid\":false,\"error\":" + bench::quoted(e.what()) + "}";
        try
        {
            bench::emit(failed, json);
        }
        catch (const std::exception &)
        {
            std::cout << json << std::endl;
        }
        return 2;
    }
}
