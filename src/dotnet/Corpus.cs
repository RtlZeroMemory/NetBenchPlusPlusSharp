using System.Buffers.Binary;
using System.Diagnostics;
using System.Security.Cryptography;

namespace TcpBench;

sealed record CorpusFrame(byte[] Payload, ulong Records, ulong Hash, ulong[] Counts, long[] Sums);
sealed class Corpus
{
    public readonly CorpusFrame[] Frames;
    public readonly string Hash;
    public long PayloadBytes { get; }

    public Corpus(string path)
    {
        using var file = File.OpenRead(path);
        Span<byte> header = stackalloc byte[16];
        file.ReadExactly(header);
        uint count = BinaryPrimitives.ReadUInt32BigEndian(header[8..]);
        if (!header[..8].SequenceEqual("TCPBCH01"u8) || BinaryPrimitives.ReadUInt32BigEndian(header[12..]) != 0 ||
            count == 0 || count > 1_000_000 || count > (file.Length - 16) / 1045)
        {
            throw new InvalidDataException("corpus_header");
        }

        Frames = new CorpusFrame[(int)count];
        byte[] meta = new byte[1044];
        for (int i = 0; i < Frames.Length; i++)
        {
            file.ReadExactly(meta);
            uint length = BinaryPrimitives.ReadUInt32BigEndian(meta);
            ulong records = BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(4)), hash = BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(12));
            if (length == 0 || length > Wire.HardLimit || length > file.Length - file.Position || records > 1_000_000_000)
            {
                throw new InvalidDataException("corpus_frame");
            }

            ulong[] counts = new ulong[64];
            long[] sums = new long[64];
            ulong total = 0;
            for (int b = 0; b < 64; b++)
            {
                counts[b] = BinaryPrimitives.ReadUInt64BigEndian(meta.AsSpan(20 + b * 8));
                sums[b] = BinaryPrimitives.ReadInt64BigEndian(meta.AsSpan(532 + b * 8));
                if (counts[b] > records || sums[b] < -(long)counts[b] * 1_000_000 || sums[b] > (long)counts[b] * 1_000_000)
                {
                    throw new InvalidDataException("corpus_metadata");
                }

                total = checked(total + counts[b]);
            }

            if (total != records)
            {
                throw new InvalidDataException("corpus_record_count");
            }

            byte[] payload = new byte[length];
            file.ReadExactly(payload);
            Frames[i] = new(payload, records, hash, counts, sums);
            PayloadBytes += length;
        }

        if (file.Position != file.Length)
        {
            throw new InvalidDataException("corpus_trailing");
        }

        file.Position = 0;
        Hash = Convert.ToHexStringLower(SHA256.HashData(file));
    }
}

static class ProcessControl
{
    static readonly ulong[] ZeroCounts = new ulong[64];
    static readonly long[] ZeroSums = new long[64];
    internal static void ProcessCycle(Processor processor, CorpusFrame[] frames, string mode)
    {
        foreach (var frame in frames)
        {
            ulong records = mode == "transport" ? 0 : frame.Records, hash = mode == "transport" ? (ulong)frame.Payload.Length : frame.Hash;
            if (processor.Epoch.Records + records > 1_000_000_000)
            {
                processor.Epoch.Reset();
            }

            ulong sequence = processor.Epoch.Batches + 1;
            var result = processor.Process(frame.Payload, sequence);
            if (result.Records != records || result.Hash != hash)
            {
                throw new InvalidDataException("process_oracle");
            }

            processor.VerifyCategories(mode == "transport" ? ZeroCounts : frame.Counts, mode == "transport" ? ZeroSums : frame.Sums);
        }
    }

    public static int Run(Options options)
    {
        var corpus = new Corpus(options.Corpus);
        var processor = new Processor(options.Mode, options.RetainBatches, options.RetainBytes);
        long until = Stopwatch.GetTimestamp() + (long)(options.Warmup * Stopwatch.Frequency);
        while (Stopwatch.GetTimestamp() < until)
        {
            ProcessCycle(processor, corpus.Frames, options.Mode);
        }

        processor.Epoch.Reset();
        var snapshot = new Resources();
        long start = Stopwatch.GetTimestamp();
        ulong batches = 0, records = 0, bytes = 0;
        ulong corpusRecords = options.Mode == "transport" ? 0 : corpus.Frames.Aggregate(0UL, (sum, f) => checked(sum + f.Records));
        do
        {
            ProcessCycle(processor, corpus.Frames, options.Mode);
            batches += (ulong)corpus.Frames.Length;
            records += corpusRecords;
            bytes += (ulong)corpus.PayloadBytes;
        }
        while (Stopwatch.GetElapsedTime(start).TotalSeconds < options.Duration);
        double seconds = Stopwatch.GetElapsedTime(start).TotalSeconds;
        processor.VerifyRetained();
        JsonOutput.Save(options.Output, new
        {
            implementation = "dotnet",
            language = "csharp",
            command = "process",
            kind = "process",
            mode = options.Mode,
            control = "full-corpus processing without TCP",
            valid = true,
            success = true,
            completed_frames = batches,
            completed_records = records,
            records,
            payload_bytes = bytes,
            duration_seconds = seconds,
            requested_duration_seconds = options.Duration,
            frames_per_second = batches / seconds,
            records_per_second = records / seconds,
            payload_gib_per_second = bytes / seconds / (1L << 30),
            retained_canonical_bytes = processor.RetainedBytes,
            owned_storage_bytes = processor.OwnedCapacityBytes,
            owned_storage_peak_bytes = processor.PeakOwnedCapacityBytes,
            owned_storage_scope = "retained, spare and incoming row/text array capacities; excludes array/object headers, abandoned arrays awaiting GC, parser/receive buffers and runtime memory",
            corpus_sha256 = corpus.Hash,
            corpus_frames = corpus.Frames.Length,
            resources = snapshot.Finish(),
            build = Metadata.Build()
        });
        return 0;
    }
}
