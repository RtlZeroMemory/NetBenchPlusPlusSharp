using System.Globalization;
using System.Net.Sockets;

namespace TcpBench;

sealed class Options
{
    public string Command = "";
    public string Mode = "aggregate";
    public string Corpus = "";
    public string Output = "";
    public string Arrival = "steady";
    public int Port = 9000;
    public int MaxFrame = 1048576;
    public int MaxConnections = 32;
    public int Workers = 4;
    public int IdleMs = 5000;
    public int FrameMs = 30000;
    public int RetainBatches = 8;
    public int SocketBuffer = 262144;
    public int Connections = 4;
    public int Window = 8;
    public int PauseEvery;
    public int PauseMs;
    public int ControlStdin;
    public int IoCap = int.MaxValue;
    public long RetainBytes = 67108864, InflightBytes = 67108864;
    public double Duration = 10;
    public double Warmup = 2;
    public double Rate;
    public double DrainSeconds = 30;
    public double RunSeconds;
    public ulong Seed = 42;
    public byte[] Manifest = new byte[32];
    public static Options Parse(string[] args)
    {
        if (args.Length == 0)
        {
            throw new ArgumentException("usage: Bench server|client|process|selftest [--name value]");
        }

        var o = new Options
        {
            Command = args[0]
        };
        string allowed = o.Command switch
        {
            "server" => "port mode max-frame max-connections workers manifest-hash idle-timeout-ms frame-timeout-ms " +
                "retain-batches retain-bytes socket-buffer output run-seconds pause-every pause-ms io-cap control-stdin",
            "client" => "port corpus mode connections duration warmup window inflight-bytes rate arrival seed " +
                "manifest-hash socket-buffer drain-seconds output io-cap",
            "process" => "corpus mode duration warmup output retain-batches retain-bytes",
            "selftest" => "corpus",
            _ => throw new ArgumentException("unknown command")
        };
        var names = allowed.Split(' ').ToHashSet(StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (int i = 1; i < args.Length; i += 2)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
            {
                throw new ArgumentException("options require --name value");
            }

            string name = args[i][2..], value = args[i + 1];
            if (!names.Contains(name) || !seen.Add(name))
            {
                throw new ArgumentException("unknown or duplicate option: " + name);
            }

            int Int(int min, int max) =>
                int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out int v) && v >= min && v <= max
                    ? v : throw new ArgumentException("invalid --" + name);
            long Long(long min, long max) =>
                long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out long v) && v >= min && v <= max
                    ? v : throw new ArgumentException("invalid --" + name);
            double Real(double min, double max) =>
                double.TryParse(value, NumberStyles.AllowDecimalPoint | NumberStyles.AllowExponent, CultureInfo.InvariantCulture, out double v)
                    && double.IsFinite(v) && v >= min && v <= max
                    ? v : throw new ArgumentException("invalid --" + name);
            switch (name)
            {
                case "port":
                    o.Port = Int(1, 65535);
                    break;
                case "mode":
                    o.Mode = value;
                    break;
                case "corpus":
                    o.Corpus = value;
                    break;
                case "output":
                    o.Output = value;
                    break;
                case "arrival":
                    o.Arrival = value;
                    break;
                case "max-frame":
                    o.MaxFrame = Int(1, Wire.HardLimit);
                    break;
                case "max-connections":
                    o.MaxConnections = Int(1, 1024);
                    break;
                case "workers":
                    o.Workers = Int(1, 1024);
                    break;
                case "idle-timeout-ms":
                    o.IdleMs = Int(1, 3600000);
                    break;
                case "frame-timeout-ms":
                    o.FrameMs = Int(1, 3600000);
                    break;
                case "retain-batches":
                    o.RetainBatches = Int(1, 65536);
                    break;
                case "retain-bytes":
                    o.RetainBytes = Long(1, int.MaxValue);
                    break;
                case "socket-buffer":
                    o.SocketBuffer = Int(1024, 16777216);
                    break;
                case "connections":
                    o.Connections = Int(1, 1024);
                    break;
                case "window":
                    o.Window = Int(1, 1048576);
                    break;
                case "inflight-bytes":
                    o.InflightBytes = Long(1, 2147483648);
                    break;
                case "duration":
                    o.Duration = Real(0.001, 86400);
                    break;
                case "warmup":
                    o.Warmup = Real(0, 86400);
                    break;
                case "rate":
                    o.Rate = Real(0, 1000000000);
                    break;
                case "drain-seconds":
                    o.DrainSeconds = Real(0.001, 86400);
                    break;
                case "run-seconds":
                    o.RunSeconds = Real(0, 86400);
                    break;
                case "pause-every":
                    o.PauseEvery = Int(0, int.MaxValue);
                    break;
                case "pause-ms":
                    o.PauseMs = Int(0, 3600000);
                    break;
                case "io-cap":
                    o.IoCap = Int(1, Wire.HardLimit);
                    break;
                case "control-stdin":
                    o.ControlStdin = Int(0, 1);
                    break;
                case "seed":
                    o.Seed = ulong.Parse(value, NumberStyles.None, CultureInfo.InvariantCulture);
                    break;
                case "manifest-hash":
                    if (value.Length != 64)
                    {
                        throw new ArgumentException("manifest hash must have 64 hex digits");
                    }

                    o.Manifest = Convert.FromHexString(value);
                    break;
            }
        }

        if (o.Mode is not ("aggregate" or "retain-reuse" or "retain-allocate" or "transport"))
        {
            throw new ArgumentException("invalid mode");
        }

        if (o.Arrival is not ("steady" or "poisson" or "burst"))
        {
            throw new ArgumentException("invalid arrival");
        }

        if (o.Command is "client" or "process" && o.Corpus.Length == 0)
        {
            throw new ArgumentException("--corpus required");
        }

        if (o.Command == "server" &&
            ((long)o.MaxFrame * o.MaxConnections > 1073741824 ||
            o.Mode.StartsWith("retain", StringComparison.Ordinal) && o.RetainBytes * o.MaxConnections > 2147483648))
        {
            throw new ArgumentException("scenario exceeds application memory envelope");
        }

        if ((o.PauseEvery == 0) != (o.PauseMs == 0))
        {
            throw new ArgumentException("pause-every and pause-ms must both be positive or both zero");
        }

        return o;
    }

    public void Configure(Socket socket)
    {
        socket.NoDelay = true;
        socket.SendBufferSize = SocketBuffer;
        socket.ReceiveBufferSize = SocketBuffer;
    }
}

static class Program
{
    public static async Task<int> Main(string[] args)
    {
        try
        {
            Options options = Options.Parse(args);
            return options.Command switch
            {
                "server" => await Server.Run(options),
                "client" => await Load.Run(options),
                "process" => ProcessControl.Run(options),
                "selftest" => SelfTest.Run(options),
                _ => 2
            };
        }
        catch (Exception e)
        {
            JsonOutput.Print(new
            {
                @event = "error",
                valid = false,
                error = e.Message,
                error_type = e.GetType().Name
            });
            return 2;
        }
    }
}
