#include "bench.hpp"

#include <algorithm>
#include <bit>
#include <cstring>
#include <fstream>
#include <iostream>

namespace bench
{
void Budget::check() const
{
    if (stop && stop->load(std::memory_order_relaxed))
    {
        fail("shutdown");
    }
    if (deadline && now() >= deadline)
    {
        fail(reason);
    }
}

void Epoch::check(const Result &r) const
{
    require(r.records <= epoch_record_limit - records, "epoch_record_limit");
}

void Epoch::add(const Result &r, size_t payload, uint64_t sequence)
{
    check(r);
    ++batches;
    bytes += payload;
    records += r.records;
    hash_int(digest, sequence, 8);
    hash_int(digest, r.records, 8);
    hash_int(digest, r.digest, 8);
    for (size_t i = 0; i < 64; ++i)
    {
        counts[i] += r.counts[i];
        sums[i] += r.sums[i];
    }
}

std::array<uint8_t, summary_bytes> Epoch::encode() const
{
    std::array<uint8_t, summary_bytes> b{};
    write_be(b.data(), batches, 8);
    write_be(b.data() + 8, bytes, 8);
    write_be(b.data() + 16, records, 8);
    write_be(b.data() + 24, digest, 8);
    for (size_t i = 0; i < 64; ++i)
    {
        write_be(b.data() + 32 + 8 * i, counts[i], 8);
        write_be(b.data() + 544 + 8 * i, uint64_t(sums[i]), 8);
    }
    return b;
}

Result transport_result(size_t length)
{
    Result r;
    r.digest = length;
    return r;
}

namespace
{
void add_row(Result &r, const Row &row, std::string_view message)
{
    require(r.records < epoch_record_limit, "epoch_record_limit");
    ++r.records;
    size_t bucket = size_t(row.kind) * 4 + (row.flags & 3);
    ++r.counts[bucket];
    r.sums[bucket] += row.value;
    hash_byte(r.digest, 0x52);
    hash_int(r.digest, row.id, 8);
    hash_int(r.digest, row.timestamp, 8);
    hash_int(r.digest, row.source, 4);
    hash_byte(r.digest, row.kind);
    hash_int(r.digest, uint64_t(row.value), 8);
    hash_int(r.digest, row.flags, 4);
    hash_int(r.digest, message.size(), 4);
    for (unsigned char c : message)
    {
        hash_byte(r.digest, c);
    }
}

// Strict JSON integer lexeme: digits only, no leading zero, no fraction or exponent.
// Only a 20th digit can overflow 64 bits.
bool digits(std::string_view s, uint64_t &value)
{
    if (s.empty() || s.size() > 20 || (s[0] == '0' && s.size() > 1))
    {
        return false;
    }
    uint64_t v = 0;
    for (size_t i = 0; i < s.size(); ++i)
    {
        auto d = uint64_t(uint8_t(s[i]) - uint8_t('0'));
        if (d > 9 || (i == 19 && v > (UINT64_MAX - d) / 10))
        {
            return false;
        }
        v = v * 10 + d;
    }
    value = v;
    return true;
}

// On-Demand does not validate unread scalars, so the raw token is the complete check.
std::string_view number_token(simdjson::ondemand::value &v)
{
    std::string_view s = v.raw_json_token();
    while (!s.empty() &&
           (s.back() == ' ' || s.back() == '\t' || s.back() == '\r' || s.back() == '\n'))
    {
        s.remove_suffix(1);
    }
    return s;
}

uint64_t unsigned_value(simdjson::ondemand::value &v, uint64_t max)
{
    uint64_t n = 0;
    require(digits(number_token(v), n) && n <= max, "json_unsigned");
    return n;
}

int64_t signed_value(simdjson::ondemand::value &v)
{
    std::string_view s = number_token(v);
    bool negative = s.starts_with('-');
    uint64_t n = 0;
    require(digits(s.substr(negative ? 1 : 0), n) && n <= 1000000, "json_value_range");
    return negative ? -int64_t(n) : int64_t(n);
}

unsigned field_bit(std::string_view key)
{
    return key == "id"             ? 1
           : key == "timestamp_ns" ? 2
           : key == "source"       ? 4
           : key == "kind"         ? 8
           : key == "value_milli"  ? 16
           : key == "flags"        ? 32
           : key == "message"      ? 64
                                   : 0;
}

// Identical chunked storage in both implementations (see Batch).
void append(Batch &b, const Row &row, std::string_view text, uint64_t limit)
{
    require(b.canonical <= limit && 38 + text.size() <= limit - b.canonical, "retention_capacity");
    const size_t chunk = b.count / Batch::row_chunk;
    if (chunk == b.rows.size())
    {
        b.rows.emplace_back().reserve(chunk == 0 ? 16 : Batch::row_chunk);
    }
    std::vector<Row> &rows = b.rows[chunk];
    if (rows.size() == rows.capacity())
    {
        rows.reserve(rows.capacity() * 2);
    }
    if (b.text.empty())
    {
        b.text.emplace_back().reserve(std::max<size_t>(256, text.size()));
    }
    else if (const size_t needed = b.text[b.current].size() + text.size(); needed > b.text[b.current].capacity())
    {
        std::vector<uint8_t> &current = b.text[b.current];
        if (current.capacity() < Batch::text_chunk && needed <= Batch::text_chunk)
        {
            current.reserve(std::min(Batch::text_chunk, std::max(current.capacity() * 2, needed)));
        }
        else if (++b.current == b.text.size())
        {
            b.text.emplace_back().reserve(Batch::text_chunk);
        }
    }
    std::vector<uint8_t> &out = b.text[b.current];
    Row owned = row;
    owned.begin = uint32_t(b.current * Batch::text_chunk + out.size());
    owned.length = uint32_t(text.size());
    rows.push_back(owned);
    out.insert(out.end(), text.begin(), text.end());
    ++b.count;
    b.canonical += 38 + text.size();
}
} // namespace

Processor::Processor(Mode m, size_t max_frame, size_t batches, uint64_t bytes)
    : mode(m), retain_batches(batches), retain_bytes(bytes)
{
    if (mode != Mode::transport)
    {
        require(parser.allocate(std::max<size_t>(max_frame, 32), 4) == simdjson::SUCCESS,
                "parser_capacity");
    }
    if (retains(mode))
    {
        pool.resize(retain_batches + 1);
        live.reserve(retain_batches);
        for (size_t i = pool.size(); i > 0; --i)
        {
            spare.push_back(i - 1);
        }
    }
}

Result Processor::parse(
    const uint8_t *data, size_t length, size_t capacity, Batch *batch, const Budget &budget)
{
    require(!(length >= 3 && data[0] == 0xef && data[1] == 0xbb && data[2] == 0xbf), "json_bom");
    Result r;
    uint64_t parsed = 0;
    simdjson::ondemand::document doc = parser.iterate(data, length, capacity);
    for (auto item : doc.get_array())
    {
        if (parsed++ % 1024 == 0)
        {
            budget.check();
        }
        Row row;
        std::string_view message;
        unsigned seen = 0;
        for (auto field : item.get_object())
        {
            // Compare raw names; only escaped names need unescaping (duplicates compare decoded).
            std::string_view key = field.escaped_key();
            if (key.find('\\') != std::string_view::npos)
            {
                key = field.unescaped_key();
            }
            unsigned bit = field_bit(key);
            require(bit && !(seen & bit), "json_fields");
            seen |= bit;
            simdjson::ondemand::value v = field.value();
            switch (bit)
            {
            case 1:
                row.id = unsigned_value(v, UINT64_MAX);
                break;
            case 2:
                row.timestamp = unsigned_value(v, UINT64_MAX);
                break;
            case 4:
                row.source = uint32_t(unsigned_value(v, UINT32_MAX));
                break;
            case 8: {
                std::string_view kind = v.get_string();
                require(kind.size() == 6 && kind.starts_with("kind") &&
                            (kind[4] == '0' || kind[4] == '1') && kind[5] >= '0' &&
                            kind[5] <= '9' && (kind[4] == '0' || kind[5] <= '5'),
                        "json_kind");
                row.kind = uint8_t((kind[4] - '0') * 10 + (kind[5] - '0'));
                break;
            }
            case 16:
                row.value = signed_value(v);
                break;
            case 32:
                row.flags = uint32_t(unsigned_value(v, UINT32_MAX));
                break;
            default:
                // Stage 1 validated all UTF-8; get_string rejects lone surrogate escapes.
                message = v.get_string();
                require(message.size() <= 4096, "json_message_length");
            }
        }
        require(seen == 127, "json_fields");
        if (batch)
        {
            append(*batch, row, message, retain_bytes);
        }
        else
        {
            add_row(r, row, message);
        }
    }
    require(doc.at_end(), "json_trailing");
    return r;
}

Result Processor::visit(const Batch &b, const Budget &budget) const
{
    Result r;
    for (size_t i = 0; i < b.count; ++i)
    {
        if (r.records % 1024 == 0)
        {
            budget.check();
        }
        const Row &row = b.rows[i / Batch::row_chunk][i % Batch::row_chunk];
        // An empty message right after an exactly full chunk points one chunk past the last.
        auto text = row.length ? reinterpret_cast<const char *>(b.text[row.begin / Batch::text_chunk].data()) : "";
        add_row(r, row, std::string_view(text + (row.length ? row.begin % Batch::text_chunk : 0), row.length));
    }
    budget.check();
    return r;
}

uint64_t Processor::owned_capacity() const
{
    uint64_t n = 0;
    for (const Batch &b : pool)
    {
        for (const auto &r : b.rows)
            n += r.capacity() * sizeof(Row);
        for (const auto &t : b.text)
            n += t.capacity();
    }
    return n;
}

void Processor::observe_owned()
{
    peak_owned = std::max(peak_owned, owned_capacity());
}

Result Processor::apply(const uint8_t *data,
                        size_t length,
                        size_t capacity,
                        Epoch &epoch,
                        uint64_t sequence,
                        const Budget &budget,
                        bool sample)
{
    budget.check();
    const int64_t start = sample ? now() : 0;
    if (!retains(mode))
    {
        Result r = mode == Mode::transport ? transport_result(length)
                                           : parse(data, length, capacity, nullptr, budget);
        if (sample)
        {
            last_decode_ticks = uint64_t(now() - start);
            last_visit_ticks = 0;
        }
        budget.check();
        epoch.add(r, length, sequence); // checks the epoch record limit before changing state
        return r;
    }
    // Scratch is a free slot, never committed state. Allocate mode starts from empty storage.
    const size_t slot = spare.back();
    Batch &scratch = pool[slot];
    if (mode == Mode::retain_allocate)
    {
        scratch = Batch{};
    }
    scratch.clear();
    try
    {
        parse(data, length, capacity, &scratch, budget);
    }
    catch (...)
    {
        observe_owned(); // a failed parse can still have grown scratch
        throw;
    }
    observe_owned(); // committed batches plus incoming scratch at their joint high-water mark
    const int64_t decoded = sample ? now() : 0;
    Result r = visit(scratch, budget);
    scratch.digest = r.digest;
    // Verify every planned eviction before any committed state changes.
    size_t evict = 0;
    uint64_t remaining = retained;
    while (evict < live.size() &&
           (live.size() - evict >= retain_batches || scratch.canonical > retain_bytes - remaining))
    {
        const Batch &old = pool[live[evict]];
        require(visit(old, budget).digest == old.digest, "retained_integrity");
        remaining -= old.canonical;
        ++evict;
    }
    epoch.check(r);
    budget.check();
    // Commit: no failure point from here on.
    spare.pop_back();
    for (size_t i = 0; i < evict; ++i)
    {
        if (mode == Mode::retain_allocate)
        {
            pool[live[i]] = Batch{};
        }
        spare.push_back(live[i]);
    }
    live.erase(live.begin(), live.begin() + ptrdiff_t(evict));
    live.push_back(slot);
    retained = remaining + scratch.canonical;
    peak_retained = std::max(peak_retained, retained);
    epoch.add(r, length, sequence);
    if (sample)
    {
        last_decode_ticks = uint64_t(decoded - start);
        last_visit_ticks = uint64_t(now() - decoded);
    }
    return r;
}

void Processor::verify(const Budget &budget) const
{
    budget.check();
    for (size_t slot : live)
    {
        require(visit(pool[slot], budget).digest == pool[slot].digest, "retained_integrity");
    }
}

Corpus load_corpus(const std::string &path)
{
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    require(bool(f), "corpus_open");
    const auto length = uint64_t(std::streamoff(f.tellg()));
    f.seekg(0);
    auto read = [&](uint8_t *p, size_t n) {
        require(bool(f.read(reinterpret_cast<char *>(p), std::streamsize(n))), "corpus_truncated");
    };
    std::array<uint8_t, 16> h{};
    require(length >= h.size(), "corpus_header");
    read(h.data(), h.size());
    const uint64_t count = read_be(h.data() + 8, 4);
    require(std::memcmp(h.data(), "TCPBCH01", 8) == 0 && read_be(h.data() + 12, 4) == 0 &&
                count > 0 && count <= (length - 16) / 1045,
            "corpus_header");
    Corpus corpus;
    corpus.frames.reserve(size_t(count));
    uint64_t consumed = 16;
    std::array<uint8_t, 1044> meta{};
    for (uint64_t i = 0; i < count; ++i)
    {
        read(meta.data(), meta.size());
        consumed += meta.size();
        Frame frame;
        frame.length = size_t(read_be(meta.data(), 4));
        require(frame.length > 0 && frame.length <= hard_limit && frame.length <= length - consumed,
                "corpus_frame");
        frame.expected.records = read_be(meta.data() + 4, 8);
        frame.expected.digest = read_be(meta.data() + 12, 8);
        require(frame.expected.records <= epoch_record_limit, "corpus_frame");
        uint64_t total = 0;
        for (size_t b = 0; b < 64; ++b)
        {
            uint64_t c = read_be(meta.data() + 20 + 8 * b, 8);
            auto s = std::bit_cast<int64_t>(read_be(meta.data() + 532 + 8 * b, 8));
            require(c <= frame.expected.records - total && s >= -int64_t(c) * 1000000 &&
                        s <= int64_t(c) * 1000000,
                    "corpus_metadata");
            total += c;
            frame.expected.counts[b] = c;
            frame.expected.sums[b] = s;
        }
        require(total == frame.expected.records, "corpus_metadata");
        frame.payload.resize(frame.length + simdjson::SIMDJSON_PADDING);
        read(frame.payload.data(), frame.length);
        consumed += frame.length;
        corpus.payload_bytes += frame.length;
        corpus.max_frame = std::max<uint64_t>(corpus.max_frame, frame.length);
        corpus.frames.push_back(std::move(frame));
    }
    require(consumed == length, "corpus_trailing");
    return corpus;
}

// Processing-only control: full-corpus cycles through the server's processor, no sockets.
int run_process(const Options &o)
{
    Corpus corpus = load_corpus(o.corpus);
    Processor processor(o.mode, corpus.max_frame, o.retain_batches, o.retain_bytes);
    Epoch epoch;
    uint64_t sequence = 0, frames = 0, records = 0, bytes = 0;
    auto cycle = [&] {
        for (const Frame &f : corpus.frames)
        {
            Result want = o.mode == Mode::transport ? transport_result(f.length) : f.expected;
            if (want.records > epoch_record_limit - epoch.records)
            {
                epoch = {};
            }
            Result got =
                processor.apply(f.payload.data(), f.length, f.payload.size(), epoch, ++sequence);
            require(got == want, "process_oracle_mismatch");
            ++frames;
            records += got.records;
            bytes += f.length;
        }
    };
    for (int64_t until = now() + to_ticks(o.warmup); now() < until;)
    {
        cycle();
    }
    frames = records = bytes = 0;
    const Resources before = Resources::sample();
    const int64_t start = now();
    do
    {
        cycle(); // whole cycles only; elapsed can exceed the requested duration
    } while (now() - start < to_ticks(o.duration));
    const double seconds = to_seconds(now() - start);
    const Resources after = Resources::sample();
    processor.verify();
    Json j;
    j.open()
        .str("role", "process")
        .str("implementation", "cpp")
        .str("mode", mode_name(o.mode))
        .boolean("valid", true)
        .null("error")
        .num("seconds", seconds)
        .num("frames", frames)
        .num("records", records)
        .num("payload_bytes", bytes)
        .num("records_per_second", double(records) / seconds)
        .num("payload_mib_per_second", double(bytes) / seconds / 1048576.0)
        .open("resources")
        .str("scope", "timed full-corpus cycles after warmup")
        .num("cpu_seconds", after.cpu_seconds - before.cpu_seconds)
        .num("allocations", after.new_calls - before.new_calls)
        .num("allocated_bytes", after.new_bytes - before.new_bytes)
        .null("gc")
        .num("peak_working_set_bytes", after.peak_working_set)
        .close()
        .open("retention")
        .num("canonical_bytes", processor.retained)
        .num("owned_capacity_peak_bytes", processor.peak_owned)
        .close();
    write_build(j);
    j.close();
    emit(o, j.take());
    return 0;
}

namespace
{
// Applies JSON text through the real padded-input path, then scribbles the input so retained
// data can never alias it.
Result apply_text(Processor &p, Epoch &epoch, const std::string &s, const Budget &budget = {})
{
    std::vector<uint8_t> b(s.begin(), s.end());
    b.resize(s.size() + simdjson::SIMDJSON_PADDING);
    Result r = p.apply(b.data(), s.size(), b.size(), epoch, epoch.batches + 1, budget);
    std::fill(b.begin(), b.end(), uint8_t(0xcd));
    return r;
}

// The failure reason, or empty when fn completes.
template <class F> std::string error_of(F fn)
{
    try
    {
        fn();
    }
    catch (const std::exception &e)
    {
        return e.what();
    }
    return {};
}
} // namespace

int run_selftest(const std::string &corpus_path)
{
    uint64_t checks = 0;
    auto check = [&](bool ok, std::string_view what) {
        ++checks;
        require(ok, "selftest: " + std::string(what));
    };
    const std::string good = R"([{"id":18446744073709551615,"timestamp_ns":0,"source":4294967295,)"
                             R"("kind":"kind15","value_milli":-0,"flags":3,)"
                             R"("message":"a\u0000\uD83D\uDE00"}])";
    const std::string object = good.substr(1, good.size() - 2);
    Row row;
    row.id = UINT64_MAX;
    row.source = UINT32_MAX;
    row.kind = 15;
    row.flags = 3;
    Result reference;
    add_row(reference, row, std::string_view("a\0\xf0\x9f\x98\x80", 6));
    check(reference.digest == 3660725836179230917ull, "cross-language golden digest");
    for (Mode mode : {Mode::aggregate, Mode::retain_reuse, Mode::retain_allocate})
    {
        Processor p(mode, 65536, 2, 67108864);
        Epoch epoch;
        auto run = [&](const std::string &s, const Budget &budget = {}) {
            return apply_text(p, epoch, s, budget);
        };
        check(run(good) == reference, "canonical digest");
        const uint64_t first = p.owned_capacity();
        check(run(good) == reference && run(good) == reference, "repeated batches");
        check(!retains(mode) || p.peak_owned >= first * 2, "scratch counted with retained data");
        std::atomic<bool> stopping = true;
        for (const Budget &budget : {Budget{&stopping}, Budget{nullptr, now() - 1}})
        {
            const auto before = epoch.encode();
            const uint64_t retained = p.retained;
            const std::string reason = budget.stop ? "shutdown" : "frame_timeout";
            check(error_of([&] {
                      run(good, budget);
                  }) == reason &&
                      error_of([&] {
                          p.verify(budget);
                      }) == reason &&
                      epoch.encode() == before && p.retained == retained,
                  "expired budget leaves state unchanged");
            p.verify();
        }
        std::vector<std::string> bad = {
            "", " ", "{}", "[] []", "[],", "[[]]", "[{}]", good + "x", "\xef\xbb\xbf" + good};
        bad.push_back("[" + object + "," + object + "," + object + ",{}]"); // grows scratch first
        for (auto [from, to] : std::vector<std::pair<std::string, std::string>>{
                 {"18446744073709551615", "18446744073709551616"},
                 {"18446744073709551615", "-0"},
                 {"18446744073709551615", "01"},
                 {"4294967295", "4294967296"},
                 {"\"value_milli\":-0", "\"value_milli\":1e0"},
                 {"\"value_milli\":-0", "\"value_milli\":1.0"},
                 {"\"value_milli\":-0", "\"value_milli\":1000001"},
                 {"\"value_milli\":-0", "\"value_milli\":-"},
                 {"\"value_milli\":-0", "\"value_milli\":\"1\""},
                 {"kind15", "kind16"},
                 {"kind15", "kind1"},
                 {"a\\u0000\\uD83D\\uDE00", "\\uD800"},
                 {"a\\u0000\\uD83D\\uDE00", "\\uDC00x"},
                 {"a\\u0000\\uD83D\\uDE00", "\xc0\xaf"},
                 {"a\\u0000\\uD83D\\uDE00", "\xed\xa0\x80"},
                 {"a\\u0000\\uD83D\\uDE00", "\xff  "},
                 {"\"source\":", "\"id\":"},
                 {"\"source\":", "\"\\u0069d\":"},
                 {"\"source\":", "\"unknown\":"}})
        {
            std::string s = good;
            s.replace(s.find(from), from.size(), to);
            bad.push_back(s);
        }
        for (const std::string &s : bad)
        {
            const auto before = epoch.encode();
            check(!error_of([&] {
                       run(s);
                   }).empty() &&
                      before == epoch.encode(),
                  "invalid batch rejected atomically");
            p.verify();
        }
        check(p.peak_owned >= p.owned_capacity(), "failed scratch counted");
        for (int i = 0; i < 16; ++i)
        {
            check(run(good) == reference, "reuse after eviction");
        }
        check(run("[]") == Result{}, "empty array");
        std::string escaped = good;
        escaped.replace(escaped.find("\"id\""), 4, "\"\\u0069d\"");
        check(run(escaped) == reference, "escaped property name");
        escaped = good;
        escaped.replace(escaped.find("\"timestamp_ns\""), 14,
                        R"("\u0074\u0069\u006d\u0065\u0073\u0074\u0061\u006d\u0070\u005f\u006e\u0073")");
        escaped.replace(escaped.find("\"kind15\""), 8, R"("\u006bind15")");
        check(run(escaped) == reference, "escaped longest name and kind");
        std::string spaced = good;
        spaced.replace(spaced.find("\"flags\":3"), 9, "\"flags\" : 3 ");
        check(run(" \r\n" + spaced + "\t") == reference, "insignificant whitespace");
        // A budget that expires mid-parse stops the parse (checked every 1024 records).
        std::string many = "[" + object;
        for (int i = 1; i < 100000; ++i)
        {
            many += "," + object;
        }
        many += "]";
        Processor large(mode, many.size(), 8, 1ull << 31);
        Epoch fresh;
        check(error_of([&] {
                  apply_text(large, fresh, many, Budget{nullptr, now() + to_ticks(0.002)});
              }) == "frame_timeout" &&
                  fresh.batches == 0 && large.retained == 0,
              "mid-parse budget expiry");
    }
    {
        // Limits apply to decoded text (escaped forms up to six times longer; see the managed selftest).
        Processor p(Mode::retain_reuse, 65536, 2, 67108864);
        Epoch epoch;
        const auto with = [&](const std::string &message) {
            std::string s = good;
            return s.replace(s.find(R"(a\u0000\uD83D\uDE00)"), 19, message);
        };
        const auto repeat = [](const char *part, size_t n) {
            std::string s;
            for (size_t i = 0; i < n; ++i)
                s += part;
            return s;
        };
        const std::string a4095(4095, 'A');
        for (const std::string &ok : {R"(\u0041)" + a4095, repeat(R"(\u0001)", 4096), "A" + a4095})
            check(error_of([&] {
                      apply_text(p, epoch, with(ok));
                  }).empty(),
                  "4,096 decoded message bytes accepted");
        for (const std::string &bad : {R"(\u0041A)" + a4095, repeat(R"(\u0001)", 4097), "AA" + a4095})
            check(error_of([&] {
                      apply_text(p, epoch, with(bad));
                  }) == "json_message_length",
                  "4,097 decoded message bytes rejected");
    }
    for (Mode mode : {Mode::retain_reuse, Mode::retain_allocate})
    {
        // Chunked storage: 3,000 records of 3,000-byte messages span 3 row chunks and 143 text chunks
        // (capacity cross-checked with the managed selftest); with one batch retained, the smaller batch lands in
        // reused multi-chunk storage. 16 or 32 full 4 KiB messages fill chunks exactly before an empty message.
        const auto records = [](int n, size_t length) {
            std::string s = "[";
            for (int i = 0; i < n; ++i)
                s += std::string(i ? "," : "") + R"({"id":)" + std::to_string(i) +
                     R"(,"timestamp_ns":0,"source":0,"kind":"kind01","value_milli":1,"flags":)" +
                     std::to_string(i) + R"(,"message":")" + std::string(length, char('a' + i % 26)) + "\"}";
            return s + "]";
        };
        const auto with_empty = [&](int full) {
            std::string s = records(full, 4096);
            s.pop_back();
            return s + R"(,{"id":0,"timestamp_ns":0,"source":0,"kind":"kind00","value_milli":0,"flags":0,"message":""}])";
        };
        Processor p(mode, 16 << 20, 1, 67108864);
        Epoch epoch;
        const std::vector<uint64_t> capacity = mode == Mode::retain_reuse ? std::vector<uint64_t>{9519104, 19038208, 19038208}
                                                                           : std::vector<uint64_t>{9519104, 9519104, 294912};
        size_t i = 0;
        for (const std::string &input :
             {records(3000, 3000), records(3000, 3000), records(1500, 100), with_empty(16), with_empty(32)})
        {
            Processor aggregate(Mode::aggregate, 16 << 20, 0, 0), fresh(mode, 16 << 20, 1, 67108864);
            Epoch scratch, fresh_epoch;
            const Result expected = apply_text(aggregate, scratch, input);
            check(apply_text(fresh, fresh_epoch, input) == expected, "fresh chunked batch digest");
            fresh.verify();
            check(apply_text(p, epoch, input) == expected, "chunked batch digest");
            p.verify();
            check(i >= capacity.size() || p.owned_capacity() == capacity[i], "chunked capacity");
            ++i;
        }
    }
    for (Mode mode : {Mode::retain_reuse, Mode::retain_allocate})
    {
        // A batch larger than the byte limit fails before commit; retained data survives.
        Processor p(mode, 65536, 8, 200);
        Epoch epoch;
        apply_text(p, epoch, good);
        std::string big = good;
        big.replace(big.find("a\\u0000"), 7, std::string(170, 'x'));
        check(error_of([&] {
                  apply_text(p, epoch, big);
              }) == "retention_capacity" &&
                  p.retained == 44 && epoch.batches == 1,
              "oversized batch is atomic");
        p.verify();
        // Corrupt the second planned eviction: the first must not be evicted either.
        Processor q(mode, 65536, 2, 120);
        Epoch e;
        apply_text(q, e, good);
        apply_text(q, e, good);
        q.live_batch(1).digest ^= 1;
        std::string both = good;
        both.replace(both.find("a\\u0000"), 7, std::string(40, 'y')); // requires both evictions
        check(error_of([&] {
                  apply_text(q, e, both);
              }) == "retained_integrity" &&
                  e.batches == 2 && q.retained == 88,
              "eviction verification precedes mutation");
        q.live_batch(1).digest ^= 1;
        q.verify();
    }
    for (uint64_t v : {0ull, 1023ull, 1024ull, 1027ull, 2048ull, 1000000ull, 59999999999ull})
    {
        check(Histogram::upper(Histogram::index(v)) >= v &&
                  (Histogram::index(v) == 0 || Histogram::upper(Histogram::index(v) - 1) < v),
              "histogram bucket bounds");
    }
    check(Histogram::upper(1023) == 1023 && Histogram::upper(1024) == 1027 &&
              Histogram::upper(1279) == 2047 && Histogram::upper(1280) == 2055,
          "histogram vectors");
    Histogram h;
    h.add(5);
    h.add(Histogram::overflow_ns);
    check(h.quantile(.5) == 5 && !h.quantile(1.0) && h.maximum == Histogram::overflow_ns,
          "overflow rank has no finite quantile");
    Histogram stage;
    auto gated = [&](std::string_view key) {
        Json j;
        stage.write(j, {}, true);
        return j.take().find("\"" + std::string(key) + "\":null") != std::string::npos;
    };
    for (uint64_t n = 1; n <= 1000000; ++n)
    {
        stage.add(1024);
        if (n == 9999 || n == 10000 || n == 999999 || n == 1000000)
        {
            check(gated("p99_ns") == (n < 10000) && gated("p999_ns") == (n < 1000000),
                  "stage percentile sample gates");
        }
    }
    client_selftest();
    ++checks;
    if (!corpus_path.empty())
    {
        const Corpus corpus = load_corpus(corpus_path);
        for (Mode mode : {Mode::aggregate, Mode::retain_reuse, Mode::retain_allocate})
        {
            Processor p(mode, corpus.max_frame, 8, 67108864);
            Epoch e;
            for (const Frame &f : corpus.frames)
            {
                check(p.apply(f.payload.data(), f.length, f.payload.size(), e, e.batches + 1) ==
                          f.expected,
                      "corpus oracle");
            }
            p.verify();
        }
    }
    std::cout << "{\"event\":\"selftest\",\"valid\":true,\"checks\":" << checks
              << ",\"canonical_digest\":" << reference.digest << "}" << std::endl;
    return 0;
}
} // namespace bench
