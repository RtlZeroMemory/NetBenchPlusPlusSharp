using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading.Channels;

namespace TcpBench;

sealed class Request
{
    public CorpusFrame Frame = null!;
    public long Intended, FirstSend;
    public ulong Sequence;
}

sealed class Admission : IDisposable
{
    readonly Stack<Request> free;
    readonly long byteLimit;
    readonly object sync = new();
    public readonly SemaphoreSlim Changed = new(0, 1);
    long bytes;
    int occupied;
    public long PeakBytes { get; private set; }
    public int PeakFrames { get; private set; }

    public Admission(int window, long byteLimit)
    {
        free = new(Enumerable.Range(0, window).Select(_ => new Request()));
        this.byteLimit = byteLimit;
    }

    public Request? Rent(CorpusFrame frame, long intended)
    {
        lock (sync)
        {
            if (free.Count == 0 || frame.Payload.Length > byteLimit - bytes)
            {
                return null;
            }

            Request request = free.Pop();
            request.Frame = frame;
            request.Intended = intended;
            request.FirstSend = 0;
            bytes += frame.Payload.Length;
            occupied++;
            PeakBytes = Math.Max(PeakBytes, bytes);
            PeakFrames = Math.Max(PeakFrames, occupied);
            return request;
        }
    }

    public void Return(Request request)
    {
        lock (sync)
        {
            bytes -= request.Frame.Payload.Length;
            occupied--;
            free.Push(request);
            if (Changed.CurrentCount == 0)
            {
                Changed.Release();
            }
        }
    }

    public void Dispose()
    {
        Changed.Dispose();
    }
}

sealed class ClientConnection : IDisposable
{
    public readonly Socket Socket;
    public ulong Sequence = 1;
    public int Cursor;
    public ClientConnection(Socket socket, int index)
    {
        Socket = socket;
        Cursor = index;
    }

    public void Dispose()
    {
        Socket.Dispose();
    }
}

sealed class ConnectionMetrics
{
    public readonly Histogram Scheduled = new(), Submitted = new(), Delay = new();
    public readonly Histogram[] Sizes = [new(), new(), new(), new(), new()];
    public long Started;
    public long Acknowledged;
    public long Records;
    public long Bytes;
    public long WindowCompleted;
    public long WindowRecords;
    public long WindowBytes;
    public long Miss1ms;
    public long Miss5ms;
    public long Miss10ms;
    public static int SizeClass(int bytes) => bytes <= 4096 ? 0 : bytes <= 65536 ? 1 : bytes <= 262144 ? 2 : bytes <= 1048576 ? 3 : 4;
}

sealed class PhaseResult
{
    public long Offered;
    public long Admitted;
    public long Rejected;
    public long Failed;
    public long Timedout;
    public long Unresolved;
    public long WindowCompleted;
    public long WindowRecords;
    public long WindowBytes;
    public long Records;
    public long Bytes;
    public long Started;
    public long Acknowledged;
    public long PeakBytes;
    public int PeakFrames;
    public double WindowSeconds, CohortSeconds, DrainSeconds;
    public bool Valid;
    public string? Error;
    public readonly Histogram Scheduled = new();
    public readonly Histogram Submitted = new();
    public readonly Histogram Delay = new();
    public readonly Histogram Lateness = new();
    public readonly Histogram[] Sizes = [new(), new(), new(), new(), new()];
    public long Miss1ms;
    public long Miss5ms;
    public long Miss10ms;
    public long ScheduleBytes;
    public long GeneratorLateRejected;
    public long AbortedScheduleRejected;
    public long SchedulerLate1ms;
    public void Merge(ConnectionMetrics metrics)
    {
        Started += metrics.Started;
        Acknowledged += metrics.Acknowledged;
        Records += metrics.Records;
        Bytes += metrics.Bytes;
        WindowCompleted += metrics.WindowCompleted;
        WindowRecords += metrics.WindowRecords;
        WindowBytes += metrics.WindowBytes;
        Scheduled.Merge(metrics.Scheduled);
        Submitted.Merge(metrics.Submitted);
        Delay.Merge(metrics.Delay);
        Miss1ms += metrics.Miss1ms;
        Miss5ms += metrics.Miss5ms;
        Miss10ms += metrics.Miss10ms;
        for (int i = 0; i < Sizes.Length; i++)
        {
            Sizes[i].Merge(metrics.Sizes[i]);
        }
    }

    public void RejectUndispatched(long planned, bool aborted, List<ClientConnection> connections, int frameCount)
    {
        long remaining = Math.Max(0, planned - Offered);
        if (aborted)
        {
            AbortedScheduleRejected = remaining;
        }
        else
        {
            GeneratorLateRejected = remaining;
        }

        // Advance the same round-robin cursors that dispatched arrivals use, without iterating lost demand.
        for (int i = 0; i < connections.Count; i++)
        {
            long offset = (i - Offered % connections.Count + connections.Count) % connections.Count;
            long assigned = remaining <= offset ? 0 : (remaining - offset - 1) / connections.Count + 1;
            connections[i].Cursor = (int)((connections[i].Cursor + assigned * connections.Count) % frameCount);
        }

        Offered += remaining;
        Rejected += remaining;
    }
}

static class Load
{
    public static async Task<int> Run(Options options)
    {
        var corpus = new Corpus(options.Corpus);
        if (corpus.Frames.Any(f => f.Payload.Length > options.InflightBytes))
        {
            throw new ArgumentException("inflight-bytes must fit every corpus frame");
        }

        var io = new IoCounters();
        var connections = new List<ClientConnection>();
        try
        {
            for (int i = 0; i < options.Connections; i++)
            {
                var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                options.Configure(socket);
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(options.DrainSeconds));
                try
                {
                    await socket.ConnectAsync(new IPEndPoint(IPAddress.Loopback, options.Port), timeout.Token);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }

                connections.Add(new(socket, i % corpus.Frames.Length));
            }

            if (options.Warmup > 0)
            {
                PhaseResult warm = await Phase(options.Warmup, options, corpus, connections, io);
                if (!warm.Valid)
                {
                    throw new InvalidDataException("warmup failed: " + warm.Error);
                }
            }

            var snapshot = new Resources();
            PhaseResult result = await Phase(options.Duration, options, corpus, connections, io);
            object resources = snapshot.Finish();
            bool sustainable = result.Valid && result.Rejected == 0 && result.Timedout == 0 &&
                (options.Rate == 0 || result.WindowCompleted >= result.Offered * .99 &&
                    result.SchedulerLate1ms == 0 && result.GeneratorLateRejected == 0);
            bool generatorAdequate = result.Valid && (options.Rate == 0 ||
                result.SchedulerLate1ms == 0 && result.GeneratorLateRejected == 0 && result.AbortedScheduleRejected == 0);
            object scheduledLatency = result.Scheduled.Export();
            object[] sizeLatency = result.Sizes.Select(histogram => histogram.Export()).ToArray();
            JsonOutput.Save(options.Output, new
            {
                implementation = "dotnet",
                language = "csharp",
                command = "client",
                kind = "client",
                mode = options.Mode,
                valid = result.Valid,
                success = result.Valid,
                sustainable,
                sustainability_scope = "single-run reconciliation and 99% window throughput; plateau and repeated-trial gates require campaign analysis",
                error = result.Error,
                offered = result.Offered,
                admitted = result.Admitted,
                first_send_started = result.Started,
                acknowledged = result.Acknowledged,
                generator_adequate = generatorAdequate,
                generator_adequacy_policy = "scheduled runs require zero scheduler arrivals over 1ms late and zero undispatched arrivals; this is a conservative SLO evidence gate",
                scheduler_late_over_1ms = result.SchedulerLate1ms,
                pacing = "process-local high-resolution Windows waitable timer plus at most 200us final spin; no global timer resolution change",
                completed = result.Acknowledged,
                completed_frames = result.Acknowledged,
                completed_records = result.Records,
                records = result.Records,
                payload_bytes = result.Bytes,
                rejected = result.Rejected,
                failed = result.Failed,
                timedout = result.Timedout,
                unresolved = result.Unresolved,
                generator_late_rejected = result.GeneratorLateRejected,
                aborted_schedule_rejected = result.AbortedScheduleRejected,
                window_completed = result.WindowCompleted,
                window_completed_frames = result.WindowCompleted,
                window_records = result.WindowRecords,
                window_payload_bytes = result.WindowBytes,
                duration_seconds = result.WindowSeconds,
                window_seconds = result.WindowSeconds,
                cohort_seconds = result.CohortSeconds,
                drain_seconds = result.DrainSeconds,
                configured_drain_seconds = options.DrainSeconds,
                drain_limit_seconds = options.DrainSeconds,
                cohort_timing_scope = "T0 through max(T1, completion of all data send/ACK workers), including failed drain waits; End controls excluded",
                window_frames_per_second = result.WindowCompleted / result.WindowSeconds,
                cohort_frames_per_second = result.Acknowledged / result.CohortSeconds,
                window_records_per_second = result.WindowRecords / result.WindowSeconds,
                cohort_records_per_second = result.Records / result.CohortSeconds,
                window_payload_gib_per_second = result.WindowBytes / result.WindowSeconds / (1L << 30),
                cohort_payload_gib_per_second = result.Bytes / result.CohortSeconds / (1L << 30),
                scheduled_latency = scheduledLatency,
                submitted_latency = result.Submitted.Export(),
                client_delay = result.Delay.Export(),
                scheduler_lateness = result.Lateness.Export(),
                latency_ns = scheduledLatency,
                size_class_scheduled_latency = sizeLatency.Select((histogram, index) => (histogram, index)).ToDictionary(p => p.index.ToString(), p => p.histogram),
                size_latency = sizeLatency,
                size_class_upper_payload_bytes = new[] { 4096, 65536, 262144, 1048576, Wire.HardLimit },
                misses_1ms = result.Miss1ms + result.Rejected + result.Failed + result.Unresolved,
                misses_5ms = result.Miss5ms + result.Rejected + result.Failed + result.Unresolved,
                misses_10ms = result.Miss10ms + result.Rejected + result.Failed + result.Unresolved,
                queue_high_water = result.PeakFrames,
                inflight_bytes_high_water = result.PeakBytes,
                rate = options.Rate,
                arrival = options.Arrival,
                seed = options.Seed,
                load_model = options.Rate == 0 ? "closed-loop" : "scheduled independent arrivals",
                connection_assignment = "round-robin offered request index",
                corpus_cursor = "connection index; advance by connections on each offered request; preserve across warmup",
                global_window = options.Window,
                global_inflight_bytes = options.InflightBytes,
                connections = options.Connections,
                corpus_sha256 = corpus.Hash,
                corpus_frames = corpus.Frames.Length,
                corpus_payload_bytes = corpus.PayloadBytes,
                schedule_bytes = result.ScheduleBytes,
                socket_send_buffer = connections[0].Socket.SendBufferSize,
                socket_receive_buffer = connections[0].Socket.ReceiveBufferSize,
                protocol_data_header_bytes = result.Started * 16,
                protocol_ack_bytes = result.Acknowledged * 32,
                transport = io,
                transport_scope = "whole client lifecycle including warmup and controls",
                resources_scope = "measured phase including phase setup, Begin/End controls and drain",
                resources,
                build = Metadata.Build()
            });
            return result.Valid ? 0 : 1;
        }
        finally
        {
            foreach (ClientConnection connection in connections)
            {
                connection.Dispose();
            }
        }
    }

    static async Task Control(ClientConnection connection, ushort type, byte[] body, Summary? expected, Options options, IoCounters io)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(options.DrainSeconds));
        using var wire = new Wire(
connection.Socket,
io,
timeout.Token,
(int)Math.Min(int.MaxValue, options.DrainSeconds * 1000),
(int)Math.Min(int.MaxValue, options.DrainSeconds * 1000),
options.IoCap);
        byte[] header = new byte[16];
        Wire.Header(header, body.Length, type, connection.Sequence);
        long start = Stopwatch.GetTimestamp();
        await wire.Send(header, start);
        await wire.Send(body, start);
        await wire.ReadExact(header, start);
        var response = Wire.ParseHeader(header);
        if (response.Type != (type | 0x8000) || response.Sequence != connection.Sequence || response.Length != (type == 1 ? 0 : 1056))
        {
            throw new InvalidDataException("control_ack");
        }

        connection.Sequence++;
        if (response.Length > 0)
        {
            byte[] output = new byte[1056];
            await wire.ReadExact(output, start);
            expected!.Verify(output);
        }
    }

    static async Task<PhaseResult> Phase(double seconds, Options options, Corpus corpus, List<ClientConnection> connections, IoCounters io)
    {
        long[]? schedule = options.Rate > 0 ? options.Arrival switch
        {
            "poisson" => ArrivalSchedule.Poisson(seconds, options.Rate, options.Seed),
            "burst" => ArrivalSchedule.Burst(seconds, options.Rate),
            _ => null
        } : null;
        foreach (ClientConnection connection in connections)
        {
            await Control(connection, 1, options.Manifest, null, options, io);
        }

        var result = new PhaseResult
        {
            WindowSeconds = seconds,
            ScheduleBytes = schedule?.LongLength * 8 ?? 0

        };
        using var admission = new Admission(options.Window, options.InflightBytes);
        using var failure = new CancellationTokenSource();
        using var generationStop = CancellationTokenSource.CreateLinkedTokenSource(failure.Token);
        using var pacer = new Pacer(generationStop.Token);
        var metrics = connections.Select(_ => new ConnectionMetrics()).ToArray();
        var summaries = connections.Select(_ => new Summary()).ToArray();
        var sendQueues = connections.Select(_ => Channel.CreateBounded<Request>(new BoundedChannelOptions(options.Window)
        {
            SingleReader = true,
            SingleWriter = true,
            AllowSynchronousContinuations = false
        })).ToArray();
        var ackQueues = connections.Select(_ => Channel.CreateBounded<Request>(new BoundedChannelOptions(options.Window)
        {
            SingleReader = true,
            SingleWriter = true,
            AllowSynchronousContinuations = false
        })).ToArray();
        long start = Stopwatch.GetTimestamp() + Stopwatch.Frequency / 10, end = start + (long)(seconds * Stopwatch.Frequency);
        generationStop.CancelAfter(TimeSpan.FromSeconds(seconds + .1));
        failure.CancelAfter(TimeSpan.FromSeconds(seconds + .1 + options.DrainSeconds));
        string? error = null;
        bool drainExpired = false;
        var tasks = new List<Task>();
        // The generator owns admission/cursors; each connection has one sender and one ACK reader.
        // Workers update separate metric fields. Merging waits for all workers to release their ownership.
        for (int c = 0; c < connections.Count; c++)
        {
            int index = c;
            tasks.Add(SendLoop(index));
            tasks.Add(AckLoop(index));
        }

        try
        {
            long offeredIndex = 0;
            while (!generationStop.IsCancellationRequested)
            {
                long due;
                if (options.Rate == 0)
                {
                    due = Math.Max(start, Stopwatch.GetTimestamp());
                }
                else if (schedule != null)
                {
                    due = offeredIndex < schedule.Length ? start + schedule[offeredIndex] : end;
                }
                else
                {
                    due = start + (long)(offeredIndex / options.Rate * Stopwatch.Frequency);
                }

                if (due >= end)
                {
                    break;
                }

                pacer.Wait(due);
                if (options.Rate == 0 && Stopwatch.GetTimestamp() >= end)
                {
                    break;
                }

                int index = (int)(offeredIndex % connections.Count);
                ClientConnection connection = connections[index];
                CorpusFrame frame = corpus.Frames[connection.Cursor];
                Request? request;
                if (options.Rate == 0)
                {
                    while ((request = admission.Rent(frame, Stopwatch.GetTimestamp())) == null)
                    {
                        await admission.Changed.WaitAsync(generationStop.Token);
                    }

                    if (Stopwatch.GetTimestamp() >= end)
                    {
                        admission.Return(request);
                        break;
                    }
                }
                else
                {
                    request = admission.Rent(frame, due);
                }

                long now = Stopwatch.GetTimestamp();
                result.Lateness.RecordTicks(now - due);
                if (Histogram.Nanoseconds(now - due) > 1_000_000)
                {
                    result.SchedulerLate1ms++;
                }

                result.Offered++;
                offeredIndex++;
                connection.Cursor = (connection.Cursor + connections.Count) % corpus.Frames.Length;
                if (request == null)
                {
                    result.Rejected++;
                }
                else
                {
                    result.Admitted++;
                    if (!sendQueues[index].Writer.TryWrite(request))
                    {
                        throw new InvalidOperationException("bounded descriptor queue mismatch");
                    }
                }

                if ((offeredIndex & 255) == 0)
                {
                    await Task.Yield();
                }
            }
        }
        catch (OperationCanceledException) when (generationStop.IsCancellationRequested)
        {
        }
        catch (Exception e)
        {
            error = e.Message;
            failure.Cancel();
        }
        finally
        {
            foreach (var queue in sendQueues)
            {
                queue.Writer.TryComplete();
            }
        }

        await Task.WhenAll(tasks);
        if (error == null)
        {
            try
            {
                pacer.Wait(end);
            }
            catch (OperationCanceledException) when (generationStop.IsCancellationRequested)
            {
            }
        }

        long drainFinish = Stopwatch.GetTimestamp();
        // End control exchanges below are deliberately outside the data cohort/drain interval.
        foreach (ConnectionMetrics metric in metrics)
        {
            result.Merge(metric);
        }

        if (options.Rate > 0)
        {
            long planned = schedule?.LongLength ?? (long)Math.Ceiling(seconds * options.Rate);
            result.RejectUndispatched(planned, error != null, connections, corpus.Frames.Length);
        }

        long missing = result.Admitted - result.Acknowledged;
        result.Timedout = drainExpired ? missing : 0;
        result.Failed = error != null && !drainExpired ? missing : 0;
        result.Unresolved = missing - result.Failed;
        result.PeakBytes = admission.PeakBytes;
        result.PeakFrames = admission.PeakFrames;
        result.CohortSeconds = Math.Max(seconds, (double)(drainFinish - start) / Stopwatch.Frequency);
        result.DrainSeconds = Math.Max(0, (double)(drainFinish - end) / Stopwatch.Frequency);
        if (error == null && missing == 0)
        {
            try
            {
                for (int i = 0; i < connections.Count; i++)
                {
                    await Control(connections[i], 3, [], summaries[i], options, io);
                }
            }
            catch (Exception e)
            {
                error = e.Message;
            }
        }

        result.Error = error;
        result.Valid = error == null && missing == 0 && result.Offered == result.Admitted + result.Rejected;
        return result;
        async Task SendLoop(int index)
        {
            ClientConnection connection = connections[index];
            byte[] header = new byte[16];
            int timeout = (int)Math.Min(int.MaxValue, (seconds + options.DrainSeconds + 1) * 1000);
            using var wire = new Wire(connection.Socket, io, failure.Token, timeout, timeout, options.IoCap);
            try
            {
                await foreach (Request request in sendQueues[index].Reader.ReadAllAsync(failure.Token))
                {
                    request.Sequence = connection.Sequence++;
                    long firstSend = request.FirstSend = Stopwatch.GetTimestamp();
                    metrics[index].Started++;
                    Wire.Header(header, request.Frame.Payload.Length, 2, request.Sequence);
                    var frame = request.Frame;
                    summaries[index].Add(
request.Sequence,
frame.Payload.Length,
options.Mode == "transport" ? 0 : frame.Records,
options.Mode == "transport" ? (ulong)frame.Payload.Length : frame.Hash,
options.Mode == "transport" ? ZeroCounts : frame.Counts,
options.Mode == "transport" ? ZeroSums : frame.Sums);
                    // Once published, the ACK reader can return this descriptor to the pool.
                    // The sender only uses its copied timestamp and immutable frame after this point.
                    if (!ackQueues[index].Writer.TryWrite(request))
                    {
                        throw new InvalidOperationException("ack queue overflow");
                    }

                    await wire.Send(header, firstSend);
                    await wire.Send(frame.Payload, firstSend);
                }
            }
            catch (Exception e)
            {
                Failure(e);
            }
            finally
            {
                ackQueues[index].Writer.TryComplete();
            }
        }

        async Task AckLoop(int index)
        {
            byte[] response = new byte[32];
            var m = metrics[index];
            int timeout = (int)Math.Min(int.MaxValue, (seconds + options.DrainSeconds + 1) * 1000);
            using var wire = new Wire(connections[index].Socket, io, failure.Token, timeout, timeout, options.IoCap);
            try
            {
                await foreach (Request request in ackQueues[index].Reader.ReadAllAsync(failure.Token))
                {
                    await wire.ReadExact(response.AsMemory(0, 16), request.FirstSend);
                    var header = Wire.ParseHeader(response);
                    if (header.Type != 0x8002 || header.Sequence != request.Sequence || header.Length != 16)
                    {
                        throw new InvalidDataException("data_ack_header");
                    }

                    await wire.ReadExact(response.AsMemory(16, 16), request.FirstSend);
                    long now = Stopwatch.GetTimestamp();
                    ulong expectedRecords = options.Mode == "transport" ? 0 : request.Frame.Records;
                    ulong expectedDigest = options.Mode == "transport" ? (ulong)request.Frame.Payload.Length : request.Frame.Hash;
                    if (BinaryPrimitives.ReadUInt64BigEndian(response.AsSpan(16)) != expectedRecords || BinaryPrimitives.ReadUInt64BigEndian(response.AsSpan(24)) != expectedDigest)
                    {
                        throw new InvalidDataException("data_ack_integrity");
                    }

                    m.Acknowledged++;
                    m.Records += (long)expectedRecords;
                    m.Bytes += request.Frame.Payload.Length;
                    if (now < end)
                    {
                        m.WindowCompleted++;
                        m.WindowRecords += (long)expectedRecords;
                        m.WindowBytes += request.Frame.Payload.Length;
                    }

                    long latency = Histogram.Nanoseconds(now - request.Intended);
                    m.Scheduled.Record(latency);
                    m.Submitted.RecordTicks(now - request.FirstSend);
                    m.Delay.RecordTicks(request.FirstSend - request.Intended);
                    m.Sizes[ConnectionMetrics.SizeClass(request.Frame.Payload.Length)].Record(latency);
                    if (latency > 1_000_000)
                    {
                        m.Miss1ms++;
                    }

                    if (latency > 5_000_000)
                    {
                        m.Miss5ms++;
                    }

                    if (latency > 10_000_000)
                    {
                        m.Miss10ms++;
                    }

                    admission.Return(request);
                }
            }
            catch (Exception e)
            {
                Failure(e);
            }
        }

        void Failure(Exception e)
        {
            if (e is OperationCanceledException && error == null)
            {
                drainExpired = true;
                Interlocked.CompareExchange(ref error, "drain_deadline", null);
            }
            else
            {
                Interlocked.CompareExchange(ref error, e.Message, null);
            }

            failure.Cancel();
        }
    }

    static readonly ulong[] ZeroCounts = new ulong[64];
    static readonly long[] ZeroSums = new long[64];
}
