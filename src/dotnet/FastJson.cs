using System.Diagnostics.CodeAnalysis;
using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Runtime.Intrinsics;
using System.Text.Unicode;

namespace TcpBench;

// Schema-specific strict parser (--parser fast). It accepts and rejects exactly what the Utf8JsonReader
// path accepts and rejects (the selftest fuzzes one against the other) and produces the same rows:
// - UTF-8 is validated once per frame; outside strings only ASCII can be legal anyway, and a valid
//   frame splits into valid runs at every ASCII quote or backslash, so decoded strings stay valid.
// - Strings are scanned 64 (or 32/16) bytes at a time for '"', '\\' and control bytes.
// - The seven keys are matched with one or two integer compares; integers are parsed in place.
// Only acceptance and results are guaranteed identical; failure reasons mostly match the other path
// (json_syntax for grammar errors), but a few invalid inputs report a different reason.
static unsafe class FastJson
{
    // Scratch layout: decoded message at 0 (up to 4,096 bytes; 32-byte speculative stores reach 4,127),
    // decoded keys and kinds at 8,192 (up to 16 bytes; stores reach 8,239).
    const int KeyScratch = 8192, KeyLimit = 16, ScratchBytes = KeyScratch + KeyLimit + 32;
    static readonly ulong Timestam = Read8("timestam"u8), ValueMi = Read8("value_mi"u8), UeMilli = Read8("ue_milli"u8);
    static readonly uint PNs = Read4("p_ns"u8), Sour = Read4("sour"u8), Kind4 = Read4("kind"u8), Flag = Read4("flag"u8),
        Mess = Read4("mess"u8), Sage = Read4("sage"u8);
    static readonly ushort Id2 = Read2("id"u8), Ce = Read2("ce"u8);

    static ulong Read8(ReadOnlySpan<byte> s) => MemoryMarshal.Read<ulong>(s);
    static uint Read4(ReadOnlySpan<byte> s) => MemoryMarshal.Read<uint>(s);
    static ushort Read2(ReadOnlySpan<byte> s) => MemoryMarshal.Read<ushort>(s);

    public static void Parse(ReadOnlySpan<byte> input, Batch? batch, Result r, Budget budget, byte[] scratch, long retainBytes)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(scratch.Length, ScratchBytes);
        r.Clear();
        if (input.IsEmpty)
        {
            Fail("json_syntax");
        }

        if (!Utf8Check.IsValid(input))
        {
            Fail("json_string");
        }

        fixed (byte* start = input)
        fixed (byte* buffer = scratch)
        {
            byte* p = Ws(start, start + input.Length), end = start + input.Length;
            if (p == end || *p != '[')
            {
                Fail("json_root");
            }

            p = Ws(p + 1, end);
            if (p < end && *p == ']')
            {
                p++;
            }
            else
            {
                ulong parsed = 0;
                while (true)
                {
                    if ((parsed++ & 1023) == 0)
                    {
                        budget.Check();
                    }

                    if (p == end || *p != '{')
                    {
                        Fail(p == end ? "json_syntax" : "json_record");
                    }

                    p = Ws(p + 1, end);
                    Row row = default;
                    byte* message = null;
                    int messageLength = 0, seen = 0;
                    while (true)
                    {
                        int bit;
                        p = Key(p, end, buffer + KeyScratch, out bit);
                        if (bit == 0 || (seen & bit) != 0)
                        {
                            Fail("json_fields");
                        }

                        seen |= bit;
                        p = Ws(p, end);
                        if (p == end || *p != ':')
                        {
                            Fail("json_syntax");
                        }

                        p = Ws(p + 1, end);
                        switch (bit)
                        {
                            case 1:
                                p = Unsigned(p, end, ulong.MaxValue, out row.Id);
                                break;
                            case 2:
                                p = Unsigned(p, end, ulong.MaxValue, out row.Timestamp);
                                break;
                            case 4:
                                p = Unsigned(p, end, uint.MaxValue, out ulong source);
                                row.Source = (uint)source;
                                break;
                            case 8:
                                p = Kind(p, end, buffer + KeyScratch, out row.Kind);
                                break;
                            case 16:
                                p = Signed(p, end, out row.Value);
                                break;
                            case 32:
                                p = Unsigned(p, end, uint.MaxValue, out ulong flags);
                                row.Flags = (uint)flags;
                                break;
                            default:
                                p = Message(p, end, buffer, out message, out messageLength);
                                break;
                        }

                        p = Ws(p, end);
                        if (p < end && *p == ',')
                        {
                            p = Ws(p + 1, end);
                            continue;
                        }

                        if (p < end && *p == '}')
                        {
                            p++;
                            break;
                        }

                        Fail("json_syntax");
                    }

                    if (seen != 127)
                    {
                        Fail("json_fields");
                    }

                    var text = new ReadOnlySpan<byte>(message, messageLength);
                    if (batch != null)
                    {
                        batch.Add(row, text, retainBytes);
                    }
                    else
                    {
                        r.Add(row, text);
                    }

                    p = Ws(p, end);
                    if (p < end && *p == ',')
                    {
                        p = Ws(p + 1, end);
                        continue;
                    }

                    if (p < end && *p == ']')
                    {
                        p++;
                        break;
                    }

                    Fail("json_syntax");
                }
            }

            if (Ws(p, end) != end)
            {
                Fail("json_trailing");
            }
        }
    }

    [DoesNotReturn]
    [MethodImpl(MethodImplOptions.NoInlining)]
    static void Fail(string reason) => throw new BenchException(reason);

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static byte* Ws(byte* p, byte* end)
    {
        while (p < end && *p <= ' ' && (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t'))
        {
            p++;
        }

        return p;
    }

    // Offset of the first '"', '\\' or control byte at or after s, or end - s if there is none.
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static long Special(byte* s, byte* end)
    {
        byte* q = s;
        if (Vector512.IsHardwareAccelerated)
        {
            while (end - q >= 64)
            {
                Vector512<byte> v = Vector512.Load(q);
                ulong m = (Vector512.Equals(v, Vector512.Create((byte)'"')) | Vector512.Equals(v, Vector512.Create((byte)'\\')) |
                    Vector512.LessThan(v, Vector512.Create((byte)0x20))).ExtractMostSignificantBits();
                if (m != 0)
                {
                    return q - s + BitOperations.TrailingZeroCount(m);
                }

                q += 64;
            }
        }

        while (end - q >= 32)
        {
            Vector256<byte> v = Vector256.Load(q);
            uint m = (Vector256.Equals(v, Vector256.Create((byte)'"')) | Vector256.Equals(v, Vector256.Create((byte)'\\')) |
                Vector256.LessThan(v, Vector256.Create((byte)0x20))).ExtractMostSignificantBits();
            if (m != 0)
            {
                return q - s + BitOperations.TrailingZeroCount(m);
            }

            q += 32;
        }

        while (q < end && *q != '"' && *q != '\\' && *q >= 0x20)
        {
            q++;
        }

        return q - s;
    }

    // Matches a decoded key of length n at s (at least 16 readable bytes) to its field bit, or 0.
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Match(byte* s, long n) => n switch
    {
        2 when Unsafe.ReadUnaligned<ushort>(s) == Id2 => 1,
        12 when Unsafe.ReadUnaligned<ulong>(s) == Timestam && Unsafe.ReadUnaligned<uint>(s + 8) == PNs => 2,
        6 when Unsafe.ReadUnaligned<uint>(s) == Sour && Unsafe.ReadUnaligned<ushort>(s + 4) == Ce => 4,
        4 when Unsafe.ReadUnaligned<uint>(s) == Kind4 => 8,
        11 when Unsafe.ReadUnaligned<ulong>(s) == ValueMi && Unsafe.ReadUnaligned<ulong>(s + 3) == UeMilli => 16,
        5 when Unsafe.ReadUnaligned<uint>(s) == Flag && s[4] == 's' => 32,
        7 when Unsafe.ReadUnaligned<uint>(s) == Mess && Unsafe.ReadUnaligned<uint>(s + 3) == Sage => 64,
        _ => 0
    };

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static byte* Key(byte* p, byte* end, byte* scratch, out int bit)
    {
        if (p == end || *p != '"')
        {
            Fail("json_syntax");
        }

        byte* s = p + 1;
        if (end - s >= 16)
        {
            long n = Special(s, s + 16);
            if (n < 16 && s[n] == '"')
            {
                bit = Match(s, n);
                return s + n + 1;
            }
        }

        // Escaped, long or near the end of the frame: decode, then compare the decoded name.
        p = Decode(p, end, scratch, KeyLimit, "json_fields", out int length);
        bit = Match(scratch, length);
        return p;
    }

    static byte* Kind(byte* p, byte* end, byte* scratch, out byte kind)
    {
        if (end - p >= 8 && *p == '"' && Unsafe.ReadUnaligned<uint>(p + 1) == Kind4 && p[7] == '"')
        {
            int k = KindIndex(p[5], p[6]);
            if (k >= 0)
            {
                kind = (byte)k;
                return p + 8;
            }
        }

        if (p == end || *p != '"')
        {
            Fail("json_kind");
        }

        p = Decode(p, end, scratch, KeyLimit, "json_kind", out int length);
        int index = length == 6 && Unsafe.ReadUnaligned<uint>(scratch) == Kind4 ? KindIndex(scratch[4], scratch[5]) : -1;
        if (index < 0)
        {
            Fail("json_kind");
        }

        kind = (byte)index;
        return p;
    }

    static int KindIndex(byte tens, byte ones) => tens switch
    {
        (byte)'0' when (uint)(ones - '0') <= 9 => ones - '0',
        (byte)'1' when (uint)(ones - '0') <= 5 => 10 + ones - '0',
        _ => -1
    };

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static byte* Message(byte* p, byte* end, byte* scratch, out byte* text, out int length)
    {
        if (p == end || *p != '"')
        {
            Fail("json_message");
        }

        byte* s = p + 1;
        long n = Special(s, end);
        if (n < end - s && s[n] == '"')
        {
            if (n > 4096)
            {
                Fail("json_message_length");
            }

            text = s;
            length = (int)n;
            return s + n + 1;
        }

        text = scratch;
        return Decode(p, end, scratch, 4096, "json_message_length", out length, n);
    }

    // Decodes the string starting at the quote p into destination (at most limit bytes; the destination
    // has 32 bytes of slack), checking the JSON string grammar and surrogate pairing. Plain runs are
    // copied 32 bytes at a time before the special byte is located, as simdjson does. The first plain
    // bytes (already scanned by the caller, all before the first special byte) are copied as they are.
    [MethodImpl(MethodImplOptions.NoInlining)]
    static byte* Decode(byte* p, byte* end, byte* destination, int limit, string tooLong, out int written, long plain = 0)
    {
        if (plain > limit)
        {
            Fail(tooLong);
        }

        Buffer.MemoryCopy(p + 1, destination, plain, plain);
        byte* s = p + 1 + plain;
        int w = (int)plain;
        Vector256<byte> quote = Vector256.Create((byte)'"'), backslash = Vector256.Create((byte)'\\'), control = Vector256.Create((byte)0x20);
        while (true)
        {
            if (end - s >= 32)
            {
                Vector256<byte> v = Vector256.Load(s);
                v.Store(destination + w); // speculative; only the part before the special byte counts
                uint m = (Vector256.Equals(v, quote) | Vector256.Equals(v, backslash) | Vector256.LessThan(v, control)).ExtractMostSignificantBits();
                int n = m == 0 ? 32 : BitOperations.TrailingZeroCount(m);
                s += n;
                w += n;
                if (w > limit)
                {
                    Fail(tooLong);
                }

                if (m == 0)
                {
                    continue;
                }
            }
            else
            {
                while (s < end && *s != '"' && *s != '\\' && *s >= 0x20)
                {
                    if (w == limit)
                    {
                        Fail(tooLong);
                    }

                    destination[w++] = *s++;
                }

                if (s == end)
                {
                    Fail("json_syntax"); // unterminated
                }
            }

            if (*s == '"')
            {
                written = w;
                return s + 1;
            }

            if (*s != '\\' || end - s < 2)
            {
                Fail("json_syntax"); // control byte, or a backslash at the end
            }

            int code;
            switch (s[1])
            {
                case (byte)'"' or (byte)'\\' or (byte)'/':
                    code = s[1];
                    break;
                case (byte)'b':
                    code = 8;
                    break;
                case (byte)'f':
                    code = 12;
                    break;
                case (byte)'n':
                    code = 10;
                    break;
                case (byte)'r':
                    code = 13;
                    break;
                case (byte)'t':
                    code = 9;
                    break;
                case (byte)'u':
                    code = end - s >= 6 ? Hex4(s + 2) : -1;
                    if (code < 0)
                    {
                        Fail("json_syntax");
                    }

                    if (code is >= 0xD800 and <= 0xDBFF)
                    {
                        bool escape = end - s >= 12 && s[6] == '\\' && s[7] == 'u';
                        int low = escape ? Hex4(s + 8) : -1;
                        if (low is < 0xDC00 or > 0xDFFF)
                        {
                            Fail(escape && low < 0 ? "json_syntax" : "json_string");
                        }

                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                        s += 6;
                    }
                    else if (code is >= 0xDC00 and <= 0xDFFF)
                    {
                        Fail("json_string");
                    }

                    s += 4;
                    break;
                default:
                    Fail("json_syntax");
                    code = 0;
                    break;
            }

            s += 2;
            int length = code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
            if (length > limit - w)
            {
                Fail(tooLong);
            }

            byte* d = destination + w;
            switch (length)
            {
                case 1:
                    d[0] = (byte)code;
                    break;
                case 2:
                    d[0] = (byte)(0xC0 | (code >> 6));
                    d[1] = (byte)(0x80 | (code & 0x3F));
                    break;
                case 3:
                    d[0] = (byte)(0xE0 | (code >> 12));
                    d[1] = (byte)(0x80 | ((code >> 6) & 0x3F));
                    d[2] = (byte)(0x80 | (code & 0x3F));
                    break;
                default:
                    d[0] = (byte)(0xF0 | (code >> 18));
                    d[1] = (byte)(0x80 | ((code >> 12) & 0x3F));
                    d[2] = (byte)(0x80 | ((code >> 6) & 0x3F));
                    d[3] = (byte)(0x80 | (code & 0x3F));
                    break;
            }

            w += length;
        }
    }

    // Four hex digits, or -1. Invalid characters map to 0xFF, whose high bit survives the OR.
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static int Hex4(byte* s)
    {
        ReadOnlySpan<byte> hex = HexValues;
        int a = hex[s[0]], b = hex[s[1]], c = hex[s[2]], d = hex[s[3]];
        return ((a | b | c | d) & 0x80) != 0 ? -1 : (a << 12) | (b << 8) | (c << 4) | d;
    }

    static ReadOnlySpan<byte> HexValues =>
    [
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
    ];

    // JSON integer lexeme, no sign: at most 20 digits, no leading zero, value <= max.
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static byte* Unsigned(byte* p, byte* end, ulong max, out ulong value)
    {
        byte* s = p;
        ulong v = 0;
        while (end - s >= 8 && s - p <= 11)
        {
            ulong chunk = Unsafe.ReadUnaligned<ulong>(s);
            if (!EightDigits(chunk))
            {
                break;
            }

            v = v * 100_000_000 + ParseEight(chunk);
            s += 8;
        }

        while (s < end && (uint)(*s - '0') <= 9 && s - p < 19)
        {
            v = v * 10 + (uint)(*s - '0');
            s++;
        }

        if (s < end && (uint)(*s - '0') <= 9)
        {
            // A 20th digit: the only length that can overflow; a 21st always does.
            uint d = (uint)(*s - '0');
            if (v > 1844674407370955161UL || (v == 1844674407370955161UL && d > 5) || (s + 1 < end && (uint)(s[1] - '0') <= 9))
            {
                Fail("json_unsigned");
            }

            v = v * 10 + d;
            s++;
        }

        if (s == p || (*p == '0' && s - p > 1) || v > max || FractionOrExponent(s, end))
        {
            Fail(s == p || v > max || FractionOrExponent(s, end) ? "json_unsigned" : "json_syntax");
        }

        value = v;
        return s;
    }

    static bool FractionOrExponent(byte* s, byte* end) => s < end && (*s == '.' || (*s | 0x20) == 'e');

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static bool EightDigits(ulong chunk) =>
        ((chunk & 0xF0F0F0F0F0F0F0F0UL) | (((chunk + 0x0606060606060606UL) & 0xF0F0F0F0F0F0F0F0UL) >> 4)) == 0x3333333333333333UL;

    // Eight ASCII digits, first digit in the lowest byte (Lemire, "Quickly parsing eight digits").
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static ulong ParseEight(ulong chunk)
    {
        chunk -= 0x3030303030303030UL;
        chunk = chunk * 10 + (chunk >> 8);
        return ((chunk & 0x000000FF000000FFUL) * (100 + (1000000UL << 32)) +
                ((chunk >> 16) & 0x000000FF000000FFUL) * (1 + (10000UL << 32))) >> 32;
    }

    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    static byte* Signed(byte* p, byte* end, out long value)
    {
        bool negative = p < end && *p == '-';
        byte* s = negative ? p + 1 : p, digits = s;
        ulong v = 0;
        while (s < end && (uint)(*s - '0') <= 9 && s - digits < 8)
        {
            v = v * 10 + (uint)(*s - '0');
            s++;
        }

        if (s == digits || (*digits == '0' && s - digits > 1) || v > 1_000_000 || (s < end && (uint)(*s - '0') <= 9) ||
            FractionOrExponent(s, end))
        {
            Fail(s == digits || v > 1_000_000 || (s < end && (uint)(*s - '0') <= 9) || FractionOrExponent(s, end)
                ? "json_value_range" : "json_syntax");
        }

        value = negative ? -(long)v : (long)v;
        return s;
    }
}
