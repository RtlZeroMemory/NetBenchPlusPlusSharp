using System.Diagnostics;
using System.Numerics;
using System.Runtime;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

namespace TcpBench;

sealed class Histogram
{
    readonly long[] bins = new long[7680];
    public long Count, Max, Overflow;
    public void Clear()
    {
        Array.Clear(bins);
        Count = Max = Overflow = 0;
    }

    public static long Nanoseconds(long ticks) => (long)((Int128)Math.Max(0, ticks) * 1_000_000_000 / Stopwatch.Frequency);
    public void RecordTicks(long ticks) => Record(Nanoseconds(ticks));
    public void Record(long ns)
    {
        if (ns < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(ns));
        }

        Count++;
        Max = Math.Max(Max, ns);
        if (ns >= 60_000_000_000)
        {
            Overflow++;
            return;
        }

        int index;
        if (ns < 1024)
        {
            index = (int)ns;
        }
        else
        {
            int exponent = BitOperations.Log2((ulong)ns);
            index = 1024 + (exponent - 10) * 256 + (int)(ns >> (exponent - 8)) - 256;
        }

        bins[index]++;
    }

    public static long Upper(int index)
    {
        if (index < 1024)
        {
            return index;
        }

        int exponent = 10 + (index - 1024) / 256, part = (index - 1024) % 256;
        return ((257L + part) << (exponent - 8)) - 1;
    }

    public void Merge(Histogram other)
    {
        Count += other.Count;
        Overflow += other.Overflow;
        Max = Math.Max(Max, other.Max);
        for (int i = 0; i < bins.Length; i++)
        {
            bins[i] += other.bins[i];
        }
    }

    long? Quantile(double q)
    {
        if (Count == 0)
        {
            return null;
        }

        long target = (long)Math.Ceiling(Count * q), sum = 0;
        for (int i = 0; i < bins.Length; i++)
        {
            if ((sum += bins[i]) >= target)
            {
                return Upper(i);
            }
        }

        return null;
    }

    public object Export() => ExportCore(false);
    public object ExportStages() => ExportCore(true);
    object ExportCore(bool stages)
    {
        long? p50 = Quantile(.5), p90 = Quantile(.9);
        long? p99 = stages && Count < 10_000 ? null : Quantile(.99);
        long? p999 = stages && Count < 1_000_000 ? null : Quantile(.999);
        return new
        {
            count = Count,
            p50_ns = p50,
            p90_ns = p90,
            p99_ns = p99,
            p999_ns = p999,
            max_ns = Max,
            p50,
            p90,
            p99,
            p999,
            max = Max,
            overflow_60s = Overflow,
            buckets = bins.Select((count, index) => (count, index)).Where(b => b.count != 0).Select(b => new long[] { Upper(b.index), b.count }).ToArray(),
            schema = "exact ns 0..1023; 256 sub-buckets per power of two; upper bounds; >=60s counted separately"

        };
    }
}

static class JsonOutput
{
    static readonly JsonSerializerOptions Settings = new()
    {
        IncludeFields = true
    };
    public static void Print(object result)
    {
        Console.WriteLine(JsonSerializer.Serialize(result, Settings));
        Console.Out.Flush();
    }

    public static void Save(string path, object result)
    {
        string json = JsonSerializer.Serialize(result, Settings);
        if (path.Length > 0)
        {
            string full = Path.GetFullPath(path);
            Directory.CreateDirectory(Path.GetDirectoryName(full)!);
            File.WriteAllText(full, json + "\n");
        }

        Console.WriteLine(json);
        Console.Out.Flush();
    }
}

static class Metadata
{
#if DEBUG
    const string Configuration = "Debug";
#else
    const string Configuration = "Release";
#endif
    public static object Build() => new
    {
        framework = RuntimeInformation.FrameworkDescription,
        runtime = Environment.Version.ToString(),
        architecture = RuntimeInformation.ProcessArchitecture.ToString(),
        os = RuntimeInformation.OSDescription,
        processor_count = Environment.ProcessorCount,
        server_gc = GCSettings.IsServerGC,
        gc_latency_mode = GCSettings.LatencyMode.ToString(),
        gc_heap_count = GC.GetConfigurationVariables().GetValueOrDefault("HeapCount"),
        clock_frequency = Stopwatch.Frequency,
        sdk_pin = "11.0.100-preview.7.26381.103",
        configuration = Configuration,
        executable_sha256 = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(typeof(Program).Assembly.Location))),
        tiered_compilation = Environment.GetEnvironmentVariable("DOTNET_TieredCompilation") ?? "runtime default",
        dynamic_pgo = Environment.GetEnvironmentVariable("DOTNET_TieredPGO") ?? "runtime default",
        async_policy = "Socket Memory async APIs; runtime defaults",
        retention_layout = "owned typed row arrays and UTF-8 text arrays; allocate uses fresh arrays, reuse recycles evicted arrays"

    };
}

sealed class Resources
{
    readonly Process process = Process.GetCurrentProcess();
    readonly TimeSpan cpu;
    readonly long allocated = GC.GetTotalAllocatedBytes(false), start = Stopwatch.GetTimestamp();
    readonly int[] collections = [GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2)];
    public Resources()
    {
        cpu = process.TotalProcessorTime;
    }

    public object Finish()
    {
        process.Refresh();
        var memory = GC.GetGCMemoryInfo();
        return new
        {
            elapsed_seconds = Stopwatch.GetElapsedTime(start).TotalSeconds,
            cpu_seconds = (process.TotalProcessorTime - cpu).TotalSeconds,
            allocated_bytes = GC.GetTotalAllocatedBytes(false) - allocated,
            gen0 = GC.CollectionCount(0) - collections[0],
            gen1 = GC.CollectionCount(1) - collections[1],
            gen2 = GC.CollectionCount(2) - collections[2],
            private_bytes = process.PrivateMemorySize64,
            working_set = process.WorkingSet64,
            peak_working_set = process.PeakWorkingSet64,
            threads = process.Threads.Count,
            managed_heap_bytes = memory.HeapSizeBytes,
            managed_committed_bytes = memory.TotalCommittedBytes,
            fragmented_bytes = memory.FragmentedBytes,
            gc_pause_time_percentage = memory.PauseTimePercentage,
            gc_pause_durations_seconds = memory.PauseDurations.ToArray().Select(t => t.TotalSeconds).ToArray(),
            loh_size_after_bytes = memory.GenerationInfo.Length > 3 ? memory.GenerationInfo[3].SizeAfterBytes : 0

        };
    }
}
