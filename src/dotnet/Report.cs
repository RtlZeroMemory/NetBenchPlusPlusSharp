using System.Diagnostics;
using System.Numerics;
using System.Runtime;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace TcpBench;

// Shared log-linear histogram: exact ns through 1023, then 256 buckets per power of two up
// to 2^36 ns. Values of at least 60 s count as overflow and have no finite bucket.
sealed class Histogram
{
    const int Bins = 1024 + 26 * 256;
    const long OverflowNs = 60_000_000_000;
    readonly long[] bins = new long[Bins];
    public long Count, Max, Overflow;

    public static long Nanoseconds(long ticks) =>
        ticks <= 0 ? 0 : (long)((Int128)ticks * 1_000_000_000 / Stopwatch.Frequency);

    public static int Index(long ns)
    {
        if (ns < 1024)
        {
            return (int)ns;
        }

        int e = BitOperations.Log2((ulong)ns);
        return 1024 + (e - 10) * 256 + (int)(ns >> (e - 8)) - 256;
    }

    public static long Upper(int index) =>
        index < 1024 ? index : ((257L + (index - 1024) % 256) << ((index - 1024) / 256 + 2)) - 1;

    public void RecordTicks(long ticks) => Record(Nanoseconds(ticks));

    public void Record(long ns)
    {
        Count++;
        Max = Math.Max(Max, ns);
        if (ns >= OverflowNs)
        {
            Overflow++;
        }
        else
        {
            bins[Index(ns)]++;
        }
    }

    public void Merge(Histogram other)
    {
        Count += other.Count;
        Overflow += other.Overflow;
        Max = Math.Max(Max, other.Max);
        for (int i = 0; i < Bins; i++)
        {
            bins[i] += other.bins[i];
        }
    }

    public void Clear()
    {
        Array.Clear(bins);
        Count = Max = Overflow = 0;
    }

    // Bucket upper bound at the ceil(q*count) rank; null when empty or the rank is in overflow.
    public long? Quantile(double q)
    {
        long target = (long)Math.Ceiling(q * Count), seen = 0;
        for (int i = 0; Count > 0 && i < Bins; i++)
        {
            if ((seen += bins[i]) >= target)
            {
                return Upper(i);
            }
        }

        return null;
    }

    // Stage histograms suppress p99 below 10,000 and p99.9 below 1,000,000 samples.
    public object Export(bool stage = false) => new
    {
        count = Count,
        p50_ns = Quantile(.5),
        p90_ns = Quantile(.9),
        p99_ns = stage && Count < 10_000 ? null : Quantile(.99),
        p999_ns = stage && Count < 1_000_000 ? null : Quantile(.999),
        max_ns = Max,
        overflow_60s = Overflow,
        buckets = Enumerable.Range(0, Bins).Where(i => bins[i] != 0).Select(i => new[] { Upper(i), bins[i] }).ToArray()
    };
}

static class JsonOutput
{
    public static void Emit(string path, object result)
    {
        string json = JsonSerializer.Serialize(result);
        if (path.Length > 0)
        {
            File.WriteAllText(path, json + "\n");
        }

        Console.WriteLine(json);
        Console.Out.Flush();
    }
}

// Process resource snapshot; Delta() reports the interval between two snapshots.
readonly record struct Resources(long Timestamp, double CpuSeconds, long Allocated, int Gen0, int Gen1, int Gen2, TimeSpan Pause)
{
    static readonly Process Self = Process.GetCurrentProcess();

    public static Resources Sample() => new(
        Stopwatch.GetTimestamp(),
        Self.TotalProcessorTime.TotalSeconds,
        GC.GetTotalAllocatedBytes(precise: true),
        GC.CollectionCount(0),
        GC.CollectionCount(1),
        GC.CollectionCount(2),
        GC.GetTotalPauseDuration());

    public static long PeakWorkingSet()
    {
        Self.Refresh();
        return Self.PeakWorkingSet64;
    }

    public double Seconds(Resources before) => (double)(Timestamp - before.Timestamp) / Stopwatch.Frequency;

    public object Gc(Resources before) => new
    {
        gen0 = Gen0 - before.Gen0,
        gen1 = Gen1 - before.Gen1,
        gen2 = Gen2 - before.Gen2,
        pause_seconds = (Pause - before.Pause).TotalSeconds
    };
}

static class Build
{
    public static object Info(bool fastParser = false) => new
    {
        runtime = RuntimeInformation.FrameworkDescription,
#if DEBUG
        configuration = "Debug",
#else
        configuration = "Release",
#endif
        parser = fastParser ? "FastJson (schema-specific, SIMD scanning)" : "System.Text.Json Utf8JsonReader",
        parser_kernel = fastParser ? (System.Runtime.Intrinsics.Vector512.IsHardwareAccelerated ? "avx512" : "avx2") : "n/a",
        server_gc = GCSettings.IsServerGC,
        gc_concurrent = AppContext.GetData("System.GC.Concurrent")?.ToString(),
        gc_heap_count = GC.GetConfigurationVariables().GetValueOrDefault("HeapCount"),
        gc_dynamic_adaptation = GC.GetConfigurationVariables().GetValueOrDefault("GCDynamicAdaptationMode"),
        tiered_pgo = Environment.GetEnvironmentVariable("DOTNET_TieredPGO") ?? "runtime default",
        processor_count = Environment.ProcessorCount,
        qpc_frequency = Stopwatch.Frequency,
        affinity_mask = (long)Process.GetCurrentProcess().ProcessorAffinity,
        sanitized = false
    };
}
