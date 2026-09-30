using System.Runtime.InteropServices;
using System.Text;
using System.Text.Unicode;

namespace TcpBench;

// Differential fuzzing for the unsafe fast paths. FastJson must accept and reject exactly what the
// Utf8JsonReader path does, with identical results; Utf8Check must agree with Utf8.IsValid. Every
// input ends right before a no-access page, so any read past its end crashes the selftest.
static unsafe partial class SelfTest
{
    [LibraryImport("kernel32.dll")]
    private static partial byte* VirtualAlloc(nint address, nuint size, uint type, uint protect);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool VirtualProtect(byte* address, nuint size, uint protect, out uint old);

    const int GuardedBytes = 1 << 20;
    static byte* guarded; // GuardedBytes of read/write memory followed by a no-access page

    static ReadOnlySpan<byte> Guarded(byte[] input)
    {
        byte* at = guarded + GuardedBytes - input.Length;
        input.CopyTo(new Span<byte>(at, input.Length));
        return new ReadOnlySpan<byte>(at, input.Length);
    }

    // BENCH_FUZZ=<seed>,<scale> runs a longer soak (scale multiplies both iteration counts).
    static int Fuzz(int documents = 60_000, int texts = 60_000)
    {
        int checks = 0, seed = 20260930;
        if (Environment.GetEnvironmentVariable("BENCH_FUZZ") is { } soak)
        {
            string[] parts = soak.Split(',');
            seed = int.Parse(parts[0], System.Globalization.CultureInfo.InvariantCulture);
            int scale = parts.Length > 1 ? int.Parse(parts[1], System.Globalization.CultureInfo.InvariantCulture) : 1;
            documents *= scale;
            texts *= scale;
        }

        if (guarded == null)
        {
            guarded = VirtualAlloc(0, GuardedBytes + 4096, 0x3000, 0x04); // reserve+commit, read/write
            if (guarded == null || !VirtualProtect(guarded + GuardedBytes, 4096, 0x01, out _)) // last page: no access
            {
                throw new BenchException("selftest: guard page");
            }
        }

        var random = new Random(seed);
        int accepted = 0;
        foreach (Mode mode in new[] { Mode.Aggregate, Mode.RetainReuse })
        {
            var reference = new Processor(mode, 4, 1 << 26);
            var candidate = new Processor(mode, 4, 1 << 26, fastParser: true);
            // Canaries: the fast parser may write [0, 4128) and [8192, 8240) of its scratch, nothing else.
            byte[] scratch = candidate.Scratch;
            scratch.AsSpan(4128, 8192 - 4128).Fill(0xA5);
            scratch.AsSpan(8240).Fill(0xA5);
            for (int i = 0; i < documents / 2; i++)
            {
                bool clean = random.Next(2) == 0;
                byte[] input = clean ? Document(random, true) : Mutate(random, Document(random, false), random.Next(3));
                Result? want = null, have = null;
                string? expected = ErrorOf(() => want = reference.Process(input, reference.Epoch.Batches + 1));
                string? got = ErrorOf(() => have = candidate.Process(Guarded(input), candidate.Epoch.Batches + 1));
                bool same = (expected is null) == (got is null) &&
                    (expected is not null || have!.Matches(want!.Records, want.Hash, want.Counts, want.Sums));
                checks++;
                accepted += expected is null ? 1 : 0;
                if (scratch.AsSpan(4128, 8192 - 4128).IndexOfAnyExcept((byte)0xA5) >= 0 || scratch.AsSpan(8240).IndexOfAnyExcept((byte)0xA5) >= 0)
                {
                    throw new BenchException("selftest: fast parser wrote outside its scratch regions");
                }

                if (!same)
                {
                    throw new BenchException($"selftest: parsers disagree ({expected ?? "ok"} vs {got ?? "ok"}): " +
                        Convert.ToHexString(input.AsSpan(0, Math.Min(input.Length, 400))));
                }
            }

            reference.VerifyRetained();
            candidate.VerifyRetained();
        }

        if (accepted < documents / 5 || accepted > documents * 4 / 5)
        {
            throw new BenchException($"selftest: fuzz mix too one-sided ({accepted} of {documents} accepted)");
        }

        // Every piece at every offset, followed by ASCII runs that end exactly at, before and after the 32- and
        // 64-byte block edges; and inputs of lengths 1..130 that end in each lead or continuation byte.
        byte[][] edge = [[0xC3], [0xE2, 0x82], [0xF0, 0x9F, 0x98], [0x80], [0xED, 0xA0, 0x80], [0xC3, 0xA9], [0xE2, 0x82, 0xAC],
            [0xF0, 0x9F, 0x98, 0x80], [0xF4, 0x90, 0x80, 0x80], [0xC0, 0x80]];
        foreach (byte[] piece in edge)
        {
            for (int offset = 0; offset <= 130; offset++)
            {
                foreach (int after in new[] { 0, 1, 31, 32, 33, 63, 64, 65, 128 })
                {
                    byte[] text = [.. Enumerable.Repeat((byte)'x', offset), .. piece, .. Enumerable.Repeat((byte)'x', after)];
                    checks++;
                    if (Utf8Check.IsValid(Guarded(text)) != Utf8.IsValid(text))
                    {
                        throw new BenchException("selftest: UTF-8 validators disagree: " + Convert.ToHexString(text));
                    }
                }
            }
        }

        int validTexts = 0;
        for (int i = 0; i < texts; i++)
        {
            byte[] text = Text(random);
            checks++;
            validTexts += Utf8.IsValid(text) ? 1 : 0;
            if (Utf8Check.IsValid(Guarded(text)) != Utf8.IsValid(text))
            {
                throw new BenchException("selftest: UTF-8 validators disagree: " + Convert.ToHexString(text));
            }
        }

        if (validTexts < texts / 5 || validTexts > texts * 19 / 20)
        {
            throw new BenchException($"selftest: UTF-8 fuzz mix too one-sided ({validTexts} of {texts} valid)");
        }

        return checks;
    }

    // A JSON batch. Clean ones are valid: shuffled fields, varied whitespace, escaped names and kinds,
    // boundary integers, messages mixing ASCII, escapes, surrogate pairs and raw UTF-8. Dirty ones also
    // draw invalid values, missing, duplicate or unknown fields and over-long messages.
    static byte[] Document(Random random, bool clean)
    {
        string Ws() => random.Next(4) switch { 0 => " ", 1 => "\n\t", 2 => "\r\n  ", _ => "" };
        string Pick(string[] valid, string[] invalid) =>
            !clean && random.Next(8) == 0 ? invalid[random.Next(invalid.Length)] : valid[random.Next(valid.Length)];
        var sb = new StringBuilder("[").Append(Ws());
        for (int record = random.Next(8) == 0 ? random.Next(3, 12) : random.Next(0, 3); record >= 0; record--)
        {
            var fields = new List<string>
            {
                Name("id", random) + ":" + Ws() + Pick(["0", "7", "18446744073709551615", "12345678901234567", "10000000000000000000",
                    "18446744073709551610", Digits(random, random.Next(1, 20))],
                    ["18446744073709551616", "18446744073709551620", "99999999999999999999", "184467440737095516150", "007", "-1",
                    "1.0", "1e3", "\"1\"", "true", "-", Digits(random, random.Next(1, 21)) + Pick2(random, ".5", "e1", "E+2", ".", "e")]),
                Name("timestamp_ns", random) + ":" + Pick(["1700000000000000000", "0", "18446744073709551615", "99999999"], ["01", "1.5", "+1"]),
                Name("source", random) + ":" + Pick(["4294967295", "0", "123", "10"], ["4294967296", "-0", "00"]),
                Name("kind", random) + ":" + Pick(["\"kind00\"", "\"kind15\"", "\"\\u006bind07\"", "\"kind09\"", "\"kin\\u006410\""],
                    ["\"kind16\"", "\"Kind01\"", "\"kind1\"", "5", "\"kind\"", "\"kind010\"", "\"kind05x", "\"kind05\u0001\"",
                    "\"kind05\\\"\"", "\"kind05\\n\""]),
                Name("value_milli", random) + ":" + Pick(["-0", "1000000", "-1000000", "0", "123", "-999999"],
                    ["1000001", "-1000001", "-", "--1", "-00", "01", "1e0"]),
                Name("flags", random) + ":" + Pick(["0", "3", "4294967295", "12"], ["4294967296", "-3", "3.0"]),
                Name("message", random) + ":" + Ws() + Message(random, clean),
            };
            if (!clean && random.Next(15) == 0)
            {
                fields.RemoveAt(random.Next(fields.Count)); // missing field
            }

            if (!clean && random.Next(15) == 0)
            {
                fields.Add(fields[random.Next(fields.Count)]); // duplicate
            }

            if (!clean && random.Next(20) == 0)
            {
                // Unknown names, including same-length near misses of real ones.
                string[] unknown = ["extra", "i\\u0064x", "value_millx", "timestamp_nz", "messagf", "flagz", "Id", "kinds", "sourcf", "kind "];
                fields.Add("\"" + unknown[random.Next(unknown.Length)] + "\":1");
            }

            sb.Append('{').Append(Ws());
            sb.Append(string.Join(Ws() + "," + Ws(), fields.OrderBy(_ => random.Next())));
            sb.Append(Ws()).Append('}').Append(Ws()).Append(record > 0 ? "," + Ws() : "");
        }

        sb.Append(']').Append(Ws());
        return Encoding.UTF8.GetBytes(sb.ToString());
    }

    static string Name(string name, Random random) => random.Next(12) != 0
        ? "\"" + name + "\""
        : "\"" + string.Concat(name.Select(c => random.Next(3) switch { 0 => $"\\u{(int)c:x4}", 1 => $"\\u{(int)c:X4}", _ => c.ToString() })) + "\"";

    static string Digits(Random random, int count) =>
        (char)('1' + random.Next(9)) + string.Concat(Enumerable.Range(1, count - 1).Select(_ => (char)('0' + random.Next(10))));

    static string Pick2(Random random, params string[] values) => values[random.Next(values.Length)];

    static string Message(Random random, bool clean)
    {
        if (!clean && random.Next(8) == 0)
        {
            string[] invalid = ["1", "null", "\"\\", "\"a\\x\"", "\"\\u12\"", "\"\\uD800\"", "\"\\uDC00x\"", "\"\\uD83D\\u0041\"",
                "\"\u0001\"", "\"\\uD83D\\\\uDE00\"", "\"tab\there\""];
            return invalid[random.Next(invalid.Length)];
        }

        string[] pieces = ["a", "Z", " ", "\\\"", "\\\\", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t", "\\u0041", "\\u00e9", "\\u20AC",
            "\\uFFFF", "\\u0000", "\\uD83D\\uDE00", "\\uDBFF\\uDFFF", "é", "€", "😀", "Ünïcödé", "日本語", "\u007f"];
        var sb = new StringBuilder("\"");
        int length = random.Next(8) != 0 ? random.Next(0, 90) : clean ? random.Next(600, 3000) : random.Next(3900, 4400);
        while (sb.Length < length)
        {
            sb.Append(random.Next(3) == 0 ? new string('x', random.Next(1, 90)) : pieces[random.Next(pieces.Length)]);
        }

        return sb.Append('"').ToString();
    }

    // Byte-level damage: overwrite, insert, delete or truncate with bytes that matter to JSON and UTF-8.
    static byte[] Mutate(Random random, byte[] input, int count)
    {
        byte[] interesting = [.. "\"\\{}[],:0123456789-.eEu nrt\t\n\r"u8, 0x00, 0x1F, 0x7F, 0x80, 0xBF, 0xC0, 0xC3, 0xE2, 0xED, 0xF0, 0xF4, 0xFF];
        var bytes = new List<byte>(input);
        for (int i = 0; i < count && bytes.Count > 0; i++)
        {
            int at = random.Next(bytes.Count);
            switch (random.Next(4))
            {
                case 0:
                    bytes[at] = interesting[random.Next(interesting.Length)];
                    break;
                case 1:
                    bytes.Insert(at, interesting[random.Next(interesting.Length)]);
                    break;
                case 2:
                    bytes.RemoveAt(at);
                    break;
                default:
                    bytes.RemoveRange(at, bytes.Count - at);
                    break;
            }
        }

        return [.. bytes];
    }

    // Byte strings around UTF-8 edge cases at every alignment: valid 1-4 byte characters, truncated
    // sequences, overlongs, encoded surrogates, values past U+10FFFF and stray continuation bytes.
    static byte[] Text(Random random)
    {
        byte[][] pieces =
        [
            "a"u8.ToArray(), "0123456789abcdef"u8.ToArray(), [0xC3, 0xA9], [0xE2, 0x82, 0xAC], [0xF0, 0x9F, 0x98, 0x80],
            [0xF4, 0x8F, 0xBF, 0xBF], [0xED, 0x9F, 0xBF], [0xEE, 0x80, 0x80], [0xDF, 0xBF], [0xE0, 0xA0, 0x80],
            [0xC3], [0xE2, 0x82], [0xF0, 0x9F, 0x98], [0x80], [0xBF], [0xC0, 0x80], [0xC1, 0xBF], [0xE0, 0x80, 0x80],
            [0xED, 0xA0, 0x80], [0xED, 0xBF, 0xBF], [0xF0, 0x80, 0x80, 0x80], [0xF4, 0x90, 0x80, 0x80], [0xF5, 0x80, 0x80, 0x80], [0xFF],
        ];
        var bytes = new List<byte>(Enumerable.Repeat((byte)'x', random.Next(0, 70)));
        bool mostlyValid = random.Next(3) != 0;
        for (int n = random.Next(1, 40); n > 0; n--)
        {
            bytes.AddRange(pieces[random.Next(mostlyValid ? 10 : pieces.Length)]);
        }

        if (mostlyValid && random.Next(4) == 0 && bytes.Count > 0)
        {
            bytes[random.Next(bytes.Count)] = pieces[10 + random.Next(pieces.Length - 10)][0]; // one damaged byte
        }

        return [.. bytes];
    }
}
