// Differential fuzzing for the fast parser, a port of src/dotnet/SelfTestFuzz.cs. fast_parse must
// accept and reject exactly what the simdjson path does, with identical results; utf8_valid must
// agree with an independent scalar validator and with simdjson's. Every input ends right before a
// no-access page, so any read past its end crashes the selftest (and ASan checks everything else).
#include "bench.hpp"

#include <cstdlib>
#include <cstring>
#include <random>

namespace bench
{
namespace
{
constexpr size_t guarded_bytes = 1 << 20;

uint8_t *guarded_region()
{
    static uint8_t *region = [] {
        auto *p = static_cast<uint8_t *>(VirtualAlloc(nullptr, guarded_bytes + 4096, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        DWORD old = 0;
        require(p && VirtualProtect(p + guarded_bytes, 4096, PAGE_NOACCESS, &old), "selftest: guard page");
        return p;
    }();
    return region;
}

const uint8_t *guarded(const std::string &input)
{
    uint8_t *at = guarded_region() + guarded_bytes - input.size();
    std::memcpy(at, input.data(), input.size());
    return at;
}

struct Rng
{
    std::mt19937_64 engine;
    int next(int n)
    {
        return int(engine() % uint64_t(n));
    }
    int next(int low, int high) // [low, high)
    {
        return low + next(high - low);
    }
    template <class T> const T &pick(const std::vector<T> &values)
    {
        return values[size_t(next(int(values.size())))];
    }
};

std::string hex4(int c, bool upper)
{
    char b[8];
    std::snprintf(b, sizeof b, upper ? "\\u%04X" : "\\u%04x", c);
    return b;
}

std::string name(const std::string &n, Rng &rng)
{
    if (rng.next(12) != 0)
    {
        return "\"" + n + "\"";
    }
    std::string s = "\"";
    for (char c : n)
    {
        const int r = rng.next(3);
        s += r == 2 ? std::string(1, c) : hex4(c, r == 1);
    }
    return s + "\"";
}

std::string digits(Rng &rng, int count)
{
    std::string s(1, char('1' + rng.next(9)));
    for (int i = 1; i < count; ++i)
    {
        s += char('0' + rng.next(10));
    }
    return s;
}

std::string message(Rng &rng, bool clean)
{
    if (!clean && rng.next(8) == 0)
    {
        static const std::vector<std::string> invalid = {
            "1", "null", "\"\\", "\"a\\x\"", "\"\\u12\"", "\"\\uD800\"", "\"\\uDC00x\"", "\"\\uD83D\\u0041\"",
            "\"\x01\"", "\"\\uD83D\\\\uDE00\"", "\"tab\there\""};
        return rng.pick(invalid);
    }
    static const std::vector<std::string> pieces = {
        "a", "Z", " ", "\\\"", "\\\\", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t", "\\u0041", "\\u00e9", "\\u20AC",
        "\\uFFFF", "\\u0000", "\\uD83D\\uDE00", "\\uDBFF\\uDFFF", "\xC3\xA9", "\xE2\x82\xAC", "\xF0\x9F\x98\x80",
        "\xC3\x9Cn\xC3\xAF", "\xE6\x97\xA5\xE6\x9C\xAC", "\x7F"};
    std::string s = "\"";
    const size_t length = rng.next(8) != 0 ? size_t(rng.next(0, 90)) : clean ? size_t(rng.next(600, 3000)) : size_t(rng.next(3900, 4400));
    while (s.size() < length)
    {
        s += rng.next(3) == 0 ? std::string(size_t(rng.next(1, 90)), 'x') : rng.pick(pieces);
    }
    return s + "\"";
}

std::string document(Rng &rng, bool clean)
{
    auto ws = [&] {
        switch (rng.next(4))
        {
        case 0:
            return std::string(" ");
        case 1:
            return std::string("\n\t");
        case 2:
            return std::string("\r\n  ");
        default:
            return std::string();
        }
    };
    auto pick = [&](const std::vector<std::string> &valid, const std::vector<std::string> &invalid) {
        return !clean && rng.next(8) == 0 ? rng.pick(invalid) : rng.pick(valid);
    };
    std::string s = "[" + ws();
    const int records = rng.next(8) == 0 ? rng.next(3, 12) : rng.next(0, 3);
    for (int record = records; record >= 0; --record)
    {
        std::vector<std::string> fields = {
            name("id", rng) + ":" + ws() +
                pick({"0", "7", "18446744073709551615", "12345678901234567", "10000000000000000000", "18446744073709551610",
                      digits(rng, rng.next(1, 20))},
                     {"18446744073709551616", "18446744073709551620", "99999999999999999999", "184467440737095516150", "007",
                      "-1", "1.0", "1e3", "\"1\"", "true", "-",
                      digits(rng, rng.next(1, 21)) + rng.pick(std::vector<std::string>{".5", "e1", "E+2", ".", "e"})}),
            name("timestamp_ns", rng) + ":" + pick({"1700000000000000000", "0", "18446744073709551615", "99999999"}, {"01", "1.5", "+1"}),
            name("source", rng) + ":" + pick({"4294967295", "0", "123", "10"}, {"4294967296", "-0", "00"}),
            name("kind", rng) + ":" +
                pick({"\"kind00\"", "\"kind15\"", "\"\\u006bind07\"", "\"kind09\"", "\"kin\\u006410\""},
                     {"\"kind16\"", "\"Kind01\"", "\"kind1\"", "5", "\"kind\"", "\"kind010\"", "\"kind05x", "\"kind05\x01\"",
                      "\"kind05\\\"\"", "\"kind05\\n\""}),
            name("value_milli", rng) + ":" +
                pick({"-0", "1000000", "-1000000", "0", "123", "-999999"}, {"1000001", "-1000001", "-", "--1", "-00", "01", "1e0"}),
            name("flags", rng) + ":" + pick({"0", "3", "4294967295", "12"}, {"4294967296", "-3", "3.0"}),
            name("message", rng) + ":" + ws() + message(rng, clean),
        };
        if (!clean && rng.next(15) == 0)
        {
            fields.erase(fields.begin() + rng.next(int(fields.size()))); // missing field
        }
        if (!clean && rng.next(15) == 0)
        {
            fields.push_back(fields[size_t(rng.next(int(fields.size())))]); // duplicate
        }
        if (!clean && rng.next(20) == 0)
        {
            // Unknown names, including same-length near misses of real ones.
            static const std::vector<std::string> unknown = {"extra", "i\\u0064x", "value_millx", "timestamp_nz", "messagf",
                                                             "flagz", "Id", "kinds", "sourcf", "kind "};
            fields.push_back("\"" + rng.pick(unknown) + "\":1");
        }
        std::shuffle(fields.begin(), fields.end(), rng.engine);
        s += "{" + ws();
        for (size_t i = 0; i < fields.size(); ++i)
        {
            s += (i ? ws() + "," + ws() : std::string()) + fields[i];
        }
        s += ws() + "}" + ws() + (record > 0 ? "," + ws() : std::string());
    }
    return s + "]" + ws();
}

// Byte-level damage: overwrite, insert, delete or truncate with bytes that matter to JSON and UTF-8.
std::string mutate(Rng &rng, std::string s, int count)
{
    static const std::string interesting = std::string("\"\\{}[],:0123456789-.eEu nrt\t\n\r") +
                                           std::string("\x00\x1F\x7F\x80\xBF\xC0\xC3\xE2\xED\xF0\xF4\xFF", 12);
    for (int i = 0; i < count && !s.empty(); ++i)
    {
        const size_t at = size_t(rng.next(int(s.size())));
        switch (rng.next(4))
        {
        case 0:
            s[at] = interesting[size_t(rng.next(int(interesting.size())))];
            break;
        case 1:
            s.insert(s.begin() + ptrdiff_t(at), interesting[size_t(rng.next(int(interesting.size())))]);
            break;
        case 2:
            s.erase(at, 1);
            break;
        default:
            s.erase(at);
            break;
        }
    }
    return s;
}

// Independent scalar UTF-8 validator (RFC 3629): the reference the SIMD port is compared with.
bool utf8_scalar(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n;)
    {
        const uint8_t c = p[i];
        size_t length;
        uint32_t cp;
        if (c < 0x80)
        {
            ++i;
            continue;
        }
        if (c >= 0xC2 && c <= 0xDF)
        {
            length = 2, cp = c & 0x1F;
        }
        else if (c >= 0xE0 && c <= 0xEF)
        {
            length = 3, cp = c & 0x0F;
        }
        else if (c >= 0xF0 && c <= 0xF4)
        {
            length = 4, cp = c & 0x07;
        }
        else
        {
            return false;
        }
        if (n - i < length)
        {
            return false;
        }
        for (size_t k = 1; k < length; ++k)
        {
            if ((p[i + k] & 0xC0) != 0x80)
            {
                return false;
            }
            cp = (cp << 6) | (p[i + k] & 0x3F);
        }
        if ((length == 3 && cp < 0x800) || (length == 4 && (cp < 0x10000 || cp > 0x10FFFF)) || (cp >= 0xD800 && cp <= 0xDFFF))
        {
            return false;
        }
        i += length;
    }
    return true;
}

std::string text(Rng &rng)
{
    static const std::vector<std::string> pieces = {
        "a", "0123456789abcdef", "\xC3\xA9", "\xE2\x82\xAC", "\xF0\x9F\x98\x80", "\xF4\x8F\xBF\xBF", "\xED\x9F\xBF",
        "\xEE\x80\x80", "\xDF\xBF", "\xE0\xA0\x80",
        // invalid from here on
        "\xC3", "\xE2\x82", "\xF0\x9F\x98", "\x80", "\xBF", "\xC0\x80", "\xC1\xBF", "\xE0\x80\x80", "\xED\xA0\x80",
        "\xED\xBF\xBF", "\xF0\x80\x80\x80", "\xF4\x90\x80\x80", "\xF5\x80\x80\x80", "\xFF"};
    std::string s(size_t(rng.next(0, 70)), 'x');
    const bool mostly_valid = rng.next(3) != 0;
    for (int n = rng.next(1, 40); n > 0; --n)
    {
        s += pieces[size_t(rng.next(mostly_valid ? 10 : int(pieces.size())))];
    }
    if (mostly_valid && rng.next(4) == 0 && !s.empty())
    {
        s[size_t(rng.next(int(s.size())))] = pieces[size_t(10 + rng.next(int(pieces.size()) - 10))][0]; // one damaged byte
    }
    return s;
}

bool utf8_agree(const std::string &s)
{
    const uint8_t *g = guarded(s);
    const bool want = utf8_scalar(reinterpret_cast<const uint8_t *>(s.data()), s.size());
    return utf8_valid(g, s.size()) == want && simdjson::validate_utf8(s.data(), s.size()) == want;
}

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

// BENCH_FUZZ=<seed>,<scale> runs a longer soak (scale multiplies both iteration counts).
uint64_t fuzz_selftest()
{
    uint64_t checks = 0, seed = 20260930, documents = 60000, texts = 60000;
    if (const char *soak = std::getenv("BENCH_FUZZ"))
    {
        char *rest = nullptr;
        seed = std::strtoull(soak, &rest, 10);
        const uint64_t scale = rest && *rest == ',' ? std::strtoull(rest + 1, nullptr, 10) : 1;
        documents *= scale;
        texts *= scale;
    }
    Rng rng{std::mt19937_64(seed)};
    uint64_t accepted = 0;
    for (Mode mode : {Mode::aggregate, Mode::retain_reuse})
    {
        Processor reference(mode, 1 << 20, 4, 1 << 26), candidate(mode, 1 << 20, 4, 1 << 26, true);
        Epoch reference_epoch, candidate_epoch;
        // Canaries: the fast parser may write [0, 4128) and [8192, 8240) of its scratch, nothing else.
        std::vector<uint8_t> &scratch = candidate.scratch_for_tests();
        std::fill(scratch.begin() + 4128, scratch.begin() + 8192, uint8_t(0xA5));
        std::fill(scratch.begin() + 8240, scratch.end(), uint8_t(0xA5));
        for (uint64_t i = 0; i < documents / 2; ++i)
        {
            const bool clean = rng.next(2) == 0;
            const std::string input = clean ? document(rng, true) : mutate(rng, document(rng, false), rng.next(3));
            Result want, have;
            std::vector<uint8_t> padded(input.begin(), input.end());
            padded.resize(input.size() + simdjson::SIMDJSON_PADDING);
            const std::string expected = error_of([&] {
                want = reference.apply(padded.data(), input.size(), padded.size(), reference_epoch, reference_epoch.batches + 1);
            });
            const std::string got = error_of([&] {
                have = candidate.apply(guarded(input), input.size(), input.size(), candidate_epoch, candidate_epoch.batches + 1);
            });
            ++checks;
            accepted += expected.empty();
            require(expected.empty() == got.empty() && (!expected.empty() || want == have),
                    "selftest: parsers disagree (" + (expected.empty() ? std::string("ok") : expected) + " vs " +
                        (got.empty() ? std::string("ok") : got) + "): " + input.substr(0, 300));
            require(std::all_of(scratch.begin() + 4128, scratch.begin() + 8192, [](uint8_t b) { return b == 0xA5; }) &&
                        std::all_of(scratch.begin() + 8240, scratch.end(), [](uint8_t b) { return b == 0xA5; }),
                    "selftest: fast parser wrote outside its scratch regions");
        }
        reference.verify();
        candidate.verify();
    }
    require(accepted >= documents / 5 && accepted <= documents * 4 / 5, "selftest: fuzz mix too one-sided");

    // Every piece at every offset, followed by ASCII runs that end at, before and after block edges.
    for (const char *piece : {"\xC3", "\xE2\x82", "\xF0\x9F\x98", "\x80", "\xED\xA0\x80", "\xC3\xA9", "\xE2\x82\xAC",
                              "\xF0\x9F\x98\x80", "\xF4\x90\x80\x80", "\xC0\x80"})
    {
        for (size_t offset = 0; offset <= 130; ++offset)
        {
            for (size_t after : {0, 1, 31, 32, 33, 63, 64, 65, 128})
            {
                ++checks;
                const std::string s = std::string(offset, 'x') + piece + std::string(after, 'x');
                require(utf8_agree(s), "selftest: UTF-8 validators disagree (sweep)");
            }
        }
    }
    uint64_t valid_texts = 0;
    for (uint64_t i = 0; i < texts; ++i)
    {
        const std::string s = text(rng);
        ++checks;
        valid_texts += utf8_scalar(reinterpret_cast<const uint8_t *>(s.data()), s.size());
        require(utf8_agree(s), "selftest: UTF-8 validators disagree");
    }
    require(valid_texts >= texts / 5 && valid_texts <= texts * 19 / 20, "selftest: UTF-8 fuzz mix too one-sided");
    return checks;
}
} // namespace bench
