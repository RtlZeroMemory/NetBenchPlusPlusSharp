using System.Buffers.Binary;
using System.Globalization;
using System.Net.Sockets;

[assembly: System.Runtime.Versioning.SupportedOSPlatform("windows")]

namespace TcpBench;

sealed class Options
{
    public const int HardLimit = 16 * 1024 * 1024;
    public string Command = "", Corpus = "", Output = "", Arrival = "steady";
    public Mode Mode = Mode.Aggregate;
    public int Port = 9000, MaxConnections = 32, Workers = 4, Connections = 4, Window = 8;
    public int IdleMs = 5000, FrameMs = 30000, SocketBuffer = 262144, PauseEvery, PauseMs, IoCap, ControlStdin;
    public int MaxFrame = 1048576, RetainBatches = 8;
    public long RetainBytes = 67108864, InflightBytes = 67108864;
    public double Duration = 10, Warmup = 2, Rate, DrainSeconds = 30;
    public ulong Seed = 42;
    public byte[] Manifest = new byte[32];

    static readonly Dictionary<string, string> Allowed = new()
    {
        ["server"] = " port mode max-frame max-connections workers manifest-hash idle-timeout-ms frame-timeout-ms " +
            "retain-batches retain-bytes socket-buffer output pause-every pause-ms io-cap control-stdin ",
        ["client"] = " port corpus mode connections duration warmup window inflight-bytes rate arrival seed " +
            "manifest-hash socket-buffer drain-seconds output io-cap ",
        ["process"] = " corpus mode duration warmup output retain-batches retain-bytes ",
        ["selftest"] = " corpus "
    };

    public static Options Parse(string[] args)
    {
        if (args.Length == 0 || !Allowed.TryGetValue(args[0], out string? allowed))
        {
            throw new BenchException("usage: Bench server|client|process|selftest [--name value]");
        }

        var o = new Options { Command = args[0] };
        var values = new Dictionary<string, string>();
        for (int i = 1; i < args.Length; i += 2)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
            {
                throw new BenchException("options require --name value");
            }

            string name = args[i][2..];
            if (!allowed.Split(' ', StringSplitOptions.RemoveEmptyEntries).Contains(name) || !values.TryAdd(name, args[i + 1]))
            {
                throw new BenchException("unknown or duplicate option: " + name);
            }
        }

        double Number(string name, double current, double min, double max, bool integer = true)
        {
            if (!values.TryGetValue(name, out string? text))
            {
                return current;
            }

            NumberStyles style = integer ? NumberStyles.None : NumberStyles.AllowDecimalPoint | NumberStyles.AllowExponent;
            return double.TryParse(text, style, CultureInfo.InvariantCulture, out double v) && double.IsFinite(v) && v >= min && v <= max
                ? v
                : throw new BenchException("invalid --" + name);
        }

        string Text(string name, string current) => values.GetValueOrDefault(name, current);

        o.Mode = Text("mode", "aggregate") switch
        {
            "aggregate" => Mode.Aggregate,
            "retain-reuse" => Mode.RetainReuse,
            "retain-allocate" => Mode.RetainAllocate,
            "transport" => Mode.Transport,
            _ => throw new BenchException("invalid mode")
        };
        o.Corpus = Text("corpus", "");
        o.Output = Text("output", "");
        o.Arrival = Text("arrival", "steady");
        o.Port = (int)Number("port", o.Port, 1, 65535);
        o.MaxFrame = (int)Number("max-frame", o.MaxFrame, 1, HardLimit);
        o.MaxConnections = (int)Number("max-connections", o.MaxConnections, 1, 1024);
        o.Workers = (int)Number("workers", o.Workers, 1, 256);
        o.IdleMs = (int)Number("idle-timeout-ms", o.IdleMs, 1, 3_600_000);
        o.FrameMs = (int)Number("frame-timeout-ms", o.FrameMs, 1, 3_600_000);
        o.RetainBatches = (int)Number("retain-batches", o.RetainBatches, 1, 65536);
        o.RetainBytes = (long)Number("retain-bytes", o.RetainBytes, 1, 2147483648);
        o.SocketBuffer = (int)Number("socket-buffer", o.SocketBuffer, 0, 16777216);
        o.Connections = (int)Number("connections", o.Connections, 1, 1024);
        o.Window = (int)Number("window", o.Window, 1, 1048576);
        o.InflightBytes = (long)Number("inflight-bytes", o.InflightBytes, 1, 2147483648);
        o.Duration = Number("duration", o.Duration, 0.001, 86400, false);
        o.Warmup = Number("warmup", o.Warmup, 0, 86400, false);
        o.Rate = Number("rate", o.Rate, 0, 1e9, false);
        o.DrainSeconds = Number("drain-seconds", o.DrainSeconds, 0.001, 86400, false);
        o.PauseEvery = (int)Number("pause-every", o.PauseEvery, 0, int.MaxValue);
        o.PauseMs = (int)Number("pause-ms", o.PauseMs, 0, 60000);
        o.IoCap = (int)Number("io-cap", o.IoCap, 0, HardLimit);
        o.ControlStdin = (int)Number("control-stdin", o.ControlStdin, 0, 1);
        if (values.TryGetValue("seed", out string? seed) && !ulong.TryParse(seed, NumberStyles.None, CultureInfo.InvariantCulture, out o.Seed))
        {
            throw new BenchException("invalid --seed");
        }

        string manifest = Text("manifest-hash", new string('0', 64));
        if (manifest.Length != 64 || !manifest.All(Uri.IsHexDigit))
        {
            throw new BenchException("manifest hash must have 64 hex digits");
        }

        o.Manifest = Convert.FromHexString(manifest);
        if (o.Arrival is not ("steady" or "poisson" or "burst"))
        {
            throw new BenchException("invalid arrival");
        }

        if (o.SocketBuffer is > 0 and < 1024)
        {
            throw new BenchException("invalid --socket-buffer");
        }

        if ((o.PauseEvery == 0) != (o.PauseMs == 0))
        {
            throw new BenchException("pause-every and pause-ms must both be positive or both zero");
        }

        if (o.Command is "client" or "process" && o.Corpus.Length == 0)
        {
            throw new BenchException("--corpus required");
        }

        if (o.Command == "client" && o.Rate == 0 && o.Window % o.Connections != 0)
        {
            throw new BenchException("closed loop requires window to be a multiple of connections");
        }

        if (o.Command == "server" && ((long)o.MaxFrame * o.MaxConnections > 1L << 30 ||
            o.Mode is Mode.RetainReuse or Mode.RetainAllocate && o.RetainBytes * o.MaxConnections > 1L << 31))
        {
            throw new BenchException("scenario exceeds application memory envelope");
        }

        return o;
    }

    public static string Name(Mode mode) => mode switch
    {
        Mode.Aggregate => "aggregate",
        Mode.RetainReuse => "retain-reuse",
        Mode.RetainAllocate => "retain-allocate",
        _ => "transport"
    };

    public static void Header(Span<byte> h, int length, ushort type, ulong sequence)
    {
        BinaryPrimitives.WriteUInt32BigEndian(h, (uint)length);
        BinaryPrimitives.WriteUInt16BigEndian(h[4..], 1);
        BinaryPrimitives.WriteUInt16BigEndian(h[6..], type);
        BinaryPrimitives.WriteUInt64BigEndian(h[8..], sequence);
    }

    // TCP_NODELAY always; buffer sizes only when positive (0 keeps Windows autotuning).
    public void Configure(Socket socket)
    {
        socket.NoDelay = true;
        if (SocketBuffer > 0)
        {
            socket.SendBufferSize = SocketBuffer;
            socket.ReceiveBufferSize = SocketBuffer;
        }
    }
}

static class Program
{
    public static async Task<int> Main(string[] args)
    {
        try
        {
            Options o = Options.Parse(args);
            return o.Command switch
            {
                "server" => await Server.Run(o),
                "client" => await Client.Run(o),
                "process" => ProcessControl.Run(o),
                _ => SelfTest.Run(o)
            };
        }
        catch (Exception e) when (e is BenchException or IOException or SocketException or UnauthorizedAccessException or OperationCanceledException)
        {
            // Also replace any stale --output file, so a failed run never leaves an old result.
            int at = Array.IndexOf(args, "--output");
            string output = at > 0 && at + 1 < args.Length ? args[at + 1] : "";
            JsonOutput.Emit(output, new { @event = "error", valid = false, error = e.Message });
            return 2;
        }
    }
}
