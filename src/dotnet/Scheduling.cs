using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace TcpBench;

static class ArrivalSchedule
{
    public static ulong SplitMix(ref ulong seed)
    {
        ulong z = seed = unchecked(seed + 0x9E3779B97F4A7C15UL);
        z = unchecked((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL);
        z = unchecked((z ^ (z >> 27)) * 0x94D049BB133111EBUL);
        return z ^ (z >> 31);
    }

    public static long[] Poisson(double seconds, double rate, ulong seed)
    {
        // ponytail: precompute at most 10M arrivals; a streamed schedule producer is needed beyond this explicit memory cap.
        if (seconds * rate > 9_500_000)
        {
            throw new ArgumentException("Poisson schedule exceeds the explicit 10M arrival storage limit");
        }

        var times = new List<long>((int)Math.Min(10_000_000, Math.Ceiling(seconds * rate * 1.01 + 100)));
        double time = 0;
        while (true)
        {
            double u = ((SplitMix(ref seed) >> 11) + 1.0) / 9007199254740993.0;
            time += -Math.Log(u) / rate;
            if (time >= seconds)
            {
                return times.ToArray();
            }

            if (times.Count == 10_000_000)
            {
                throw new ArgumentException("Poisson schedule exceeds 10M arrivals");
            }

            times.Add((long)(time * Stopwatch.Frequency));
        }
    }

    public static long[] Burst(double seconds, double rate)
    {
        // The finite schedule makes each arrival independent of ACKs and leaves RNG/piecewise generation outside timing.
        if (seconds * rate * 1.5 > 10_000_000)
        {
            throw new ArgumentException("burst schedule exceeds the explicit 10M arrival storage limit");
        }

        var times = new List<long>();
        for (long i = 0; ; i++)
        {
            double mass = 4.5 * rate, cycle = Math.Floor(i / mass), remainder = i - cycle * mass;
            double time = 6 * cycle + (remainder < 3 * rate ? remainder / (.6 * rate) : 5 + (remainder - 3 * rate) / (1.5 * rate));
            if (time >= seconds)
            {
                break;
            }

            times.Add((long)(time * Stopwatch.Frequency));
        }

        return times.ToArray();
    }
}

sealed class Pacer : IDisposable
{
    const uint HighResolutionTimer = 2;
    const uint TimerModifyAndSynchronize = 0x00100002;
    readonly EventWaitHandle timer = new(false, EventResetMode.AutoReset);
    readonly CancellationToken token;
    readonly WaitHandle[] waits;
    readonly long spinTicks = Math.Max(1, Stopwatch.Frequency / 5000);
    public Pacer(CancellationToken token)
    {
        this.token = token;
        IntPtr handle = CreateWaitableTimerExW(IntPtr.Zero, null, HighResolutionTimer, TimerModifyAndSynchronize);
        if (handle == IntPtr.Zero)
        {
            timer.Dispose();
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }

        SafeWaitHandle old = timer.SafeWaitHandle;
        timer.SafeWaitHandle = new SafeWaitHandle(handle, true);
        old.Dispose();
        waits = [timer, token.WaitHandle];
    }

    public void Wait(long target)
    {
        while (true)
        {
            token.ThrowIfCancellationRequested();
            long remaining = target - Stopwatch.GetTimestamp();
            if (remaining <= 0)
            {
                return;
            }

            if (remaining > spinTicks)
            {
                long due = -(long)((Int128)(remaining - spinTicks) * 10_000_000 / Stopwatch.Frequency);
                if (!SetWaitableTimer(timer.SafeWaitHandle, ref due, 0, IntPtr.Zero, IntPtr.Zero, false))
                {
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }

                if (WaitHandle.WaitAny(waits) == 1)
                {
                    token.ThrowIfCancellationRequested();
                }
            }
            else
            {
                Thread.SpinWait(16);
            }
        }
    }

    public void Dispose()
    {
        timer.Dispose();
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWaitableTimerExW(IntPtr attributes, string? name, uint flags, uint access);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool SetWaitableTimer(SafeWaitHandle timer, ref long dueTime, int period, IntPtr completion, IntPtr argument, [MarshalAs(UnmanagedType.Bool)] bool resume);
}
