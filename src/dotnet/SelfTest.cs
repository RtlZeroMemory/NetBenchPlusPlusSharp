using System.Buffers.Binary;
using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Text.Json;

namespace TcpBench;

static class SelfTest
{
    public static int Run(Options options)
    {
        int checks = 0;
        void Check(bool value, string label)
        {
            checks++;
            if (!value)
            {
                throw new InvalidDataException("selftest: " + label);
            }
        }

        const string valid = "[{\"id\":1,\"timestamp_ns\":2,\"source\":3,\"kind\":\"kind04\",\"value_milli\":-5,\"flags\":6,\"message\":\"\"}]";
        foreach (string mode in new[]
        {
            "aggregate",
            "retain-reuse",
            "retain-allocate"
        }

        )
        {
            var p = new Processor(mode, 2, 8192);
            byte[] input = Encoding.UTF8.GetBytes(valid);
            Check(p.Process(input, 1) == (1UL, 16660929537651884513UL), mode + " golden digest");
            Check(p.Epoch.Counts[18] == 1 && p.Epoch.Sums[18] == -5, "category");
            Array.Fill(input, (byte)0);
            p.VerifyRetained();
            checks++;
            var escaped = Encoding.UTF8.GetBytes(valid.Replace("\"id\"", "\"\\u0069d\""));
            Check(p.Process(escaped, 2).Hash == 16660929537651884513UL, "escaped property");
            for (int i = 0; i < 5; i++)
            {
                p.Process(Encoding.UTF8.GetBytes(valid), (ulong)(i + 3));
                p.VerifyRetained();
                checks++;
            }

            string[] invalid = [
"",
" ",
"{}",
"[] []",
"[{}]",
valid + "x",
valid.Replace("\"id\":1", "\"id\":-0"),
valid.Replace("\"source\":3", "\"source\":4294967296"),
valid.Replace("\"id\":1", "\"id\":18446744073709551616"),
valid.Replace("\"id\":1", "\"id\":1.0"),
valid.Replace("\"id\":1", "\"id\":1e0"),
valid.Replace("\"message\":\"\"", "\"message\":\"\\uD800\""),
valid.Replace("\"message\":\"\"", "\"message\":\"\\uDC00\""),
valid.Replace("\"message\":\"\"", "\"message\":\"\\uD800\\u0041\""),
valid.Replace("\"message\":\"\"", "\"message\":\"" + new string('x', 4097) + "\""),
valid.Replace("\"flags\":6", "\"flags\":6,\"\\u0069d\":1"),
valid.Replace("\"id\":1", "\"other\":1"),
valid.Replace("kind04", "kind16"),
valid.Replace("\"flags\":6", "\"flags\":-0"),
valid.Replace("\"flags\":6", "\"flags\":[]")
];
            foreach (string bad in invalid)
            {
                byte[] before = new byte[1056];
                p.Epoch.Write(before);
                bool failed = false;
                try
                {
                    p.Process(Encoding.UTF8.GetBytes(bad), 99);
                }
                catch (Exception e) when (e is InvalidDataException or System.Text.Json.JsonException or InvalidOperationException or ArgumentException)
                {
                    failed = true;
                }

                Check(failed, "reject invalid JSON " + bad[..Math.Min(24, bad.Length)]);
                p.Epoch.Verify(before);
                p.VerifyRetained();
                checks++;
            }

            var unicode = new Processor(mode, 2, 8192);
            ulong raw = unicode.Process(Encoding.UTF8.GetBytes(valid.Replace("\"message\":\"\"", "\"message\":\"😀é\"")), 1).Hash;
            Check(unicode.Process(Encoding.UTF8.GetBytes(valid.Replace("\"message\":\"\"", "\"message\":\"\\ud83d\\ude00\\u00e9\"")), 2).Hash == raw, "Unicode equivalence");
            var signedZero = new Processor(mode, 2, 8192);
            Check(signedZero.Process(Encoding.UTF8.GetBytes(valid.Replace("-5", "-0")), 1).Hash == signedZero.Process(Encoding.UTF8.GetBytes(valid.Replace("-5", "0")), 2).Hash, "signed zero");
            Check(p.Process("[]"u8, 100) == (0UL, Digest.Offset), "empty array");
            var categoryCounts = new ulong[64];
            var categorySums = new long[64];
            categoryCounts[18] = 1;
            categorySums[18] = -5;
            var frame = new CorpusFrame(Encoding.UTF8.GetBytes(valid), 1, 16660929537651884513UL, categoryCounts, categorySums);
            var cycleProcessor = new Processor(mode, 2, 8192);
            ProcessControl.ProcessCycle(cycleProcessor, [frame], mode);
            ProcessControl.ProcessCycle(cycleProcessor, [frame], mode);
            Check(cycleProcessor.Epoch.Counts[18] == 2 && cycleProcessor.Epoch.Sums[18] == -10, "process repeated category validation");
            cycleProcessor.Epoch.Records = 1_000_000_000;
            ProcessControl.ProcessCycle(cycleProcessor, [frame], mode);
            Check(cycleProcessor.Epoch.Records == 1, "process epoch reset");
            foreach (bool moveBucket in new[]
            {
                false,
                true
            }

            )
            {
                var badCounts = (ulong[])categoryCounts.Clone();
                var badSums = (long[])categorySums.Clone();
                if (moveBucket)
                {
                    badCounts[18] = 0;
                    badSums[18] = 0;
                    badCounts[0] = 1;
                    badSums[0] = -5;
                }
                else
                {
                    badSums[18] = -4;
                }

                bool failed = false;
                try
                {
                    ProcessControl.ProcessCycle(new Processor(mode, 2, 8192), [frame with { Counts = badCounts, Sums = badSums }], mode);
                }
                catch (InvalidDataException)
                {
                    failed = true;
                }

                Check(failed, "process rejects category metadata independently of correct digest");
            }
        }

        foreach (string mode in new[] { "aggregate", "retain-reuse", "retain-allocate", "transport" })
        {
            var processor = new Processor(mode, 2, 8192);
            byte[] input = Encoding.UTF8.GetBytes(valid);
            processor.Process(input, 1);
            byte[] before = new byte[1056];
            processor.Epoch.Write(before);
            long retainedBytes = processor.RetainedBytes;
            using var cancelled = new CancellationTokenSource();
            cancelled.Cancel();
            foreach (ProcessingBudget budget in new[]
            {
                new ProcessingBudget(Stopwatch.GetTimestamp() - 1, default),
                new ProcessingBudget(0, cancelled.Token)
            })
            {
                bool rejected = false;
                try
                {
                    processor.Process(input, 2, budget: budget);
                }
                catch (Exception e) when (e is TimeoutException or OperationCanceledException)
                {
                    rejected = true;
                }
                Check(rejected, mode + " rejects expired/cancelled processing budget");
                processor.Epoch.Verify(before);
                Check(processor.RetainedBytes == retainedBytes, "budget preserves retention");
                rejected = false;
                try
                {
                    processor.VerifyRetained(budget);
                }
                catch (Exception e) when (e is TimeoutException or OperationCanceledException)
                {
                    rejected = true;
                }
                Check(rejected, "retained visits observe budget");
            }
            processor.VerifyRetained();
        }
        foreach (string mode in new[] { "retain-reuse", "retain-allocate" })
        {
            // Corrupt the second planned eviction: the first must remain retained when validation fails.
            var processor = new Processor(mode, 2, 200);
            byte[] small = Encoding.UTF8.GetBytes(valid);
            byte[] large = Encoding.UTF8.GetBytes(valid.Replace("\"message\":\"\"", "\"message\":\"" + new string('x', 130) + "\""));
            processor.Process(small, 1);
            processor.Process(small, 2);
            var retained = (Queue<Batch>)typeof(Processor).GetField("retained", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(processor)!;
            Batch[] original = retained.ToArray();
            original[1].StoredDigest ^= 1;
            byte[] before = new byte[1056];
            processor.Epoch.Write(before);
            bool rejected = false;
            try
            {
                processor.Process(large, 3);
            }
            catch (InvalidDataException e) when (e.Message == "retained_integrity")
            {
                rejected = true;
            }
            Check(rejected && retained.SequenceEqual(original) && processor.RetainedBytes == 76, "eviction validation is atomic");
            processor.Epoch.Verify(before);
            original[1].StoredDigest ^= 1;
            processor.VerifyRetained();
            Check(processor.PeakOwnedCapacityBytes >= original.Sum(batch => batch.CapacityBytes) + 168, "failed scratch contributes to peak");
            processor.Process(large, 3);
            Check(retained.Count == 1 && processor.RetainedBytes == 168, "validated multi-eviction commits");

            var capacity = new Processor(mode, 1, 8192);
            capacity.Process(small, 1);
            long firstCapacity = capacity.OwnedCapacityBytes;
            capacity.Process(small, 2);
            Check(capacity.PeakOwnedCapacityBytes == firstCapacity * 2, "peak includes simultaneous scratch and retained arrays");
        }
        var merged = new PhaseResult();
        var connectionMetrics = new ConnectionMetrics();
        connectionMetrics.Sizes[4].Record(1024);
        merged.Merge(connectionMetrics);
        Check(merged.Sizes.Length == 5 && merged.Sizes.Sum(histogram => histogram.Count) == 1, "fixed size-class merge preserves empty classes");
        JsonElement histogramJson = JsonSerializer.SerializeToElement(merged.Sizes[4].Export());
        foreach (string alias in new[] { "p50", "p90", "p99", "p999", "max" })
        {
            Check(histogramJson.GetProperty(alias).GetInt64() == histogramJson.GetProperty(alias + "_ns").GetInt64(), "histogram aliases agree");
        }
        var transport = new Processor("transport", 1, 1);
        Check(transport.Process("not JSON"u8, 1) == (0UL, 8UL), "transport bypass");
        ulong seed = 0;
        Check(ArrivalSchedule.SplitMix(ref seed) == 0xe220a8397b1dcdafUL, "SplitMix64 vector");
        Check(Histogram.Upper(1023) == 1023 && Histogram.Upper(1024) == 1027 && Histogram.Upper(1279) == 2047 && Histogram.Upper(1280) == 2055, "histogram vectors");
        int[] sizeBounds = [4096, 65536, 262144, 1048576, Wire.HardLimit];
        for (int i = 0; i < sizeBounds.Length; i++)
        {
            Check(ConnectionMetrics.SizeClass(sizeBounds[i]) == i, "size class inclusive upper bound");
            if (i > 0)
            {
                Check(ConnectionMetrics.SizeClass(sizeBounds[i - 1] + 1) == i, "size class lower boundary");
            }
        }

        var stages = new Histogram();
        for (int i = 0; i < 1_000_000; i++)
        {
            stages.Record(1024);
            if (i + 1 is not (1 or 9999 or 10000 or 999999 or 1000000))
            {
                continue;
            }

            using var stageJson = System.Text.Json.JsonDocument.Parse(System.Text.Json.JsonSerializer.Serialize(stages.ExportStages()));
            Check((stageJson.RootElement.GetProperty("p99_ns").ValueKind == System.Text.Json.JsonValueKind.Null) == (i + 1 < 10_000), "stage p99 sample gate");
            Check((stageJson.RootElement.GetProperty("p999_ns").ValueKind == System.Text.Json.JsonValueKind.Null) == (i + 1 < 1_000_000), "stage p999 sample gate");
            using var rawJson = System.Text.Json.JsonDocument.Parse(System.Text.Json.JsonSerializer.Serialize(stages.Export()));
            Check(rawJson.RootElement.GetProperty("p999_ns").GetInt64() == 1027, "raw client tails unchanged");
        }

        byte[] header = new byte[16];
        Wire.Header(header, 32, 1, 42);
        Check(Wire.ParseHeader(header) == (32, (ushort)1, 42UL), "wire header");
        BinaryPrimitives.WriteUInt32BigEndian(header, uint.MaxValue);
        bool headerRejected = false;
        try
        {
            Wire.ParseHeader(header);
        }
        catch (InvalidDataException)
        {
            headerRejected = true;
        }

        Check(headerRejected, "overlong frame");
        if (options.Corpus.Length > 0)
        {
            var corpus = new Corpus(options.Corpus);
            foreach (string mode in new[]
            {
                "aggregate",
                "retain-reuse",
                "retain-allocate"
            }

            )
            {
                var processor = new Processor(mode, 8, 67108864);
                var expected = new Summary();
                ulong sequence = 1;
                foreach (var frame in corpus.Frames)
                {
                    Check(processor.Process(frame.Payload, sequence) == (frame.Records, frame.Hash), "corpus oracle");
                    expected.Add(sequence++, frame.Payload.Length, frame.Records, frame.Hash, frame.Counts, frame.Sums);
                    byte[] actual = new byte[1056];
                    processor.Epoch.Write(actual);
                    expected.Verify(actual);
                    checks++;
                }

                processor.VerifyRetained();
            }
        }

        JsonOutput.Print(new
        {
            @event = "selftest",
            valid = true,
            checks,
            build = Metadata.Build()
        });
        return 0;
    }
}
