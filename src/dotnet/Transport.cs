using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;

namespace TcpBench;

sealed class IoCounters
{
    public long ReceiveCalls;
    public long SendCalls;
    public long ReceiveBytes;
    public long SendBytes;
}

sealed class Wire : IDisposable
{
    public const int HardLimit = 16 * 1024 * 1024;
    readonly Socket socket;
    readonly IoCounters counters;
    readonly CancellationTokenSource deadline;
    readonly int idleMs, frameMs, cap;
    readonly CancellationToken lifetime;
    long lastProgress;
    public Wire(Socket socket, IoCounters counters, CancellationToken token, int idleMs = 5000, int frameMs = 30000, int cap = int.MaxValue)
    {
        this.socket = socket;
        this.counters = counters;
        lifetime = token;
        this.idleMs = idleMs;
        this.frameMs = frameMs;
        this.cap = cap;
        deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
    }

    public static void Header(Span<byte> header, int length, ushort type, ulong sequence)
    {
        BinaryPrimitives.WriteUInt32BigEndian(header, (uint)length);
        BinaryPrimitives.WriteUInt16BigEndian(header[4..], 1);
        BinaryPrimitives.WriteUInt16BigEndian(header[6..], type);
        BinaryPrimitives.WriteUInt64BigEndian(header[8..], sequence);
    }

    public static (int Length, ushort Type, ulong Sequence) ParseHeader(ReadOnlySpan<byte> header)
    {
        uint length = BinaryPrimitives.ReadUInt32BigEndian(header);
        if (length > HardLimit)
        {
            throw new InvalidDataException("frame_length");
        }

        if (BinaryPrimitives.ReadUInt16BigEndian(header[4..]) != 1)
        {
            throw new InvalidDataException("frame_version");
        }

        return ((int)length, BinaryPrimitives.ReadUInt16BigEndian(header[6..]), BinaryPrimitives.ReadUInt64BigEndian(header[8..]));
    }

    void Arm(long start)
    {
        double left = frameMs - Stopwatch.GetElapsedTime(start).TotalMilliseconds;
        if (left <= 0)
        {
            throw new TimeoutException("frame_deadline");
        }

        deadline.CancelAfter(TimeSpan.FromMilliseconds(Math.Max(1, Math.Min(idleMs, left))));
    }

    public ProcessingBudget ProcessingBudget(long frameStart)
    {
        long frameEnd = frameStart + (long)frameMs * Stopwatch.Frequency / 1000;
        long idleEnd = lastProgress + (long)idleMs * Stopwatch.Frequency / 1000;
        return new ProcessingBudget(Math.Min(frameEnd, idleEnd), lifetime);
    }

    public async ValueTask<(bool Present, long Start)> ReadHeader(byte[] header)
    {
        // A quiet connection has no idle timeout. The first received header bytes start the frame deadline.
        int n = await socket.ReceiveAsync(header.AsMemory(0, Math.Min(16, cap)), SocketFlags.None, lifetime);
        Interlocked.Increment(ref counters.ReceiveCalls);
        Interlocked.Add(ref counters.ReceiveBytes, n);
        if (n == 0)
        {
            return (false, 0);
        }

        long start = Stopwatch.GetTimestamp();
        lastProgress = start;
        if (n < 16)
        {
            await ReadExact(header.AsMemory(n, 16 - n), start);
        }

        return (true, start);
    }

    public async ValueTask ReadExact(Memory<byte> destination, long start)
    {
        while (!destination.IsEmpty)
        {
            Arm(start);
            int n = await socket.ReceiveAsync(destination[..Math.Min(destination.Length, cap)], SocketFlags.None, deadline.Token);
            deadline.CancelAfter(Timeout.Infinite);
            Interlocked.Increment(ref counters.ReceiveCalls);
            Interlocked.Add(ref counters.ReceiveBytes, n);
            if (n == 0)
            {
                throw new InvalidDataException("truncated_frame");
            }

            lastProgress = Stopwatch.GetTimestamp();
            destination = destination[n..];
        }
    }

    public async ValueTask Send(ReadOnlyMemory<byte> source, long start)
    {
        while (!source.IsEmpty)
        {
            Arm(start);
            int n = await socket.SendAsync(source[..Math.Min(source.Length, cap)], SocketFlags.None, deadline.Token);
            deadline.CancelAfter(Timeout.Infinite);
            Interlocked.Increment(ref counters.SendCalls);
            Interlocked.Add(ref counters.SendBytes, n);
            if (n == 0)
            {
                throw new IOException("zero_send");
            }

            source = source[n..];
        }
    }

    public void Dispose()
    {
        deadline.Dispose();
    }
}

static class Server
{
    public static async Task<int> Run(Options options)
    {
        var snapshot = new Resources();
        var io = new IoCounters();
        using var stop = new CancellationTokenSource();
        if (options.RunSeconds > 0)
        {
            stop.CancelAfter(TimeSpan.FromSeconds(options.RunSeconds));
        }

        ConsoleCancelEventHandler interrupt = (_, e) =>
        {
            e.Cancel = true;
            stop.Cancel();
        };
        Console.CancelKeyPress += interrupt;
        if (options.ControlStdin == 1)
        {
            _ = Task.Run(async () =>
            {
                while (!stop.IsCancellationRequested)
                {
                    string? line = await Console.In.ReadLineAsync();
                    if (line == null || line == "stop")
                    {
                        try
                        {
                            stop.Cancel();
                        }
                        catch (ObjectDisposedException)
                        {
                        }

                        return;
                    }
                }
            });
        }

        using var listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        listener.Bind(new IPEndPoint(IPAddress.Loopback, options.Port));
        listener.Listen(options.MaxConnections);
        var reasons = new ConcurrentDictionary<string, long>();
        var connections = new List<Task>();
        long accepted = 0, active = 0, rejected = 0, batches = 0, records = 0, payloadBytes = 0, peakRetained = 0, peakOwned = 0;
        int effectiveSend = 0, effectiveReceive = 0;
        var receiveStages = new Histogram();
        var processStages = new Histogram();
        var decodeStages = new Histogram();
        var visitStages = new Histogram();
        object stageLock = new();
        JsonOutput.Print(new
        {
            @event = "ready",
            port = options.Port,
            implementation = "dotnet",
            mode = options.Mode,
            runtime = Environment.Version.ToString()
        });
        try
        {
            while (!stop.IsCancellationRequested)
            {
                Socket socket;
                try
                {
                    socket = await listener.AcceptAsync(stop.Token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }

                connections.RemoveAll(t => t.IsCompleted);
                if (Interlocked.Read(ref active) >= options.MaxConnections)
                {
                    socket.Dispose();
                    Interlocked.Increment(ref rejected);
                    continue;
                }

                options.Configure(socket);
                effectiveSend = socket.SendBufferSize;
                effectiveReceive = socket.ReceiveBufferSize;
                Interlocked.Increment(ref accepted);
                Interlocked.Increment(ref active);
                connections.Add(Handle(socket));
            }
        }
        finally
        {
            stop.Cancel();
            listener.Close();
            await Task.WhenAll(connections);
            Console.CancelKeyPress -= interrupt;
        }

        JsonOutput.Save(options.Output, new
        {
            implementation = "dotnet",
            language = "csharp",
            command = "server",
            kind = "server",
            mode = options.Mode,
            accepted,
            rejected_connections = rejected,
            completed = batches,
            records,
            payload_bytes = payloadBytes,
            errors = reasons,
            transport = io,
            retained_canonical_peak_per_connection = peakRetained,
            owned_storage_peak_per_connection = peakOwned,
            receive_capacity_per_connection = options.MaxFrame,
            socket_send_buffer = effectiveSend,
            socket_receive_buffer = effectiveReceive,
            workers_requested = options.Workers,
            worker_policy = "runtime thread pool; CPU affinity is the resource boundary",
            diagnostic_pause_every = options.PauseEvery,
            diagnostic_pause_ms = options.PauseMs,
            diagnostic_io_cap = options.IoCap,
            resources_scope = "server process lifetime including setup, warmup, measured epochs, drain and shutdown; do not normalize by only measured records",
            retention_capacity_policy = "canonical bytes bounded; owned-array peak includes simultaneous retained and incoming scratch row/text capacities, geometric growth slack, and one reusable spare; excludes object/array headers, abandoned arrays awaiting GC, receive/parser buffers and runtime memory",
            stage_sampling = "every 1024th data frame per connection; reset on BeginEpoch; final epoch per connection only; process includes retention eviction/revisit; receive includes waiting since first header receive issue; p99 requires 10000 samples, p999 requires 1000000",
            receive_stage_ns = receiveStages.ExportStages(),
            processing_stage_ns = processStages.ExportStages(),
            decode_stage = options.Mode == "aggregate" ? "combined decode and aggregation" : "decode and materialize",
            decode_stage_ns = decodeStages.ExportStages(),
            retained_visit_and_eviction_stage_ns = visitStages.ExportStages(),
            resources = snapshot.Finish(),
            build = Metadata.Build()
        });
        return 0;
        async Task Handle(Socket socket)
        {
            using (socket)
            using (var wire = new Wire(socket, io, stop.Token, options.IdleMs, options.FrameMs, options.IoCap))
            {
                var processor = new Processor(options.Mode, options.RetainBatches, options.RetainBytes);
                byte[] input = new byte[Math.Max(32, options.MaxFrame)], header = new byte[16], response = new byte[1072];
                ulong sequence = 1, dataFrames = 0;
                bool epoch = false;
                var receiveSamples = new Histogram();
                var processSamples = new Histogram();
                var decodeSamples = new Histogram();
                var visitSamples = new Histogram();
                try
                {
                    while (!stop.IsCancellationRequested)
                    {
                        bool sample = (dataFrames + 1) % 1024 == 0;
                        long receiveStart = sample ? Stopwatch.GetTimestamp() : 0;
                        var read = await wire.ReadHeader(header);
                        if (!read.Present)
                        {
                            break;
                        }

                        var h = Wire.ParseHeader(header);
                        if (h.Sequence != sequence || sequence == ulong.MaxValue)
                        {
                            throw new InvalidDataException("frame_sequence");
                        }

                        if (h.Type is < 1 or > 3)
                        {
                            throw new InvalidDataException("frame_type");
                        }

                        bool validState = h.Type switch
                        {
                            1 => h.Length == 32 && !epoch,
                            2 => h.Length > 0 && h.Length <= options.MaxFrame && epoch,
                            3 => h.Length == 0 && epoch,
                            _ => false
                        };
                        if (!validState)
                        {
                            throw new InvalidDataException("frame_state_or_length");
                        }

                        await wire.ReadExact(input.AsMemory(0, h.Length), read.Start);
                        ProcessingBudget budget = wire.ProcessingBudget(read.Start);
                        budget.Check();
                        int length;
                        if (h.Type == 1)
                        {
                            if (!input.AsSpan(0, 32).SequenceEqual(options.Manifest))
                            {
                                throw new InvalidDataException("manifest_mismatch");
                            }

                            processor.Epoch.Reset();
                            dataFrames = 0;
                            receiveSamples.Clear();
                            processSamples.Clear();
                            decodeSamples.Clear();
                            visitSamples.Clear();
                            epoch = true;
                            length = 0;
                        }
                        else if (h.Type == 2)
                        {
                            dataFrames++;
                            if (sample)
                            {
                                receiveSamples.RecordTicks(Stopwatch.GetTimestamp() - receiveStart);
                            }

                            long processStart = sample ? Stopwatch.GetTimestamp() : 0;
                            if (options.PauseEvery > 0 && dataFrames % (ulong)options.PauseEvery == 0)
                            {
                                await budget.Pause(options.PauseMs);
                            }

                            var result = processor.Process(input.AsSpan(0, h.Length), sequence, sample, budget);
                            if (sample)
                            {
                                processSamples.RecordTicks(Stopwatch.GetTimestamp() - processStart);
                                decodeSamples.RecordTicks(processor.LastDecodeTicks);
                                if (options.Mode.StartsWith("retain", StringComparison.Ordinal))
                                {
                                    visitSamples.RecordTicks(processor.LastVisitTicks);
                                }
                            }

                            Interlocked.Increment(ref batches);
                            Interlocked.Add(ref records, (long)result.Records);
                            Interlocked.Add(ref payloadBytes, h.Length);
                            BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(16), result.Records);
                            BinaryPrimitives.WriteUInt64BigEndian(response.AsSpan(24), result.Hash);
                            length = 16;
                        }
                        else
                        {
                            processor.VerifyRetained(budget);
                            processor.Epoch.Write(response.AsSpan(16));
                            epoch = false;
                            length = 1056;
                        }

                        Wire.Header(response, length, (ushort)(h.Type | 0x8000), sequence++);
                        await wire.Send(response.AsMemory(0, length + 16), read.Start);
                    }

                    // Clean EOF has no active frame; never reuse an earlier frame's expired deadline.
                    processor.VerifyRetained(new ProcessingBudget(0, stop.Token));
                }
                catch (OperationCanceledException) when (stop.IsCancellationRequested)
                {
                }
                catch (Exception e) when (e is InvalidDataException or System.Text.Json.JsonException or ArgumentException or
                    InvalidOperationException or IOException or SocketException or OperationCanceledException or TimeoutException or OverflowException)
                {
                    string reason = e switch
                    {
                        InvalidDataException => e.Message,
                        OperationCanceledException or TimeoutException => "deadline",
                        System.Text.Json.JsonException => "json_syntax",
                        SocketException => "socket_error",
                        _ => e.GetType().Name
                    };
                    reasons.AddOrUpdate(reason, 1, (_, n) => n + 1);
                }
                finally
                {
                    Max(ref peakRetained, processor.PeakRetainedBytes);
                    Max(ref peakOwned, processor.PeakOwnedCapacityBytes);
                    lock (stageLock)
                    {
                        receiveStages.Merge(receiveSamples);
                        processStages.Merge(processSamples);
                        decodeStages.Merge(decodeSamples);
                        visitStages.Merge(visitSamples);
                    }

                    Interlocked.Decrement(ref active);
                }
            }
        }
    }

    static void Max(ref long value, long candidate)
    {
        long current;
        do
        {
            current = Interlocked.Read(ref value);
            if (candidate <= current)
            {
                return;
            }
        }
        while (Interlocked.CompareExchange(ref value, candidate, current) != current);
    }
}
