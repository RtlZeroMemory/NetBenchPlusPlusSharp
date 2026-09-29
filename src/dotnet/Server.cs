using System.Buffers.Binary;
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace TcpBench;

sealed class Totals
{
    public long Frames, Records, Bytes, ReceiveCalls, SendCalls, Received, Sent;

    public void Add(Totals t)
    {
        Frames += t.Frames;
        Records += t.Records;
        Bytes += t.Bytes;
        ReceiveCalls += t.ReceiveCalls;
        SendCalls += t.SendCalls;
        Received += t.Received;
        Sent += t.Sent;
    }
}

sealed class Stages
{
    public readonly Histogram Receive = new(), Processing = new(), Decode = new(), Visit = new();

    public void Clear()
    {
        Receive.Clear();
        Processing.Clear();
        Decode.Clear();
        Visit.Clear();
    }

    public void Merge(Stages s)
    {
        Receive.Merge(s.Receive);
        Processing.Merge(s.Processing);
        Decode.Merge(s.Decode);
        Visit.Merge(s.Visit);
    }
}

// Server-wide state. The measured interval runs from the first Begin to the last End across
// all connections, so its resource counters and processed-work denominators share one scope.
sealed class Server
{
    public readonly Options O;
    public readonly CancellationTokenSource Stop = new();
    readonly object gate = new(); // guards everything below
    readonly List<ServerConnection> active = [];
    readonly Dictionary<string, long> closed = [];
    readonly Totals lifetime = new(), intervalWork = new();
    readonly Stages stages = new();
    long accepted, rejected, acceptErrors, graceful, peakRetained, peakOwned;
    int sendBuffer, receiveBuffer, activeEpochs;
    bool intervalComplete;
    Resources intervalBefore;
    object? measured;

    Server(Options o) => O = o;

    public static Task<int> Run(Options o) => new Server(o).Serve();

    public void BeginEpoch()
    {
        lock (gate)
        {
            if (activeEpochs++ == 0)
            {
                intervalBefore = Resources.Sample();
                intervalWork.Frames = intervalWork.Records = intervalWork.Bytes = 0;
                intervalComplete = true;
            }
        }
    }

    // A null summary means the connection closed inside its epoch.
    public void EndEpoch(Summary? epoch)
    {
        lock (gate)
        {
            if (epoch == null)
            {
                intervalComplete = false;
            }
            else
            {
                intervalWork.Frames += (long)epoch.Batches;
                intervalWork.Records += (long)epoch.Records;
                intervalWork.Bytes += (long)epoch.Bytes;
            }

            if (--activeEpochs > 0)
            {
                return;
            }

            Resources after = Resources.Sample();
            measured = new
            {
                complete = intervalComplete,
                seconds = after.Seconds(intervalBefore),
                frames = intervalWork.Frames,
                records = intervalWork.Records,
                payload_bytes = intervalWork.Bytes,
                cpu_seconds = after.CpuSeconds - intervalBefore.CpuSeconds,
                allocations = (long?)null,
                allocated_bytes = after.Allocated - intervalBefore.Allocated,
                gc = after.Gc(intervalBefore),
                peak_working_set_bytes = Resources.PeakWorkingSet()
            };
        }
    }

    public void RecordBuffers(Socket socket)
    {
        lock (gate)
        {
            sendBuffer = socket.SendBufferSize;
            receiveBuffer = socket.ReceiveBufferSize;
        }
    }

    public void Finish(ServerConnection c, string reason)
    {
        lock (gate)
        {
            active.Remove(c);
            stages.Merge(c.Stages);
            lifetime.Add(c.Totals);
            peakRetained = Math.Max(peakRetained, c.Processor.PeakRetained);
            peakOwned = Math.Max(peakOwned, c.Processor.PeakOwned);
            if (reason == "graceful")
            {
                graceful++;
            }
            else
            {
                closed[reason] = closed.GetValueOrDefault(reason) + 1;
            }
        }
    }

    async Task<int> Serve()
    {
        long started = Stopwatch.GetTimestamp();
        ConsoleCancelEventHandler interrupt = (_, e) =>
        {
            e.Cancel = true;
            Stop.Cancel();
        };
        Console.CancelKeyPress += interrupt;
        if (O.ControlStdin == 1)
        {
            _ = Task.Run(async () =>
            {
                string? line;
                while ((line = await Console.In.ReadLineAsync()) != null && line.Trim() != "stop")
                {
                }

                Stop.Cancel();
            });
        }

        using var listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        listener.ExclusiveAddressUse = true;
        listener.Bind(new IPEndPoint(IPAddress.Loopback, O.Port));
        listener.Listen(512);
        JsonOutput.Emit("", new { @event = "ready", port = O.Port, implementation = "csharp" });
        Task scanner = Task.Run(Scan);
        var connections = new List<Task>();
        while (true)
        {
            Socket socket;
            try
            {
                socket = await listener.AcceptAsync(Stop.Token);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (SocketException)
            {
                lock (gate)
                {
                    acceptErrors++; // e.g. a peer reset before the accept completed
                }

                continue;
            }

            ServerConnection? c = null;
            lock (gate)
            {
                // Checked under the scanner's lock: nothing is admitted after it sees stop.
                if (Stop.IsCancellationRequested || active.Count >= O.MaxConnections)
                {
                    rejected++;
                }
                else
                {
                    accepted++;
                    c = new ServerConnection(this, socket);
                    active.Add(c);
                }
            }

            if (c == null)
            {
                socket.Dispose();
                continue;
            }

            connections.RemoveAll(t => t.IsCompleted);
            connections.Add(Task.Run(c.Run)); // a pool worker, not the accept loop, runs it
        }

        await scanner;
        await Task.WhenAll(connections);
        Console.CancelKeyPress -= interrupt;
        Resources end = Resources.Sample();
        JsonOutput.Emit(O.Output, new
        {
            role = "server",
            implementation = "csharp",
            mode = Options.Name(O.Mode),
            valid = true,
            error = (string?)null,
            config = new
            {
                max_frame = O.MaxFrame,
                max_connections = O.MaxConnections,
                workers = O.Workers,
                retain_batches = O.RetainBatches,
                retain_bytes = O.RetainBytes,
                socket_buffer = O.SocketBuffer,
                frame_timeout_ms = O.FrameMs,
                idle_timeout_ms = O.IdleMs,
                pause_every = O.PauseEvery,
                pause_ms = O.PauseMs,
                io_cap = O.IoCap,
                worker_policy = "runtime thread pool; --workers is recorded, the CPU mask is the budget"
            },
            connections = new { accepted, rejected, accept_errors = acceptErrors, graceful, closed },
            lifetime = new
            {
                seconds = Stopwatch.GetElapsedTime(started).TotalSeconds,
                frames = lifetime.Frames,
                records = lifetime.Records,
                payload_bytes = lifetime.Bytes,
                cpu_seconds = end.CpuSeconds,
                peak_working_set_bytes = Resources.PeakWorkingSet()
            },
            measured,
            retention = new
            {
                canonical_peak_per_connection = peakRetained,
                owned_capacity_peak_per_connection = peakOwned
            },
            io = new
            {
                receive_calls = lifetime.ReceiveCalls,
                send_calls = lifetime.SendCalls,
                received_bytes = lifetime.Received,
                sent_bytes = lifetime.Sent,
                inline_completions = true,
                socket_send_buffer = sendBuffer,
                socket_receive_buffer = receiveBuffer
            },
            stages = new
            {
                sample_every = ServerConnection.SampleEvery,
                receive = stages.Receive.Export(true),
                processing = stages.Processing.Export(true),
                decode = stages.Decode.Export(true),
                retained_visit = stages.Visit.Export(true)
            },
            build = Build.Info()
        });
        return 0;
    }

    // Deadline scanner, as in the native server: expired or stopping connections are closed.
    async Task Scan()
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(10));
        while (true)
        {
            ServerConnection[] snapshot;
            bool stopping;
            lock (gate)
            {
                stopping = Stop.IsCancellationRequested;
                snapshot = [.. active];
            }

            if (stopping && snapshot.Length == 0)
            {
                return;
            }

            foreach (ServerConnection c in snapshot)
            {
                c.Expire(Stopwatch.GetTimestamp(), stopping);
            }

            await timer.WaitForNextTickAsync();
        }
    }
}

sealed class ServerConnection(Server server, Socket socket)
{
    public const int SampleEvery = 1024;
    readonly Options o = server.O;
    public readonly Processor Processor = new(server.O.Mode, server.O.RetainBatches, server.O.RetainBytes);
    public readonly Stages Stages = new();
    public readonly Totals Totals = new();
    long frameStart, progress; // Stopwatch ticks; frameStart is 0 between frames (no deadline)
    string? abortReason;

    // Called by the scanner. A stale read can only close a frame whose deadline had passed.
    public void Expire(long now, bool stopping)
    {
        string? why = stopping ? "shutdown" : Deadline(out long end) is { } reason && now >= end ? reason : null;
        if (why != null && Interlocked.CompareExchange(ref abortReason, why, null) == null)
        {
            socket.Dispose(); // pending receive/send completes with an error
        }
    }

    string? Deadline(out long end)
    {
        end = 0;
        long start = Volatile.Read(ref frameStart);
        if (start == 0)
        {
            return null;
        }

        long frameEnd = start + o.FrameMs * Stopwatch.Frequency / 1000;
        long idleEnd = Volatile.Read(ref progress) + o.IdleMs * Stopwatch.Frequency / 1000;
        end = Math.Min(frameEnd, idleEnd);
        return idleEnd < frameEnd ? "idle_timeout" : "frame_timeout";
    }

    Budget Budget() => Deadline(out long end) is { } reason ? new Budget(end, server.Stop.Token, reason) : new Budget(0, server.Stop.Token);

    public async Task Run()
    {
        ulong last = 0;
        bool epoch = false;
        string reason = "internal_error";
        long receiveStart = Stopwatch.GetTimestamp();
        try
        {
            o.Configure(socket);
            server.RecordBuffers(socket);
            byte[] header = new byte[16], body = new byte[Math.Max(32, o.MaxFrame)], response = new byte[16 + 1056];
            while (true)
            {
                // A quiet connection has no deadline; the first header byte starts the frame.
                int n = await Receive(header.AsMemory(0, 16));
                if (n == 0)
                {
                    Processor.VerifyRetained(new Budget(0, server.Stop.Token)); // clean EOF between frames
                    reason = epoch ? "eof_in_epoch" : "graceful";
                    break;
                }

                long first = Stopwatch.GetTimestamp();
                Volatile.Write(ref progress, first);
                Volatile.Write(ref frameStart, first);
                await ReceiveExact(header.AsMemory(n));
                int length = (int)Math.Min(BinaryPrimitives.ReadUInt32BigEndian(header), int.MaxValue);
                ushort type = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(6));
                ulong sequence = BinaryPrimitives.ReadUInt64BigEndian(header.AsSpan(8));
                if (BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(4)) != 1 || type is < 1 or > 3 ||
                    last == ulong.MaxValue || sequence != last + 1 || length > Options.HardLimit)
                {
                    throw new BenchException("frame_header");
                }

                if (!(type == 1 ? length == 32 && !epoch : type == 3 ? length == 0 && epoch : length > 0 && length <= o.MaxFrame && epoch))
                {
                    throw new BenchException("frame_state_or_length");
                }

                last = sequence;
                await ReceiveExact(body.AsMemory(0, length));
                long bodyDone = Stopwatch.GetTimestamp();
                Budget budget = Budget();
                int size = 0;
                if (type == 1)
                {
                    if (!body.AsSpan(0, 32).SequenceEqual(o.Manifest))
                    {
                        throw new BenchException("manifest_mismatch");
                    }

                    Processor.Epoch.Clear();
                    Stages.Clear();
                    epoch = true;
                    server.BeginEpoch();
                }
                else if (type == 3)
                {
                    Processor.VerifyRetained(budget);
                    Processor.Epoch.Write(response.AsSpan(16));
                    size = 1056;
                }
                else
                {
                    ulong ordinal = Processor.Epoch.Batches + 1;
                    bool sample = ordinal % SampleEvery == 0;
                    if (o.PauseEvery > 0 && ordinal % (ulong)o.PauseEvery == 0)
                    {
                        budget.Spin(o.PauseMs);
                    }

                    Result r = Processor.Process(body.AsSpan(0, length), sequence, budget, sample);
                    BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(16), r.Records);
                    BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(24), r.Hash);
                    size = 16;
                    if (sample)
                    {
                        Stages.Receive.RecordTicks(bodyDone - receiveStart);
                        Stages.Processing.RecordTicks(Stopwatch.GetTimestamp() - bodyDone);
                        if (o.Mode != Mode.Transport)
                        {
                            Stages.Decode.RecordTicks(Processor.LastDecodeTicks);
                        }

                        if (o.Mode is Mode.RetainReuse or Mode.RetainAllocate)
                        {
                            Stages.Visit.RecordTicks(Processor.LastVisitTicks);
                        }
                    }
                }

                budget.Check(); // expired work never publishes a success response or counts
                if (type == 2)
                {
                    Totals.Frames++;
                    Totals.Records += (long)Processor.Last.Records;
                    Totals.Bytes += length;
                }
                else if (type == 3)
                {
                    epoch = false;
                    server.EndEpoch(Processor.Epoch);
                }

                Options.Header(response, size, (ushort)(type | 0x8000), sequence);
                await SendAll(response.AsMemory(0, 16 + size));
                receiveStart = Stopwatch.GetTimestamp();
                Volatile.Write(ref frameStart, 0);
                // One frame per turn: requeue behind other connections instead of continuing
                // inline while this connection's next frame is already buffered (as native does).
                await Task.Yield();
            }
        }
        catch (Exception e)
        {
            reason = Volatile.Read(ref abortReason) ?? e switch
            {
                BenchException => e.Message,
                JsonException => "json_syntax",
                SocketException or ObjectDisposedException or IOException => "io_error",
                _ => "internal_error:" + e.GetType().Name // never counted as graceful
            };
        }
        finally
        {
            if (epoch)
            {
                server.EndEpoch(null);
            }

            socket.Dispose();
            server.Finish(this, reason);
        }
    }

    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder<>))] // no box per suspension
    async ValueTask<int> Receive(Memory<byte> destination)
    {
        int n = await socket.ReceiveAsync(o.IoCap > 0 && destination.Length > o.IoCap ? destination[..o.IoCap] : destination, SocketFlags.None);
        Totals.ReceiveCalls++;
        Totals.Received += n;
        return n;
    }

    // Deadlines are checked at every completion (as in the native server) and by the scanner.
    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder))] // no box per suspension
    async ValueTask ReceiveExact(Memory<byte> destination)
    {
        while (!destination.IsEmpty)
        {
            int n = await Receive(destination);
            Budget().Check();
            if (n == 0)
            {
                throw new BenchException("truncated");
            }

            Volatile.Write(ref progress, Stopwatch.GetTimestamp());
            destination = destination[n..];
        }
    }

    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder))] // no box per suspension
    async ValueTask SendAll(ReadOnlyMemory<byte> source)
    {
        while (!source.IsEmpty)
        {
            int n = await socket.SendAsync(o.IoCap > 0 && source.Length > o.IoCap ? source[..o.IoCap] : source, SocketFlags.None);
            Totals.SendCalls++;
            Totals.Sent += n;
            Budget().Check();
            if (n == 0)
            {
                throw new BenchException("send_zero");
            }

            Volatile.Write(ref progress, Stopwatch.GetTimestamp());
            source = source[n..];
        }
    }
}
