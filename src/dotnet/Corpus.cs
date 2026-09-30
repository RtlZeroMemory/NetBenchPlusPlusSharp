using System.Buffers.Binary;
using System.Diagnostics;

namespace TcpBench;

sealed record CorpusFrame(byte[] Payload, ulong Records, ulong Hash, ulong[] Counts, long[] Sums);

sealed class Corpus
{
    public readonly CorpusFrame[] Frames;
    public readonly long PayloadBytes;
    public readonly int MaxFrame;

    // Structural checks precede allocation; the corpus is trusted input only afterwards.
    public Corpus(string path)
    {
        using var file = File.OpenRead(path);
        Span<byte> header = stackalloc byte[16];
        file.ReadExactly(header);
        uint count = BinaryPrimitives.ReadUInt32BigEndian(header[8..]);
        if (!header[..8].SequenceEqual("TCPBCH01"u8) || BinaryPrimitives.ReadUInt32BigEndian(header[12..]) != 0 ||
            count == 0 || count > (file.Length - 16) / 1045)
        {
            throw new BenchException("corpus_header");
        }

        Frames = new CorpusFrame[count];
        byte[] meta = new byte[1044];
        for (int i = 0; i < Frames.Length; i++)
        {
            file.ReadExactly(meta);
            uint length = BinaryPrimitives.ReadUInt32BigEndian(meta);
            ulong records = BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(4));
            if (length == 0 || length > Options.HardLimit || length > file.Length - file.Position || records > Summary.RecordLimit)
            {
                throw new BenchException("corpus_frame");
            }

            var counts = new ulong[64];
            var sums = new long[64];
            ulong total = 0;
            for (int b = 0; b < 64; b++)
            {
                counts[b] = BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(20 + b * 8));
                sums[b] = BinaryPrimitives.ReadInt64BigEndian(meta.AsSpan(532 + b * 8));
                if (counts[b] > records - total || sums[b] < -(long)counts[b] * 1_000_000 || sums[b] > (long)counts[b] * 1_000_000)
                {
                    throw new BenchException("corpus_metadata");
                }

                total += counts[b];
            }

            if (total != records)
            {
                throw new BenchException("corpus_metadata");
            }

            byte[] payload = new byte[length];
            file.ReadExactly(payload);
            Frames[i] = new(payload, records, BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(12)), counts, sums);
            PayloadBytes += length;
            MaxFrame = Math.Max(MaxFrame, (int)length);
        }

        if (file.Position != file.Length)
        {
            throw new BenchException("corpus_trailing");
        }
    }
}

// Processing-only control: full-corpus cycles through the server's processor, no sockets.
static class ProcessControl
{
    public static int Run(Options o)
    {
        var corpus = new Corpus(o.Corpus);
        var processor = new Processor(o.Mode, o.RetainBatches, o.RetainBytes, o.FastParser);
        ulong sequence = 0, frames = 0, records = 0, bytes = 0;
        void Cycle()
        {
            foreach (CorpusFrame f in corpus.Frames)
            {
                bool transport = o.Mode == Mode.Transport;
                if (!transport && f.Records > Summary.RecordLimit - processor.Epoch.Records)
                {
                    processor.Epoch.Clear();
                }

                Result r = processor.Process(f.Payload, ++sequence);
                if (!(transport
                    ? r.Matches(0, (ulong)f.Payload.Length, Summary.NoCounts, Summary.NoSums)
                    : r.Matches(f.Records, f.Hash, f.Counts, f.Sums)))
                {
                    throw new BenchException("process_oracle_mismatch");
                }

                frames++;
                records += r.Records;
                bytes += (ulong)f.Payload.Length;
            }
        }

        for (long until = Stopwatch.GetTimestamp() + (long)(o.Warmup * Stopwatch.Frequency); Stopwatch.GetTimestamp() < until;)
        {
            Cycle();
        }

        frames = records = bytes = 0;
        Resources before = Resources.Sample();
        do
        {
            Cycle(); // whole cycles only; elapsed can exceed the requested duration
        }
        while (Stopwatch.GetElapsedTime(before.Timestamp).TotalSeconds < o.Duration);
        Resources after = Resources.Sample();
        double seconds = after.Seconds(before);
        processor.VerifyRetained();
        JsonOutput.Emit(o.Output, new
        {
            role = "process",
            implementation = "csharp",
            mode = Options.Name(o.Mode),
            valid = true,
            error = (string?)null,
            seconds,
            frames,
            records,
            payload_bytes = bytes,
            records_per_second = records / seconds,
            payload_mib_per_second = bytes / seconds / 1048576,
            resources = new
            {
                scope = "timed full-corpus cycles after warmup",
                cpu_seconds = after.CpuSeconds - before.CpuSeconds,
                allocations = (long?)null,
                allocated_bytes = after.Allocated - before.Allocated,
                gc = after.Gc(before),
                peak_working_set_bytes = Resources.PeakWorkingSet()
            },
            retention = new
            {
                canonical_bytes = processor.Retained,
                owned_capacity_peak_bytes = processor.PeakOwned
            },
            build = Build.Info(o.FastParser)
        });
        return 0;
    }
}
