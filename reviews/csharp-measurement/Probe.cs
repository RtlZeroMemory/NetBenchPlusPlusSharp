using System.Reflection;
using System.Text.Json;

// Reflection reads the frozen DLL; this probe never references or builds its project.
var assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
var type = assembly.GetType("TcpBench.Histogram", throwOnError: true)!;
var histogram = Activator.CreateInstance(type, nonPublic: true)!;
var recordMethod = type.GetMethod("Record")!;
var export = type.GetMethod("Export")!;
var merge = type.GetMethod("Merge")!;
var clear = type.GetMethod("Clear")!;
const long overflow = 60_000_000_000;
long Upper(long ns)
{
    if (ns < 1024) return ns;
    int exponent = 0;
    for (long n = ns; (n >>= 1) != 0; ) exponent++;
    long width = 1L << (exponent - 8);
    return ns / width * width + width - 1;
}
JsonElement Snapshot(object h) => JsonSerializer.SerializeToElement(export.Invoke(h, null), new JsonSerializerOptions { IncludeFields = true });
void Check(bool value, string label)
{
    if (!value) throw new Exception(label);
}
var samples = new List<long> { 0, 1, 1023, 1024, 1027, 1028, 2047, 2048, 2055,
    59_999_999_999, 60_000_000_000, 60_000_000_001, long.MaxValue };
var random = new Random(42);
for (int i = 0; i < 100_000; i++) samples.Add(random.NextInt64(0, 65_000_000_000));
for (int exponent = 10; exponent <= 35; exponent++)
    for (long offset = -1; offset <= 1; offset++) samples.Add((1L << exponent) + offset);
foreach (long ns in samples) recordMethod.Invoke(histogram, [ns]);
var json = Snapshot(histogram);
Check(json.GetProperty("count").GetInt64() == samples.Count, "total count");
Check(json.GetProperty("max_ns").GetInt64() == long.MaxValue, "maximum");
Check(json.GetProperty("overflow_60s").GetInt64() == samples.Count(ns => ns >= overflow), "overflow count");
var expected = samples.Where(ns => ns < overflow).GroupBy(Upper).ToDictionary(g => g.Key, g => g.LongCount());
var actual = json.GetProperty("buckets").EnumerateArray().ToDictionary(b => b[0].GetInt64(), b => b[1].GetInt64());
Check(expected.Count == actual.Count && expected.All(p => actual.GetValueOrDefault(p.Key) == p.Value), "all bucket boundaries/counts");
samples.Sort();
foreach (var (name, q) in new[] { ("p50_ns", .5), ("p90_ns", .9), ("p99_ns", .99), ("p999_ns", .999) })
{
    long ranked = samples[(int)Math.Ceiling(samples.Count * q) - 1];
    Check(ranked >= overflow ? json.GetProperty(name).ValueKind == JsonValueKind.Null : json.GetProperty(name).GetInt64() == Upper(ranked), name);
}
var second = Activator.CreateInstance(type, nonPublic: true)!;
recordMethod.Invoke(second, [overflow]);
merge.Invoke(histogram, [second]);
Check(Snapshot(histogram).GetProperty("count").GetInt64() == samples.Count + 1, "merge includes overflow");
clear.Invoke(histogram, null);
var empty = Snapshot(histogram);
Check(empty.GetProperty("count").GetInt64() == 0 && empty.GetProperty("max_ns").GetInt64() == 0 && empty.GetProperty("p50_ns").ValueKind == JsonValueKind.Null, "clear and empty quantile");
Console.WriteLine(JsonSerializer.Serialize(new { frozen_binary = Path.GetFullPath(args[0]), histogram_samples = samples.Count, status = "passed" }));
