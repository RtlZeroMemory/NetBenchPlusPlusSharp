// Schema-specific strict parser (--parser fast): a line-by-line port of src/dotnet/FastJson.cs and
// Utf8Check.cs, so both languages run the same algorithm. It accepts and rejects exactly what the
// simdjson path accepts and rejects (the selftest fuzzes one against the other) and produces the
// same rows. The 64-byte scan uses AVX-512 only when the CPU has it, like the managed Vector512 path;
// everything else uses AVX2, so --parser fast needs an AVX2 CPU (checked at startup).
#include "bench.hpp"

#include <cstring>
#include <immintrin.h>
#include <intrin.h>

namespace bench
{
namespace
{
// Scratch layout: decoded message at 0 (up to 4,096 bytes; 32-byte speculative stores reach 4,127),
// decoded keys and kinds at 8,192 (up to 16 bytes; stores reach 8,239).
constexpr size_t key_scratch = 8192, key_limit = 16;

bool detect_avx512()
{
    int r[4];
    __cpuid(r, 1);
    if (!((r[2] >> 27) & 1)) // OSXSAVE
    {
        return false;
    }
    __cpuidex(r, 7, 0);
    const bool f = (r[1] >> 16) & 1, bw = (r[1] >> 30) & 1;
    return f && bw && (_xgetbv(0) & 0xE6) == 0xE6; // XMM, YMM, opmask and ZMM state enabled
}
const bool avx512 = detect_avx512();
} // namespace

const char *fast_kernel()
{
    return avx512 ? "avx512" : "avx2";
}

bool fast_supported()
{
    int r[4];
    __cpuid(r, 1);
    if (!((r[2] >> 27) & 1)) // OSXSAVE
    {
        return false;
    }
    __cpuidex(r, 7, 0);
    return ((r[1] >> 5) & 1) && (_xgetbv(0) & 0x6) == 0x6; // AVX2 with YMM state enabled
}

namespace
{

template <class T> T load(const uint8_t *p)
{
    T v;
    std::memcpy(&v, p, sizeof v);
    return v;
}

constexpr uint64_t little(const char *s, int n)
{
    uint64_t v = 0;
    for (int i = n - 1; i >= 0; --i)
    {
        v = (v << 8) | uint8_t(s[i]);
    }
    return v;
}
constexpr uint64_t timestam = little("timestam", 8), value_mi = little("value_mi", 8),
                   ue_milli = little("ue_milli", 8);
constexpr uint32_t p_ns = uint32_t(little("p_ns", 4)), sour = uint32_t(little("sour", 4)),
                   kind4 = uint32_t(little("kind", 4)), flag = uint32_t(little("flag", 4)),
                   mess = uint32_t(little("mess", 4)), sage = uint32_t(little("sage", 4));
constexpr uint16_t id2 = uint16_t(little("id", 2)), ce = uint16_t(little("ce", 2));

__forceinline const uint8_t *ws(const uint8_t *p, const uint8_t *end)
{
    while (p < end && *p <= ' ' && (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t'))
    {
        ++p;
    }
    return p;
}

// Offset of the first '"', '\\' or control byte at or after s, or end - s if there is none.
__forceinline size_t special(const uint8_t *s, const uint8_t *end)
{
    const uint8_t *q = s;
    if (avx512)
    {
        const __m512i quote = _mm512_set1_epi8('"'), backslash = _mm512_set1_epi8('\\'),
                      control = _mm512_set1_epi8(0x20);
        while (end - q >= 64)
        {
            const __m512i v = _mm512_loadu_si512(q);
            const uint64_t m = _mm512_cmpeq_epi8_mask(v, quote) | _mm512_cmpeq_epi8_mask(v, backslash) |
                               _mm512_cmplt_epu8_mask(v, control);
            if (m)
            {
                return size_t(q - s) + _tzcnt_u64(m);
            }
            q += 64;
        }
    }
    const __m256i quote = _mm256_set1_epi8('"'), backslash = _mm256_set1_epi8('\\'),
                  low = _mm256_set1_epi8(0x1F);
    while (end - q >= 32)
    {
        const __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(q));
        const __m256i hit =
            _mm256_or_si256(_mm256_or_si256(_mm256_cmpeq_epi8(v, quote), _mm256_cmpeq_epi8(v, backslash)),
                            _mm256_cmpeq_epi8(_mm256_min_epu8(v, low), v)); // v <= 0x1F
        if (const uint32_t m = uint32_t(_mm256_movemask_epi8(hit)))
        {
            return size_t(q - s) + _tzcnt_u32(m);
        }
        q += 32;
    }
    while (q < end && *q != '"' && *q != '\\' && *q >= 0x20)
    {
        ++q;
    }
    return size_t(q - s);
}

// Matches a decoded key of length n at s (at least 16 readable bytes) to its field bit, or 0.
__forceinline unsigned match(const uint8_t *s, size_t n)
{
    switch (n)
    {
    case 2:
        return load<uint16_t>(s) == id2 ? 1 : 0;
    case 12:
        return load<uint64_t>(s) == timestam && load<uint32_t>(s + 8) == p_ns ? 2 : 0;
    case 6:
        return load<uint32_t>(s) == sour && load<uint16_t>(s + 4) == ce ? 4 : 0;
    case 4:
        return load<uint32_t>(s) == kind4 ? 8 : 0;
    case 11:
        return load<uint64_t>(s) == value_mi && load<uint64_t>(s + 3) == ue_milli ? 16 : 0;
    case 5:
        return load<uint32_t>(s) == flag && s[4] == 's' ? 32 : 0;
    case 7:
        return load<uint32_t>(s) == mess && load<uint32_t>(s + 3) == sage ? 64 : 0;
    default:
        return 0;
    }
}

constexpr auto hex_table = [] {
    std::array<uint8_t, 256> t{};
    for (int c = 0; c < 256; ++c)
    {
        t[c] = c >= '0' && c <= '9'   ? uint8_t(c - '0')
               : c >= 'a' && c <= 'f' ? uint8_t(c - 'a' + 10)
               : c >= 'A' && c <= 'F' ? uint8_t(c - 'A' + 10)
                                      : uint8_t(0xFF);
    }
    return t;
}();

// Four hex digits, or -1. Invalid characters map to 0xFF, whose high bit survives the OR.
__forceinline int hex4(const uint8_t *s)
{
    const int a = hex_table[s[0]], b = hex_table[s[1]], c = hex_table[s[2]], d = hex_table[s[3]];
    return ((a | b | c | d) & 0x80) ? -1 : (a << 12) | (b << 8) | (c << 4) | d;
}

// Decodes the string starting at the quote p into destination (at most limit bytes; the destination
// has 32 bytes of slack), checking the JSON string grammar and surrogate pairing. Plain runs are
// copied 32 bytes at a time before the special byte is located, as simdjson does. The first plain
// bytes (already scanned by the caller, all before the first special byte) are copied as they are.
__declspec(noinline) const uint8_t *decode(const uint8_t *p, const uint8_t *end, uint8_t *destination,
                                           size_t limit, std::string_view too_long, size_t &written,
                                           size_t plain = 0)
{
    require(plain <= limit, too_long);
    std::memcpy(destination, p + 1, plain);
    const uint8_t *s = p + 1 + plain;
    size_t w = plain;
    const __m256i quote = _mm256_set1_epi8('"'), backslash = _mm256_set1_epi8('\\'), low = _mm256_set1_epi8(0x1F);
    while (true)
    {
        if (end - s >= 32)
        {
            const __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
            _mm256_storeu_si256(reinterpret_cast<__m256i *>(destination + w), v); // speculative
            const uint32_t m = uint32_t(_mm256_movemask_epi8(
                _mm256_or_si256(_mm256_or_si256(_mm256_cmpeq_epi8(v, quote), _mm256_cmpeq_epi8(v, backslash)),
                                _mm256_cmpeq_epi8(_mm256_min_epu8(v, low), v))));
            const size_t n = m == 0 ? 32 : _tzcnt_u32(m);
            s += n;
            w += n;
            require(w <= limit, too_long);
            if (m == 0)
            {
                continue;
            }
        }
        else
        {
            while (s < end && *s != '"' && *s != '\\' && *s >= 0x20)
            {
                require(w < limit, too_long);
                destination[w++] = *s++;
            }
            require(s < end, "json_syntax"); // unterminated
        }
        if (*s == '"')
        {
            written = w;
            return s + 1;
        }
        require(*s == '\\' && end - s >= 2, "json_syntax"); // control byte, or a backslash at the end
        int code;
        switch (s[1])
        {
        case '"':
        case '\\':
        case '/':
            code = s[1];
            break;
        case 'b':
            code = 8;
            break;
        case 'f':
            code = 12;
            break;
        case 'n':
            code = 10;
            break;
        case 'r':
            code = 13;
            break;
        case 't':
            code = 9;
            break;
        case 'u': {
            code = end - s >= 6 ? hex4(s + 2) : -1;
            require(code >= 0, "json_syntax");
            if (code >= 0xD800 && code <= 0xDBFF)
            {
                const bool escape = end - s >= 12 && s[6] == '\\' && s[7] == 'u';
                const int low_half = escape ? hex4(s + 8) : -1;
                if (low_half < 0xDC00 || low_half > 0xDFFF)
                {
                    fail(escape && low_half < 0 ? "json_syntax" : "json_string");
                }
                code = 0x10000 + ((code - 0xD800) << 10) + (low_half - 0xDC00);
                s += 6;
            }
            else
            {
                require(code < 0xDC00 || code > 0xDFFF, "json_string");
            }
            s += 4;
            break;
        }
        default:
            fail("json_syntax");
        }
        s += 2;
        const size_t length = code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
        require(length <= limit - w, too_long);
        uint8_t *d = destination + w;
        switch (length)
        {
        case 1:
            d[0] = uint8_t(code);
            break;
        case 2:
            d[0] = uint8_t(0xC0 | (code >> 6));
            d[1] = uint8_t(0x80 | (code & 0x3F));
            break;
        case 3:
            d[0] = uint8_t(0xE0 | (code >> 12));
            d[1] = uint8_t(0x80 | ((code >> 6) & 0x3F));
            d[2] = uint8_t(0x80 | (code & 0x3F));
            break;
        default:
            d[0] = uint8_t(0xF0 | (code >> 18));
            d[1] = uint8_t(0x80 | ((code >> 12) & 0x3F));
            d[2] = uint8_t(0x80 | ((code >> 6) & 0x3F));
            d[3] = uint8_t(0x80 | (code & 0x3F));
            break;
        }
        w += length;
    }
}

__forceinline const uint8_t *key(const uint8_t *p, const uint8_t *end, uint8_t *scratch, unsigned &bit)
{
    require(p < end && *p == '"', "json_syntax");
    const uint8_t *s = p + 1;
    if (end - s >= 16)
    {
        const size_t n = special(s, s + 16);
        if (n < 16 && s[n] == '"')
        {
            bit = match(s, n);
            return s + n + 1;
        }
    }
    // Escaped, long or near the end of the frame: decode, then compare the decoded name.
    size_t length = 0;
    p = decode(p, end, scratch, key_limit, "json_fields", length);
    bit = match(scratch, length);
    return p;
}

inline int kind_index(uint8_t tens, uint8_t ones)
{
    if (tens == '0' && unsigned(ones - '0') <= 9)
    {
        return ones - '0';
    }
    if (tens == '1' && unsigned(ones - '0') <= 5)
    {
        return 10 + ones - '0';
    }
    return -1;
}

const uint8_t *kind(const uint8_t *p, const uint8_t *end, uint8_t *scratch, uint8_t &value)
{
    if (end - p >= 8 && *p == '"' && load<uint32_t>(p + 1) == kind4 && p[7] == '"')
    {
        if (const int k = kind_index(p[5], p[6]); k >= 0)
        {
            value = uint8_t(k);
            return p + 8;
        }
    }
    require(p < end && *p == '"', "json_kind");
    size_t length = 0;
    p = decode(p, end, scratch, key_limit, "json_kind", length);
    const int index = length == 6 && load<uint32_t>(scratch) == kind4 ? kind_index(scratch[4], scratch[5]) : -1;
    require(index >= 0, "json_kind");
    value = uint8_t(index);
    return p;
}

__forceinline const uint8_t *message(const uint8_t *p, const uint8_t *end, uint8_t *scratch, std::string_view &text)
{
    require(p < end && *p == '"', "json_message");
    const uint8_t *s = p + 1;
    const size_t n = special(s, end);
    if (n < size_t(end - s) && s[n] == '"')
    {
        require(n <= 4096, "json_message_length");
        text = std::string_view(reinterpret_cast<const char *>(s), n);
        return s + n + 1;
    }
    size_t length = 0;
    p = decode(p, end, scratch, 4096, "json_message_length", length, n);
    text = std::string_view(reinterpret_cast<const char *>(scratch), length);
    return p;
}

__forceinline bool eight_digits(uint64_t chunk)
{
    return ((chunk & 0xF0F0F0F0F0F0F0F0ull) | (((chunk + 0x0606060606060606ull) & 0xF0F0F0F0F0F0F0F0ull) >> 4)) ==
           0x3333333333333333ull;
}

// Eight ASCII digits, first digit in the lowest byte (Lemire, "Quickly parsing eight digits").
__forceinline uint64_t parse_eight(uint64_t chunk)
{
    chunk -= 0x3030303030303030ull;
    chunk = chunk * 10 + (chunk >> 8);
    return ((chunk & 0x000000FF000000FFull) * (100 + (1000000ull << 32)) +
            ((chunk >> 16) & 0x000000FF000000FFull) * (1 + (10000ull << 32))) >>
           32;
}

inline bool fraction_or_exponent(const uint8_t *s, const uint8_t *end)
{
    return s < end && (*s == '.' || (*s | 0x20) == 'e');
}

// JSON integer lexeme, no sign: at most 20 digits, no leading zero, value <= max.
__forceinline const uint8_t *unsigned_value(const uint8_t *p, const uint8_t *end, uint64_t max, uint64_t &value)
{
    const uint8_t *s = p;
    uint64_t v = 0;
    while (end - s >= 8 && s - p <= 11)
    {
        const uint64_t chunk = load<uint64_t>(s);
        if (!eight_digits(chunk))
        {
            break;
        }
        v = v * 100000000 + parse_eight(chunk);
        s += 8;
    }
    while (s < end && unsigned(*s - '0') <= 9 && s - p < 19)
    {
        v = v * 10 + unsigned(*s - '0');
        ++s;
    }
    if (s < end && unsigned(*s - '0') <= 9)
    {
        // A 20th digit: the only length that can overflow; a 21st always does.
        const unsigned d = unsigned(*s - '0');
        require(!(v > 1844674407370955161ull || (v == 1844674407370955161ull && d > 5) ||
                  (s + 1 < end && unsigned(s[1] - '0') <= 9)),
                "json_unsigned");
        v = v * 10 + d;
        ++s;
    }
    if (s == p || (*p == '0' && s - p > 1) || v > max || fraction_or_exponent(s, end))
    {
        fail(s == p || v > max || fraction_or_exponent(s, end) ? "json_unsigned" : "json_syntax");
    }
    value = v;
    return s;
}

__forceinline const uint8_t *signed_value(const uint8_t *p, const uint8_t *end, int64_t &value)
{
    const bool negative = p < end && *p == '-';
    const uint8_t *s = negative ? p + 1 : p, *digits = s;
    uint64_t v = 0;
    while (s < end && unsigned(*s - '0') <= 9 && s - digits < 8)
    {
        v = v * 10 + unsigned(*s - '0');
        ++s;
    }
    const bool more = s < end && unsigned(*s - '0') <= 9;
    if (s == digits || (*digits == '0' && s - digits > 1) || v > 1000000 || more || fraction_or_exponent(s, end))
    {
        fail(s == digits || v > 1000000 || more || fraction_or_exponent(s, end) ? "json_value_range" : "json_syntax");
    }
    value = negative ? -int64_t(v) : int64_t(v);
    return s;
}
} // namespace

// UTF-8 validation with the lookup algorithm of Keiser and Lemire, as in simdjson; 64-byte ASCII
// blocks are skipped. A port of Utf8Check.cs.
bool utf8_valid(const uint8_t *p, size_t length)
{
    constexpr uint8_t too_short = 1, too_long = 2, overlong3 = 4, too_large = 8, surrogate = 16, overlong2 = 32,
                      too_large1000 = 64, overlong4 = 64, two_conts = 128,
                      carry = too_short | too_long | two_conts;
    constexpr uint8_t lo = carry | too_large | too_large1000;
    constexpr uint8_t c8 = too_long | overlong2 | two_conts | overlong3 | too_large1000 | overlong4;
    constexpr uint8_t c9 = too_long | overlong2 | two_conts | overlong3 | too_large;
    constexpr uint8_t cab = too_long | overlong2 | two_conts | surrogate | too_large;
    // Each 16-entry table appears twice: vpshufb looks up within each 128-bit lane.
    alignas(32) static constexpr uint8_t first_high_table[32] = {
        too_long, too_long, too_long, too_long, too_long, too_long, too_long, too_long, two_conts, two_conts,
        two_conts, two_conts, too_short | overlong2, too_short, too_short | overlong3 | surrogate,
        too_short | too_large | too_large1000 | overlong4, too_long, too_long, too_long, too_long, too_long,
        too_long, too_long, too_long, two_conts, two_conts, two_conts, two_conts, too_short | overlong2,
        too_short, too_short | overlong3 | surrogate, too_short | too_large | too_large1000 | overlong4};
    alignas(32) static constexpr uint8_t first_low_table[32] = {
        carry | overlong3 | overlong2 | overlong4, carry | overlong2, carry, carry, carry | too_large, lo, lo, lo,
        lo, lo, lo, lo, lo, lo | surrogate, lo, lo,
        carry | overlong3 | overlong2 | overlong4, carry | overlong2, carry, carry, carry | too_large, lo, lo, lo,
        lo, lo, lo, lo, lo, lo | surrogate, lo, lo};
    alignas(32) static constexpr uint8_t second_high_table[32] = {
        too_short, too_short, too_short, too_short, too_short, too_short, too_short, too_short, c8, c9, cab, cab,
        too_short, too_short, too_short, too_short, too_short, too_short, too_short, too_short, too_short,
        too_short, too_short, too_short, c8, c9, cab, cab, too_short, too_short, too_short, too_short};
    // A block ends mid-sequence if its last byte is a lead, or the 2nd/3rd-last lead 3/4-byte sequences.
    alignas(32) static constexpr uint8_t incomplete_table[32] = {
        255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
        255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0xEF, 0xDF, 0xBF};
    const auto table = [](const uint8_t *t) { return _mm256_load_si256(reinterpret_cast<const __m256i *>(t)); };
    const __m256i first_high = table(first_high_table), first_low = table(first_low_table),
                  second_high = table(second_high_table), incomplete_limit = table(incomplete_table);
    const __m256i nibble = _mm256_set1_epi8(0x0F);
    const auto block = [&](__m256i input, __m256i previous) {
        const __m256i carried = _mm256_permute2x128_si256(previous, input, 0x21);
        const __m256i prev1 = _mm256_alignr_epi8(input, carried, 15);
        const __m256i prev2 = _mm256_alignr_epi8(input, carried, 14);
        const __m256i prev3 = _mm256_alignr_epi8(input, carried, 13);
        const __m256i special = _mm256_and_si256(
            _mm256_and_si256(
                _mm256_shuffle_epi8(first_high, _mm256_and_si256(_mm256_srli_epi16(prev1, 4), nibble)),
                _mm256_shuffle_epi8(first_low, _mm256_and_si256(prev1, nibble))),
            _mm256_shuffle_epi8(second_high, _mm256_and_si256(_mm256_srli_epi16(input, 4), nibble)));
        const __m256i must_continue = _mm256_or_si256(_mm256_subs_epu8(prev2, _mm256_set1_epi8(0xE0 - 0x80)),
                                                      _mm256_subs_epu8(prev3, _mm256_set1_epi8(0xF0 - 0x80)));
        return _mm256_xor_si256(_mm256_and_si256(must_continue, _mm256_set1_epi8(-128)), special);
    };

    __m256i previous = _mm256_setzero_si256(), error = _mm256_setzero_si256(), incomplete = _mm256_setzero_si256();
    size_t i = 0;
    for (; i + 64 <= length; i += 64)
    {
        const __m256i a = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(p + i));
        const __m256i b = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(p + i + 32));
        if (_mm256_movemask_epi8(_mm256_or_si256(a, b)) == 0)
        {
            error = _mm256_or_si256(error, incomplete);
            incomplete = _mm256_setzero_si256();
            previous = b;
            continue;
        }
        error = _mm256_or_si256(error, block(a, previous));
        error = _mm256_or_si256(error, block(b, a));
        incomplete = _mm256_subs_epu8(b, incomplete_limit);
        previous = b;
    }
    alignas(32) uint8_t tail[32];
    for (; i < length; i += 32)
    {
        __m256i a;
        if (length - i >= 32)
        {
            a = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(p + i));
        }
        else
        {
            std::memset(tail, 0, sizeof tail); // zero padding is ASCII
            std::memcpy(tail, p + i, length - i);
            a = _mm256_load_si256(reinterpret_cast<const __m256i *>(tail));
        }
        error = _mm256_or_si256(error, block(a, previous));
        incomplete = _mm256_subs_epu8(a, incomplete_limit);
        previous = a;
    }
    error = _mm256_or_si256(error, incomplete);
    return _mm256_testz_si256(error, error) != 0;
}

Result fast_parse(const uint8_t *start, size_t length, Batch *batch, const Budget &budget, uint8_t *scratch,
                  uint64_t retain_bytes)
{
    Result r;
    require(length > 0, "json_syntax");
    require(utf8_valid(start, length), "json_string");
    const uint8_t *end = start + length, *p = ws(start, end);
    require(p < end && *p == '[', "json_root");
    p = ws(p + 1, end);
    if (p < end && *p == ']')
    {
        ++p;
    }
    else
    {
        uint64_t parsed = 0;
        while (true)
        {
            if ((parsed++ & 1023) == 0)
            {
                budget.check();
            }
            if (p == end || *p != '{')
            {
                fail(p == end ? "json_syntax" : "json_record");
            }
            p = ws(p + 1, end);
            Row row;
            std::string_view text;
            unsigned seen = 0;
            while (true)
            {
                unsigned bit = 0;
                p = key(p, end, scratch + key_scratch, bit);
                require(bit && !(seen & bit), "json_fields");
                seen |= bit;
                p = ws(p, end);
                require(p < end && *p == ':', "json_syntax");
                p = ws(p + 1, end);
                switch (bit)
                {
                case 1:
                    p = unsigned_value(p, end, UINT64_MAX, row.id);
                    break;
                case 2:
                    p = unsigned_value(p, end, UINT64_MAX, row.timestamp);
                    break;
                case 4: {
                    uint64_t source = 0;
                    p = unsigned_value(p, end, UINT32_MAX, source);
                    row.source = uint32_t(source);
                    break;
                }
                case 8:
                    p = kind(p, end, scratch + key_scratch, row.kind);
                    break;
                case 16:
                    p = signed_value(p, end, row.value);
                    break;
                case 32: {
                    uint64_t flags = 0;
                    p = unsigned_value(p, end, UINT32_MAX, flags);
                    row.flags = uint32_t(flags);
                    break;
                }
                default:
                    p = message(p, end, scratch, text);
                    break;
                }
                p = ws(p, end);
                if (p < end && *p == ',')
                {
                    p = ws(p + 1, end);
                    continue;
                }
                require(p < end && *p == '}', "json_syntax");
                ++p;
                break;
            }
            require(seen == 127, "json_fields");
            if (batch)
            {
                append(*batch, row, text, retain_bytes);
            }
            else
            {
                add_row(r, row, text);
            }
            p = ws(p, end);
            if (p < end && *p == ',')
            {
                p = ws(p + 1, end);
                continue;
            }
            require(p < end && *p == ']', "json_syntax");
            ++p;
            break;
        }
    }
    require(ws(p, end) == end, "json_trailing");
    return r;
}
} // namespace bench
