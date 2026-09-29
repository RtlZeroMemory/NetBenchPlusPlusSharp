using System.Buffers.Binary;
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading.Channels;
using Microsoft.Win32.SafeHandles;

namespace TcpBench;

sealed class Slot
{
    public int Frame;
    public ulong Sequence;
    public long Intended, FirstSend;
}

// Written only by one connection's ACK reader during a phase; merged after the phase.
sealed class Measure
{
    public long Acknowledged, Failed, Records, Bytes, WindowFrames, WindowRecords, WindowBytes, Miss1, Miss5, Miss10;
    public readonly Histogram Scheduled = new(), Submitted = new(), Delay = new();
    public readonly Histogram[] Sizes = [new(), new(), new(), new(), new()];

    public static int SizeClass(int n) => n <= 4096 ? 0 : n <= 65536 ? 1 : n <= 262144 ? 2 : n <= 1048576 ? 3 : 4;

    public void Merge(Measure m)
    {
        Acknowledged += m.Acknowledged;
        Failed += m.Failed;
        Records += m.Records;
        Bytes += m.Bytes;
        WindowFrames += m.WindowFrames;
        WindowRecords += m.WindowRecords;
        WindowBytes += m.WindowBytes;
        Miss1 += m.Miss1;
        Miss5 += m.Miss5;
        Miss10 += m.Miss10;
        Scheduled.Merge(m.Scheduled);
        Submitted.Merge(m.Submitted);
        Delay.Merge(m.Delay);
        for (int i = 0; i < Sizes.Length; i++)
        {
            Sizes[i].Merge(m.Sizes[i]);
        }
    }
}

// One connection: the producer (generator, or the sender in closed loop) fills ring slots, the
// sender publishes each to the ACK queue before sending, and the ACK reader recycles it.
sealed class Lane(Socket socket, int cursor, int capacity)
{
    public readonly Socket Socket = socket;
    public int Cursor = cursor; // connection-local corpus cursor; persists across phases
    public ulong Sequence;
    public Summary Expected = new();
    public readonly Slot[] Ring = Enumerable.Range(0, capacity).Select(_ => new Slot()).ToArray();
    public long Tail, Produced, Finished;
    public Channel<Slot> ToSend = null!, ToAck = null!;
    public SemaphoreSlim Credits = null!;
    public Measure Measure = new();
}

static class Arrivals
{
    public static ulong SplitMix(ref ulong x)
    {
        ulong z = x = unchecked(x + 0x9E3779B97F4A7C15UL);
        z = unchecked((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL);
        z = unchecked((z ^ (z >> 27)) * 0x94D049BB133111EBUL);
        return z ^ (z >> 31);
    }

    static long Ticks(double seconds) => (long)(seconds * Stopwatch.Frequency);

    // Offsets in ticks from T0 for one phase's Poisson or burst arrivals, computed before T0.
    public static long[] Offsets(Options o, double seconds)
    {
        var offsets = new List<long>();
        ulong rng = o.Seed;
        double t = 0;
        for (long i = 0; ; i++)
        {
            if (o.Arrival == "poisson")
            {
                double u = ((SplitMix(ref rng) >> 11) + 1.0) / 9007199254740993.0;
                t += -Math.Log(u) / o.Rate;
            }
            else
            {
                // Five seconds at 0.6R, then one second at 1.5R, repeated.
                double mass = 4.5 * o.Rate, cycle = Math.Floor(i / mass), rest = i - cycle * mass;
                t = 6 * cycle + (rest < 3 * o.Rate ? rest / (.6 * o.Rate) : 5 + (rest - 3 * o.Rate) / (1.5 * o.Rate));
            }

            if (t >= seconds)
            {
                return [.. offsets];
            }

            if (offsets.Count == 10_000_000)
            {
                throw new BenchException("schedule exceeds the 10M arrival storage limit");
            }

            offsets.Add(Ticks(t));
        }
    }

    // Steady arrival i is at T0 + ticks(i / rate); count those strictly before T1.
    public static long SteadyCount(double rate, double duration, long windowTicks)
    {
        long count = (long)Math.Ceiling(duration * rate);
        while (count > 0 && Ticks((count - 1) / rate) >= windowTicks)
        {
            count--;
        }

        while (Ticks(count / rate) < windowTicks)
        {
            count++;
        }

        return count;
    }

    // Cursor of `lane` after `remaining` more round-robin arrivals starting at `ordinal`.
    public static int AdvanceCursor(int cursor, int lane, long ordinal, long remaining, int lanes, int frames)
    {
        long first = (lane + lanes - ordinal % lanes) % lanes;
        if (first < remaining)
        {
            long count = 1 + (remaining - 1 - first) / lanes;
            cursor = (int)((cursor + count % frames * lanes) % frames);
        }

        return cursor;
    }
}

// Process-local high-resolution waitable timer, then at most 1 ms of spinning (the timer fires
// up to ~0.5 ms late on this host); no global timer-resolution change.
sealed class Pacer : IDisposable
{
    readonly EventWaitHandle timer = new(false, EventResetMode.AutoReset);
    readonly WaitHandle[] handles;
    readonly long spin = Stopwatch.Frequency / 1000;

    public Pacer(WaitHandle cancel)
    {
        IntPtr handle = CreateWaitableTimerExW(IntPtr.Zero, null, 2 /* high resolution */, 0x1F0003);
        if (handle == IntPtr.Zero)
        {
            throw new BenchException("pacing_timer");
        }

        timer.SafeWaitHandle = new SafeWaitHandle(handle, true);
        handles = [timer, cancel];
    }

    // Returns false when cancelled.
    public bool Wait(long target)
    {
        for (long left = target - Stopwatch.GetTimestamp(); left > 0; left = target - Stopwatch.GetTimestamp())
        {
            if (left > spin)
            {
                long due = -Math.Max(1, (long)((Int128)(left - spin) * 10_000_000 / Stopwatch.Frequency));
                SetWaitableTimer(timer.SafeWaitHandle, ref due, 0, IntPtr.Zero, IntPtr.Zero, false);
                if (WaitHandle.WaitAny(handles) == 1)
                {
                    return false;
                }
            }
            else
            {
                Thread.SpinWait(8);
            }
        }

        return true;
    }

    public void Dispose() => timer.Dispose();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWaitableTimerExW(IntPtr attributes, string? name, uint flags, uint access);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetWaitableTimer(SafeWaitHandle timer, ref long dueTime, int period, IntPtr completion, IntPtr argument, [MarshalAs(UnmanagedType.Bool)] bool resume);
}

sealed class Phase(Options o, Corpus corpus, List<Lane> lanes, double seconds)
{
    readonly bool closedLoop = o.Rate == 0;
    readonly CancellationTokenSource failure = new();
    readonly object gate = new();
    readonly Histogram lateness = new();
    readonly Measure measure = new();
    long start, end, deadline, outstanding, outstandingBytes, planned;
    long[] offsets = []; // scheduled arrivals of this phase, relative to T0
    long offered, admitted, rejected, lateRejected, abortedRejected, lateOver1ms, queueHigh, bytesHigh;
    string? error;
    bool timedOut;
    public static long SendCalls, ReceiveCalls, SentBytes, ReceivedBytes;

    // The first failure wins; later socket errors caused by the shutdown are not reported.
    void Fail(string why, bool drainExpired = false)
    {
        lock (gate)
        {
            if (error != null)
            {
                return;
            }

            error = why;
            timedOut = drainExpired;
        }

        failure.Cancel();
        foreach (Lane lane in lanes)
        {
            try
            {
                lane.Socket.Shutdown(SocketShutdown.Both); // unblocks pending send/receive
            }
            catch (SocketException)
            {
            }
        }
    }

    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder))] // no box per suspension
    static async ValueTask Send(Socket s, ReadOnlyMemory<byte> data, int cap, CancellationToken token)
    {
        while (!data.IsEmpty)
        {
            int n = await s.SendAsync(cap > 0 && data.Length > cap ? data[..cap] : data, SocketFlags.None, token);
            Interlocked.Increment(ref SendCalls);
            Interlocked.Add(ref SentBytes, n);
            data = data[n..];
        }
    }

    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder<>))] // no box per suspension
    static async ValueTask<int> ReceiveSome(Socket s, Memory<byte> data, int cap, CancellationToken token)
    {
        int n = await s.ReceiveAsync(cap > 0 && data.Length > cap ? data[..cap] : data, SocketFlags.None, token);
        Interlocked.Increment(ref ReceiveCalls);
        Interlocked.Add(ref ReceivedBytes, n);
        return n > 0 ? n : throw new BenchException("response_eof");
    }

    // Begin (1) or End (3) on one connection, outside the measured data interval.
    async Task Control(Lane lane, ushort type)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(o.DrainSeconds));
        byte[] request = new byte[type == 1 ? 48 : 16], reply = new byte[type == 1 ? 16 : 16 + 1056];
        Options.Header(request, type == 1 ? 32 : 0, type, ++lane.Sequence);
        if (type == 1)
        {
            o.Manifest.CopyTo(request.AsSpan(16));
        }
        await Send(lane.Socket, request, o.IoCap, timeout.Token);
        for (int have = 0; have < reply.Length;)
        {
            have += await ReceiveSome(lane.Socket, reply.AsMemory(have), o.IoCap, timeout.Token);
        }

        if (BinaryPrimitives.ReadUInt32BigEndian(reply) != reply.Length - 16 || BinaryPrimitives.ReadUInt16BigEndian(reply.AsSpan(4)) != 1 ||
            BinaryPrimitives.ReadUInt16BigEndian(reply.AsSpan(6)) != (type | 0x8000) || BinaryPrimitives.ReadUInt64BigEndian(reply.AsSpan(8)) != lane.Sequence)
        {
            throw new BenchException("control_response");
        }

        if (type == 3 && !lane.Expected.Matches(reply.AsSpan(16)))
        {
            throw new BenchException("end_summary_mismatch");
        }

        lane.Expected = new Summary();
    }

    // One gathered send per request, as in the native client; --io-cap sends capped pieces.
    [AsyncMethodBuilder(typeof(PoolingAsyncValueTaskMethodBuilder))] // no box per suspension
    async ValueTask SendRequest(Lane lane, byte[] header, byte[] payload, ArraySegment<byte>[] segments, CancellationToken token)
    {
        int sent = 0;
        if (o.IoCap == 0)
        {
            segments[1] = payload;
            sent = await lane.Socket.SendAsync(segments, SocketFlags.None);
            Interlocked.Increment(ref SendCalls);
            Interlocked.Add(ref SentBytes, sent);
        }

        if (sent < header.Length)
        {
            await Send(lane.Socket, header.AsMemory(sent), o.IoCap, token);
            sent = header.Length;
        }

        await Send(lane.Socket, payload.AsMemory(sent - header.Length), o.IoCap, token);
    }

    async Task Sender(Lane lane)
    {
        byte[] header = new byte[16];
        var segments = new ArraySegment<byte>[] { header, default };
        CancellationToken token = failure.Token;
        try
        {
            while (true)
            {
                Slot s;
                if (closedLoop)
                {
                    // Refill this connection's own slot as soon as an ACK frees one.
                    await lane.Credits.WaitAsync(token);
                    long t = Stopwatch.GetTimestamp();
                    if (t >= end)
                    {
                        break;
                    }

                    s = lane.Ring[lane.Tail++ % lane.Ring.Length];
                    s.Frame = lane.Cursor;
                    lane.Cursor = (lane.Cursor + lanes.Count) % corpus.Frames.Length;
                    s.Sequence = ++lane.Sequence;
                    s.Intended = s.FirstSend = t;
                    lane.Produced++;
                }
                else
                {
                    if (!await lane.ToSend.Reader.WaitToReadAsync(token) || !lane.ToSend.Reader.TryRead(out Slot? next))
                    {
                        break;
                    }

                    s = next;
                    s.FirstSend = Stopwatch.GetTimestamp();
                }

                // Publish before sending: the ACK reader may consume this slot immediately after.
                lane.ToAck.Writer.TryWrite(s);
                byte[] payload = corpus.Frames[s.Frame].Payload;
                Options.Header(header, payload.Length, 2, s.Sequence);
                await SendRequest(lane, header, payload, segments, token);
            }
        }
        catch (Exception e) when (e is BenchException or SocketException or OperationCanceledException or ObjectDisposedException)
        {
            Fail(e is OperationCanceledException ? "cancelled" : e.Message);
        }

        lane.ToAck.Writer.TryComplete();
    }

    async Task Reader(Lane lane)
    {
        byte[] buffer = new byte[32 * Math.Min(lane.Ring.Length, 256)];
        int have = 0;
        ChannelReader<Slot> queue = lane.ToAck.Reader;
        try
        {
            while (await queue.WaitToReadAsync(failure.Token))
            {
                // Never read past the responses owed, so End controls start on a frame boundary.
                int owed = queue.Count * 32 - have;
                have += await ReceiveSome(lane.Socket, buffer.AsMemory(have, Math.Min(owed, buffer.Length - have)), o.IoCap, failure.Token);
                long t = Stopwatch.GetTimestamp();
                int used = 0;
                for (; have - used >= 32; used += 32)
                {
                    queue.TryRead(out Slot? s);
                    Acknowledge(lane, s!, buffer.AsSpan(used, 32), t);
                }

                buffer.AsSpan(used, have - used).CopyTo(buffer);
                have -= used;
            }
        }
        catch (Exception e) when (e is BenchException or SocketException or OperationCanceledException or ObjectDisposedException)
        {
            Fail(e is OperationCanceledException ? "cancelled" : e.Message);
        }

        lane.Finished = Stopwatch.GetTimestamp();
    }

    void Acknowledge(Lane lane, Slot s, ReadOnlySpan<byte> ack, long t)
    {
        CorpusFrame f = corpus.Frames[s.Frame];
        bool transport = o.Mode == Mode.Transport;
        ulong records = transport ? 0 : f.Records, hash = transport ? (ulong)f.Payload.Length : f.Hash;
        Measure m = lane.Measure;
        if (BinaryPrimitives.ReadUInt32BigEndian(ack) != 16 || BinaryPrimitives.ReadUInt16BigEndian(ack[4..]) != 1 ||
            BinaryPrimitives.ReadUInt16BigEndian(ack[6..]) != 0x8002 || BinaryPrimitives.ReadUInt64BigEndian(ack[8..]) != s.Sequence ||
            BinaryPrimitives.ReadUInt64BigEndian(ack[16..]) != records || BinaryPrimitives.ReadUInt64BigEndian(ack[24..]) != hash)
        {
            m.Failed++;
            throw new BenchException("ack_mismatch");
        }

        lane.Expected.Add(s.Sequence, f.Payload.Length, records, hash, transport ? Summary.NoCounts : f.Counts, transport ? Summary.NoSums : f.Sums);
        m.Acknowledged++;
        m.Records += (long)records;
        m.Bytes += f.Payload.Length;
        if (t < end)
        {
            m.WindowFrames++;
            m.WindowRecords += (long)records;
            m.WindowBytes += f.Payload.Length;
        }

        long latency = Histogram.Nanoseconds(t - s.Intended);
        m.Scheduled.Record(latency);
        m.Submitted.RecordTicks(t - s.FirstSend);
        m.Delay.RecordTicks(s.FirstSend - s.Intended);
        m.Sizes[Measure.SizeClass(f.Payload.Length)].Record(latency);
        m.Miss1 += latency > 1_000_000 ? 1 : 0;
        m.Miss5 += latency > 5_000_000 ? 1 : 0;
        m.Miss10 += latency > 10_000_000 ? 1 : 0;
        if (closedLoop)
        {
            lane.Credits.Release();
        }
        else
        {
            Interlocked.Decrement(ref outstanding);
            Interlocked.Add(ref outstandingBytes, -f.Payload.Length);
        }
    }

    // Scheduled arrivals, independent of ACKs; admission never waits. Runs on its own thread.
    void Generate(Pacer pacer)
    {
        long i = 0;
        for (; i < planned && !failure.IsCancellationRequested; i++)
        {
            long intended = start + (o.Arrival == "steady" ? (long)(i / o.Rate * Stopwatch.Frequency) : offsets[i]);
            if (!pacer.Wait(intended))
            {
                break;
            }

            Lane lane = lanes[(int)(i % lanes.Count)];
            int frame = lane.Cursor;
            lane.Cursor = (lane.Cursor + lanes.Count) % corpus.Frames.Length;
            int length = corpus.Frames[frame].Payload.Length;
            long t = Stopwatch.GetTimestamp();
            long late = Histogram.Nanoseconds(t - intended);
            lateness.Record(late);
            lateOver1ms += late > 1_000_000 ? 1 : 0;
            offered++;
            if (t >= end + Stopwatch.Frequency / 1000)
            {
                rejected++; // over 1 ms past T1: the generator could not keep up (jitter at T1 is only lateness)
                lateRejected++;
                continue;
            }

            long frames = Volatile.Read(ref outstanding), bytes = Volatile.Read(ref outstandingBytes);
            if (frames >= o.Window || length > o.InflightBytes - bytes)
            {
                rejected++;
                continue;
            }

            Interlocked.Increment(ref outstanding);
            Interlocked.Add(ref outstandingBytes, length);
            queueHigh = Math.Max(queueHigh, frames + 1);
            bytesHigh = Math.Max(bytesHigh, bytes + length);
            admitted++;
            Slot s = lane.Ring[lane.Tail++ % lane.Ring.Length];
            s.Frame = frame;
            s.Sequence = ++lane.Sequence;
            s.Intended = intended;
            lane.ToSend.Writer.TryWrite(s);
        }

        if (i < planned)
        {
            // Early fatal error: keep the complete intended demand visible and cursors aligned.
            long remaining = planned - i;
            for (int l = 0; l < lanes.Count; l++)
            {
                lanes[l].Cursor = Arrivals.AdvanceCursor(lanes[l].Cursor, l, i, remaining, lanes.Count, corpus.Frames.Length);
            }

            offered += remaining;
            rejected += remaining;
            abortedRejected += remaining;
        }
    }

    public async Task<(bool Valid, string? Error, object? Report)> Run(bool report)
    {
        foreach (Lane lane in lanes)
        {
            await Control(lane, 1);
            var bounded = new BoundedChannelOptions(lane.Ring.Length) { SingleReader = true, SingleWriter = true };
            lane.ToSend = Channel.CreateBounded<Slot>(bounded);
            lane.ToAck = Channel.CreateBounded<Slot>(bounded);
            lane.Credits = new SemaphoreSlim(lane.Ring.Length);
            lane.Tail = lane.Produced = lane.Finished = 0;
            lane.Measure = new Measure();
        }

        // The schedule is built before T0 so its cost never delays the first arrivals.
        if (!closedLoop)
        {
            offsets = o.Arrival == "steady" ? [] : Arrivals.Offsets(o, seconds);
            planned = o.Arrival == "steady" ? Arrivals.SteadyCount(o.Rate, seconds, (long)(seconds * Stopwatch.Frequency)) : offsets.Length;
        }

        using var pacer = new Pacer(failure.Token.WaitHandle);
        Resources before = Resources.Sample();
        start = Stopwatch.GetTimestamp() + Stopwatch.Frequency / 10;
        end = start + (long)(seconds * Stopwatch.Frequency);
        deadline = end + (long)(o.DrainSeconds * Stopwatch.Frequency);
        pacer.Wait(start);

        var workers = lanes.SelectMany(lane => new[] { Task.Run(() => Sender(lane)), Task.Run(() => Reader(lane)) }).ToList();
        if (!closedLoop)
        {
            await Task.Factory.StartNew(() =>
            {
                Thread.CurrentThread.Priority = ThreadPriority.Highest; // pacing must not be preempted by workers
                try
                {
                    Generate(pacer);
                }
                catch (BenchException e)
                {
                    Fail(e.Message);
                }
            }, TaskCreationOptions.LongRunning);
            foreach (Lane lane in lanes)
            {
                lane.ToSend.Writer.TryComplete();
            }
        }

        // Watchdog: all admitted data must finish by T1 + drain.
        Task all = Task.WhenAll(workers);
        long left = deadline - Stopwatch.GetTimestamp();
        if (await Task.WhenAny(all, Task.Delay(TimeSpan.FromSeconds(Math.Max(0, (double)left / Stopwatch.Frequency)))) != all)
        {
            Fail("drain_deadline", drainExpired: true);
        }

        await all;
        if (error == null)
        {
            pacer.Wait(end); // a healthy phase still spans its whole window before End
        }

        long finished = start;
        foreach (Lane lane in lanes)
        {
            finished = Math.Max(finished, lane.Finished);
            measure.Merge(lane.Measure);
            if (closedLoop)
            {
                offered += lane.Produced;
                admitted += lane.Produced;
            }
        }

        double cohortSeconds = Math.Max(seconds, (double)(finished - start) / Stopwatch.Frequency);
        double drainSeconds = Math.Max(0, (double)(finished - end) / Stopwatch.Frequency);
        if (error == null)
        {
            try
            {
                foreach (Lane lane in lanes)
                {
                    await Control(lane, 3);
                }
            }
            catch (Exception e) when (e is BenchException or SocketException or OperationCanceledException)
            {
                error = e is OperationCanceledException ? "control_timeout" : e.Message;
            }
        }

        Resources after = Resources.Sample();
        long unresolved = admitted - measure.Acknowledged - measure.Failed;
        bool valid = error == null && measure.Failed == 0 && unresolved == 0 && offered == admitted + rejected;
        if (!report)
        {
            return (valid, error, null);
        }

        long misses = rejected + measure.Failed + unresolved;
        return (valid, error, new
        {
            role = "client",
            implementation = "csharp",
            mode = Options.Name(o.Mode),
            valid,
            error,
            load_model = closedLoop ? "closed-loop" : "scheduled",
            config = new
            {
                connections = o.Connections,
                window = o.Window,
                depth_per_connection = closedLoop ? o.Window / o.Connections : 0,
                inflight_bytes = o.InflightBytes,
                rate = o.Rate,
                arrival = closedLoop ? null : o.Arrival, // closed loop has no schedule
                seed = closedLoop ? (ulong?)null : o.Seed,
                duration = o.Duration,
                warmup = o.Warmup,
                drain_seconds = o.DrainSeconds,
                socket_buffer = o.SocketBuffer,
                io_cap = o.IoCap
            },
            corpus = new { frames = corpus.Frames.Length, payload_bytes = corpus.PayloadBytes, max_frame_bytes = corpus.MaxFrame },
            counts = new
            {
                offered,
                admitted,
                rejected,
                acknowledged = measure.Acknowledged,
                failed = measure.Failed,
                unresolved,
                timedout = timedOut ? unresolved : 0,
                generator_late_rejected = lateRejected,
                aborted_schedule_rejected = abortedRejected
            },
            window = new { seconds, frames = measure.WindowFrames, records = measure.WindowRecords, payload_bytes = measure.WindowBytes },
            cohort = new
            {
                seconds = cohortSeconds,
                frames = measure.Acknowledged,
                records = measure.Records,
                payload_bytes = measure.Bytes,
                drain_seconds = drainSeconds,
                drain_limit_seconds = o.DrainSeconds
            },
            latency = new
            {
                scheduled = measure.Scheduled.Export(),
                submitted = measure.Submitted.Export(),
                client_delay = measure.Delay.Export(),
                by_size = measure.Sizes.Select(h => h.Export()).ToArray()
            },
            misses = new { over_1ms = measure.Miss1 + misses, over_5ms = measure.Miss5 + misses, over_10ms = measure.Miss10 + misses },
            generator = new
            {
                lateness = lateness.Export(),
                late_over_1ms = lateOver1ms,
                queue_high_water = closedLoop ? (long?)null : queueHigh,
                inflight_bytes_high_water = closedLoop ? (long?)null : bytesHigh
            },
            resources = new
            {
                scope = "measured phase: Begin through End controls",
                seconds = after.Seconds(before),
                cpu_seconds = after.CpuSeconds - before.CpuSeconds,
                logical_cpus = Environment.ProcessorCount,
                allocations = (long?)null,
                allocated_bytes = after.Allocated - before.Allocated,
                gc = after.Gc(before),
                peak_working_set_bytes = Resources.PeakWorkingSet()
            },
            io = new
            {
                scope = "client lifetime including warmup and controls",
                send_calls = SendCalls,
                receive_calls = ReceiveCalls,
                sent_bytes = SentBytes,
                received_bytes = ReceivedBytes,
                socket_send_buffer = lanes[0].Socket.SendBufferSize,
                socket_receive_buffer = lanes[0].Socket.ReceiveBufferSize
            },
            build = Build.Info()
        });
    }
}

static class Client
{
    public static async Task<int> Run(Options o)
    {
        var corpus = new Corpus(o.Corpus);
        if (corpus.MaxFrame > o.InflightBytes)
        {
            throw new BenchException("inflight-bytes must fit every corpus frame");
        }

        if (o.Rate == 0 && (long)o.Window * corpus.MaxFrame > o.InflightBytes)
        {
            throw new BenchException("closed loop requires window x largest frame <= inflight-bytes");
        }

        if ((long)o.Window * o.Connections > 1L << 22)
        {
            throw new BenchException("window x connections exceeds the descriptor budget");
        }

        var lanes = new List<Lane>();
        try
        {
            for (int i = 0; i < o.Connections; i++)
            {
                var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                o.Configure(socket);
                // Scheduled: a lane can hold the whole global window. Closed loop: its own depth.
                lanes.Add(new Lane(socket, i % corpus.Frames.Length, o.Rate > 0 ? o.Window : o.Window / o.Connections));
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(o.DrainSeconds));
                await socket.ConnectAsync(new IPEndPoint(IPAddress.Loopback, o.Port), timeout.Token);
            }

            if (o.Warmup > 0 && await new Phase(o, corpus, lanes, o.Warmup).Run(report: false) is { Valid: false } warm)
            {
                throw new BenchException("warmup failed: " + warm.Error);
            }

            var measured = await new Phase(o, corpus, lanes, o.Duration).Run(report: true);
            JsonOutput.Emit(o.Output, measured.Report!);
            return measured.Valid ? 0 : 1;
        }
        finally
        {
            foreach (Lane lane in lanes)
            {
                lane.Socket.Dispose();
            }
        }
    }
}
