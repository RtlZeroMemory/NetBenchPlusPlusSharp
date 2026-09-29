using System.Collections;
using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

class Probe
{
    delegate (ulong Records, ulong Hash) Process<T>(ReadOnlySpan<byte> input, ulong sequence, bool sample, T budget);
    const BindingFlags Flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    static Type processor = null!, budget = null!;
    static object Field(object value, string name) => value.GetType().GetField(name, Flags)!.GetValue(value)!;
    static object Property(object value, string name) => value.GetType().GetProperty(name, Flags)!.GetValue(value)!;
    static object[] Retained(object value) => ((IEnumerable)Field(value, "retained")).Cast<object>().ToArray();
    static string Summary(object value)
    {
        object epoch = Field(value, "Epoch");
        return JsonSerializer.Serialize(epoch.GetType().GetFields(Flags).ToDictionary(f => f.Name, f => f.GetValue(epoch)));
    }
    static void Require(bool okay, string message) { if (!okay) throw new Exception(message); }
    static byte[] Rows(int count) => Encoding.UTF8.GetBytes("[" + string.Join(',', Enumerable.Repeat("{\"id\":1,\"timestamp_ns\":2,\"source\":3,\"kind\":\"kind00\",\"value_milli\":4,\"flags\":0,\"message\":\"x\"}", count)) + "]");
    static object[] Run<T>()
    {
        var results = new List<object>();
        byte[] small = Rows(1), big = Rows(170000), incoming = Rows(2);
        foreach (string mode in new[] { "retain-reuse", "retain-allocate" })
        {
            object value = Activator.CreateInstance(processor, mode, 2, 6_630_040L)!;
            var process = (Process<T>)processor.GetMethod("Process")!.CreateDelegate(typeof(Process<T>), value);
            process(small, 1, false, default!);
            process(big, 2, false, default!);
            // Warm the retained verification path before the timed cancellation probe.
            processor.GetMethod("VerifyRetained")!.Invoke(value, new object?[] { default(T) });
            object[] before = Retained(value);
            string summary = Summary(value);
            long bytes = (long)Property(value, "RetainedBytes");
            using var cancellation = new CancellationTokenSource();
            T limited = (T)Activator.CreateInstance(budget, 0L, cancellation.Token)!;
            using var begin = new ManualResetEventSlim();
            using var ready = new ManualResetEventSlim();
            var cancelThread = new Thread(() =>
            {
                ready.Set();
                begin.Wait();
                long until = Stopwatch.GetTimestamp() + Stopwatch.Frequency / 1000;
                while (Stopwatch.GetTimestamp() < until) Thread.SpinWait(16);
                cancellation.Cancel();
            });
            cancelThread.Start();
            ready.Wait();
            long start = Stopwatch.GetTimestamp();
            bool rejected = false;
            begin.Set();
            try { process(incoming, 3, false, limited); }
            catch (OperationCanceledException) { rejected = true; }
            finally { cancelThread.Join(); }
            double elapsed = Stopwatch.GetElapsedTime(start).TotalMilliseconds;
            Require(rejected, "retained verification completed before cancellation; increase old workload");
            Require(((ulong[])Field(value, "counts")).Sum(n => (long)n) == 2, "cancellation occurred before staged visit; repeat probe");
            Require(Retained(value).SequenceEqual(before), "cancelled eviction changed retained identity/order");
            Require(Summary(value) == summary && (long)Property(value, "RetainedBytes") == bytes, "cancelled eviction committed state");
            processor.GetMethod("VerifyRetained")!.Invoke(value, new object?[] { default(T) });
            process(incoming, 3, false, default!);
            Require(Retained(value).Length == 1 && (long)Property(value, "RetainedBytes") == 78, "retry did not commit both validated evictions");
            results.Add(new { mode, cancellation_ms = elapsed, retained_before = before.Length, retained_after_cancel = before.Length, committed_retry_bytes = 78 });
        }
        return results.ToArray();
    }
    static void Main(string[] args)
    {
        string hash = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(args[0])));
        Require(hash == "cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd", "unexpected DLL hash");
        var assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
        processor = assembly.GetType("TcpBench.Processor")!;
        budget = assembly.GetType("TcpBench.ProcessingBudget")!;
        object cases = typeof(Probe).GetMethod("Run", BindingFlags.Static | BindingFlags.NonPublic)!.MakeGenericMethod(budget).Invoke(null, null)!;
        Console.WriteLine(JsonSerializer.Serialize(new { binary_sha256 = hash, cases }));
    }
}
