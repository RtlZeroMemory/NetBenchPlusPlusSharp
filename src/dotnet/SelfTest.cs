using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace TcpBench;

// Mirrors the native selftest case for case; the golden digest is shared by both languages.
static unsafe partial class SelfTest
{
    static bool fast; // parser under test: every check runs for both

    static Processor NewProcessor(Mode mode, int batches, long bytes) => new(mode, batches, bytes, fast);

    const string EscapedTimestamp = "\"\\u0074\\u0069\\u006d\\u0065\\u0073\\u0074\\u0061\\u006d\\u0070\\u005f\\u006e\\u0073\"";
    const string Good = """[{"id":18446744073709551615,"timestamp_ns":0,"source":4294967295,"kind":"kind15","value_milli":-0,"flags":3,"message":"a\u0000\uD83D\uDE00"}]""";
    const ulong GoldenDigest = 3660725836179230917UL;

    // The failure reason, or null when the action completes.
    static string? ErrorOf(Action action)
    {
        try
        {
            action();
            return null;
        }
        catch (Exception e) when (e is BenchException or JsonException)
        {
            return e.Message;
        }
    }

    public static int Run(Options options)
    {
        int checks = 0;
        foreach (bool parser in new[] { false, true })
        {
            fast = parser;
            checks += Checks(options);
        }

        checks += Fuzz();
        JsonOutput.Emit("", new { @event = "selftest", valid = true, checks, canonical_digest = GoldenDigest });
        return 0;
    }

    static int Checks(Options options)
    {
        int checks = 0;
        void Check(bool ok, string what)
        {
            checks++;
            if (!ok)
            {
                throw new BenchException("selftest: " + what);
            }
        }

        var reference = new Result();
        reference.Add(new Row { Id = ulong.MaxValue, Source = uint.MaxValue, Kind = 15, Flags = 3 }, "a\0😀"u8);
        Check(reference.Hash == GoldenDigest, "cross-language golden digest");
        bool Same(Result r) => r.Matches(reference.Records, reference.Hash, reference.Counts, reference.Sums);
        foreach (Mode mode in new[] { Mode.Aggregate, Mode.RetainReuse, Mode.RetainAllocate })
        {
            var p = NewProcessor(mode, 2, 67108864);
            Result Run(string json, Budget budget = default)
            {
                byte[] input = Encoding.UTF8.GetBytes(json);
                Result r = p.Process(input, p.Epoch.Batches + 1, budget);
                Array.Fill(input, (byte)0xcd); // retained data must not alias input
                return r;
            }

            byte[] Snapshot()
            {
                byte[] b = new byte[1056];
                p.Epoch.Write(b);
                return b;
            }

            Check(Same(Run(Good)), "canonical digest");
            long first = p.OwnedCapacity();
            Check(Same(Run(Good)) && Same(Run(Good)), "repeated batches");
            Check(mode == Mode.Aggregate || p.PeakOwned >= first * 2, "scratch counted with retained data");
            using var stopped = new CancellationTokenSource();
            stopped.Cancel();
            var stoppedBudget = new Budget(0, stopped.Token);
            foreach (Budget budget in new[] { stoppedBudget, new Budget(Stopwatch.GetTimestamp() - 1, default) })
            {
                byte[] before = Snapshot();
                long retained = p.Retained;
                string reason = budget.Equals(stoppedBudget) ? "shutdown" : "frame_timeout";
                Check(ErrorOf(() => Run(Good, budget)) == reason && ErrorOf(() => p.VerifyRetained(budget)) == reason &&
                      p.Epoch.Matches(before) && p.Retained == retained, "expired budget leaves state unchanged");
                p.VerifyRetained();
            }

            string record = Good[1..^1];
            var bad = new List<byte[]>();
            foreach (string s in new[] { "", " ", "{}", "[] []", "[],", "[[]]", "[{}]", Good + "x", "\uFEFF" + Good, "[" + record + "," + record + "," + record + ",{}]" })
            {
                bad.Add(Encoding.UTF8.GetBytes(s));
            }

            foreach (var (from, to) in new[]
            {
                ("18446744073709551615", "18446744073709551616"),
                ("18446744073709551615", "-0"),
                ("18446744073709551615", "01"),
                ("4294967295", "4294967296"),
                ("\"value_milli\":-0", "\"value_milli\":1e0"),
                ("\"value_milli\":-0", "\"value_milli\":1.0"),
                ("\"value_milli\":-0", "\"value_milli\":1000001"),
                ("\"value_milli\":-0", "\"value_milli\":-"),
                ("\"value_milli\":-0", "\"value_milli\":\"1\""),
                ("kind15", "kind16"),
                ("kind15", "kind1"),
                (@"a\u0000\uD83D\uDE00", @"\uD800"),
                (@"a\u0000\uD83D\uDE00", @"\uDC00x"),
                ("\"source\":", "\"id\":"),
                ("\"source\":", @"""\u0069d"":"),
                ("\"source\":", "\"unknown\":")
            })
            {
                bad.Add(Encoding.UTF8.GetBytes(Good.Replace(from, to)));
            }

            // Raw invalid UTF-8 in the message: overlong, encoded surrogate, and 0xFF.
            byte[] template = Encoding.UTF8.GetBytes(Good.Replace(@"a\u0000\uD83D\uDE00", "@@@"));
            foreach (byte[] raw in new[] { new byte[] { 0xc0, 0xaf, 0x20 }, [0xed, 0xa0, 0x80], [0xff, 0x20, 0x20] })
            {
                byte[] copy = (byte[])template.Clone();
                raw.CopyTo(copy, Array.IndexOf(template, (byte)'@'));
                bad.Add(copy);
            }

            foreach (byte[] input in bad)
            {
                byte[] before = Snapshot();
                Check(ErrorOf(() => p.Process(input, p.Epoch.Batches + 1)) != null && p.Epoch.Matches(before), "invalid batch rejected atomically: " + Encoding.Latin1.GetString(input[..Math.Min(40, input.Length)]));
                p.VerifyRetained();
            }

            Check(p.PeakOwned >= p.OwnedCapacity(), "failed scratch counted");
            for (int i = 0; i < 16; i++)
            {
                Check(Same(Run(Good)), "reuse after eviction");
            }

            Check(Run("[]").Matches(0, Digest.Offset, Summary.NoCounts, Summary.NoSums), "empty array");
            Check(Same(Run(Good.Replace("\"id\"", "\"\\u0069d\""))), "escaped property name");
            Check(Same(Run(Good.Replace("\"timestamp_ns\"", EscapedTimestamp).Replace("\"kind15\"", "\"\\u006bind15\""))), "escaped longest name and kind");
            Check(Same(Run(" \r\n" + Good.Replace("\"flags\":3", "\"flags\" : 3 ") + "\t")), "insignificant whitespace");
            // A budget that expires mid-parse stops the parse (checked every 1024 records).
            byte[] many = Encoding.UTF8.GetBytes("[" + string.Join(",", Enumerable.Repeat(record, 100_000)) + "]");
            var large = NewProcessor(mode, 8, 1L << 31);
            Check(ErrorOf(() => large.Process(many, 1, new Budget(Stopwatch.GetTimestamp() + Stopwatch.Frequency / 500, default))) == "frame_timeout" &&
                  large.Epoch.Batches == 0 && large.Retained == 0, "mid-parse budget expiry");
        }

        {
            // Limits apply to decoded text. CopyString's escaped path rejects an exact fit, so the scratch must
            // exceed the limit; the first case failed with a 4,096-byte scratch.
            var p = NewProcessor(Mode.RetainReuse, 2, 67108864);
            string With(string message) => Good.Replace("a\\u0000\\uD83D\\uDE00", message);
            string a4095 = new('A', 4095);
            foreach (string ok in new[] { "\\u0041" + a4095, string.Concat(Enumerable.Repeat("\\u0001", 4096)), "A" + a4095 })
            {
                Check(ErrorOf(() => p.Process(Encoding.UTF8.GetBytes(With(ok)), p.Epoch.Batches + 1)) == null, "4,096 decoded message bytes accepted");
            }

            foreach (string tooLong in new[] { "\\u0041A" + a4095, string.Concat(Enumerable.Repeat("\\u0001", 4097)), "AA" + a4095 })
            {
                Check(ErrorOf(() => p.Process(Encoding.UTF8.GetBytes(With(tooLong)), p.Epoch.Batches + 1)) == "json_message_length", "4,097 decoded message bytes rejected");
            }
        }

        foreach (Mode mode in new[] { Mode.RetainReuse, Mode.RetainAllocate })
        {
            // Chunked storage: 3,000 records of 3,000-byte messages span 3 row chunks and 143 text chunks
            // (capacity cross-checked with the native selftest); with one batch retained, the smaller batch lands in
            // reused multi-chunk storage. 16 or 32 full 4 KiB messages fill chunks exactly before an empty message.
            static byte[] Records(int n, int length) => Encoding.UTF8.GetBytes("[" + string.Join(",", Enumerable.Range(0, n).Select(i =>
                $"{{\"id\":{i},\"timestamp_ns\":0,\"source\":0,\"kind\":\"kind01\",\"value_milli\":1,\"flags\":{i},\"message\":\"{new string((char)('a' + i % 26), length)}\"}}")) + "]");
            static byte[] WithEmpty(int full) => Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(Records(full, 4096))[..^1] +
                ",{\"id\":0,\"timestamp_ns\":0,\"source\":0,\"kind\":\"kind00\",\"value_milli\":0,\"flags\":0,\"message\":\"\"}]");
            var p = NewProcessor(mode, 1, 67108864);
            byte[][] inputs = [Records(3000, 3000), Records(3000, 3000), Records(1500, 100), WithEmpty(16), WithEmpty(32)];
            long[] capacity = mode == Mode.RetainReuse ? [9_519_104, 19_038_208, 19_038_208] : [9_519_104, 9_519_104, 294_912];
            for (int i = 0; i < inputs.Length; i++)
            {
                Result expected = NewProcessor(Mode.Aggregate, 0, 0).Process(inputs[i], 1);
                Check(NewProcessor(mode, 1, 67108864).Process(inputs[i], 1).Matches(expected.Records, expected.Hash, expected.Counts, expected.Sums), "fresh chunked batch digest");
                Check(p.Process(inputs[i], p.Epoch.Batches + 1).Matches(expected.Records, expected.Hash, expected.Counts, expected.Sums), "chunked batch digest");
                p.VerifyRetained();
                Check(i >= capacity.Length || p.OwnedCapacity() == capacity[i], "chunked capacity");
            }
        }

        foreach (Mode mode in new[] { Mode.RetainReuse, Mode.RetainAllocate })
        {
            // A batch larger than the byte limit fails before commit; retained data survives.
            var p = NewProcessor(mode, 8, 200);
            p.Process(Encoding.UTF8.GetBytes(Good), 1);
            string big = Good.Replace("a\\u0000", new string('x', 170));
            Check(ErrorOf(() => p.Process(Encoding.UTF8.GetBytes(big), 2)) == "retention_capacity" && p.Retained == 44 && p.Epoch.Batches == 1, "oversized batch is atomic");
            p.VerifyRetained();

            // Corrupt the second planned eviction: the first must not be evicted either.
            var q = NewProcessor(mode, 2, 120);
            q.Process(Encoding.UTF8.GetBytes(Good), 1);
            q.Process(Encoding.UTF8.GetBytes(Good), 2);
            Batch[] live = q.LiveBatches();
            live[1].Digest ^= 1;
            byte[] both = Encoding.UTF8.GetBytes(Good.Replace("a\\u0000", new string('y', 40))); // needs both evictions
            Check(ErrorOf(() => q.Process(both, 3)) == "retained_integrity" && q.LiveBatches().SequenceEqual(live) && q.Epoch.Batches == 2, "eviction verification precedes mutation");
            live[1].Digest ^= 1;
            q.VerifyRetained();
        }

        Check(Histogram.Upper(1023) == 1023 && Histogram.Upper(1024) == 1027 && Histogram.Upper(1279) == 2047 && Histogram.Upper(1280) == 2055, "histogram vectors");
        foreach (long v in new long[] { 0, 1023, 1024, 1027, 2048, 1_000_000, 59_999_999_999 })
        {
            int i = Histogram.Index(v);
            Check(Histogram.Upper(i) >= v && (i == 0 || Histogram.Upper(i - 1) < v), "histogram bucket bounds");
        }

        var h = new Histogram();
        h.Record(5);
        h.Record(60_000_000_000);
        Check(h.Quantile(.5) == 5 && h.Quantile(1) == null && h.Max == 60_000_000_000, "overflow rank has no finite quantile");
        var stage = new Histogram();
        for (int i = 1; i <= 1_000_000; i++)
        {
            stage.Record(1024);
            if (i is 9_999 or 10_000 or 999_999 or 1_000_000)
            {
                JsonElement json = JsonSerializer.SerializeToElement(stage.Export(stage: true));
                Check((json.GetProperty("p99_ns").ValueKind == JsonValueKind.Null) == (i < 10_000), "stage p99 sample gate");
                Check((json.GetProperty("p999_ns").ValueKind == JsonValueKind.Null) == (i < 1_000_000), "stage p99.9 sample gate");
            }
        }

        ulong seed = 0;
        Check(Arrivals.SplitMix(ref seed) == 0xe220a8397b1dcdafUL, "SplitMix64 vector");
        for (int lanes = 1; lanes <= 7; lanes++)
        {
            for (int frames = 1; frames <= 9; frames++)
            {
                for (long ordinal = 0; ordinal < 15; ordinal++)
                {
                    for (long remaining = 0; remaining < 20; remaining++)
                    {
                        for (int lane = 0; lane < lanes; lane++)
                        {
                            int want = lane % frames;
                            for (long i = ordinal; i < ordinal + remaining; i++)
                            {
                                want = i % lanes == lane ? (want + lanes) % frames : want;
                            }

                            Check(Arrivals.AdvanceCursor(lane % frames, lane, ordinal, remaining, lanes, frames) == want, "aborted cursor arithmetic");
                        }
                    }
                }
            }
        }

        foreach (double rate in new[] { 0.1, 1.0, 3.0, 10.0, 1000.0 })
        {
            foreach (double duration in new[] { 0.001, 0.1, 0.3, 1.0 })
            {
                long windowTicks = (long)(duration * Stopwatch.Frequency), want = 0;
                while ((long)(want / rate * Stopwatch.Frequency) < windowTicks)
                {
                    want++;
                }

                Check(Arrivals.SteadyCount(rate, duration, windowTicks) == want, "steady arrival count");
            }
        }

        if (options.Corpus.Length > 0)
        {
            var corpus = new Corpus(options.Corpus);
            foreach (Mode mode in new[] { Mode.Aggregate, Mode.RetainReuse, Mode.RetainAllocate })
            {
                var p = NewProcessor(mode, 8, 67108864);
                ulong sequence = 0;
                foreach (CorpusFrame f in corpus.Frames)
                {
                    Check(p.Process(f.Payload, ++sequence).Matches(f.Records, f.Hash, f.Counts, f.Sums), "corpus oracle " + mode);
                }

                p.VerifyRetained();
            }
        }

        return checks;
    }
}
