#include "bench.hpp"

#include <algorithm>
#include <bit>
#include <cmath>
#include <mutex>
#include <thread>

namespace bench
{
namespace
{
constexpr size_t ack_bytes = 32;
constexpr size_t size_classes = 5; // payload <=4 KiB, <=64 KiB, <=256 KiB, <=1 MiB, larger

size_t size_class(size_t n)
{
    return n <= 4096 ? 0 : n <= 65536 ? 1 : n <= 262144 ? 2 : n <= 1048576 ? 3 : 4;
}

struct Slot
{
    size_t frame = 0;
    uint64_t sequence = 0;
    int64_t intended = 0, first_send = 0;
};

// Written only by one connection's ACK reader during a phase; merged after the threads join.
struct Measure
{
    uint64_t acknowledged = 0, failed = 0, records = 0, bytes = 0;
    uint64_t window_frames = 0, window_records = 0, window_bytes = 0, miss1 = 0, miss5 = 0,
             miss10 = 0;
    Histogram scheduled, submitted, delay;
    std::array<Histogram, size_classes> sizes;

    void merge(const Measure &m)
    {
        acknowledged += m.acknowledged;
        failed += m.failed;
        records += m.records;
        bytes += m.bytes;
        window_frames += m.window_frames;
        window_records += m.window_records;
        window_bytes += m.window_bytes;
        miss1 += m.miss1;
        miss5 += m.miss5;
        miss10 += m.miss10;
        scheduled.merge(m.scheduled);
        submitted.merge(m.submitted);
        delay.merge(m.delay);
        for (size_t i = 0; i < size_classes; ++i)
        {
            sizes[i].merge(m.sizes[i]);
        }
    }
};

// One connection. The producer (generator, or the sender in closed loop) appends at `tail`,
// the sender advances `sent`, the ACK reader advances `head`. Slots in [head, tail) are live.
struct Lane
{
    Socket socket;
    size_t cursor = 0;     // connection-local corpus cursor; persists across phases
    uint64_t sequence = 0; // last request sequence number
    Epoch expected;        // End summary the server must report for this epoch
    std::vector<Slot> ring;
    std::atomic<uint64_t> tail = 0, sent = 0, head = 0;
    std::atomic<uint32_t> signal = 0; // bumped on every state change; waiters block on it
    std::atomic<bool> sender_done = false;
    uint64_t produced = 0; // closed loop: admitted by this lane's sender
    int64_t finished = 0;  // when this lane's ACK reader stopped
    Measure measure;

    void poke()
    {
        signal.fetch_add(1, std::memory_order_release);
        signal.notify_all();
    }

    // Blocks until ready() or stop; the signal load precedes the check to avoid lost wakeups.
    template <class Ready> bool await(Ready ready, const std::atomic<bool> &stop)
    {
        for (;;)
        {
            uint32_t seen = signal.load(std::memory_order_acquire);
            if (ready())
            {
                return true;
            }
            if (stop.load(std::memory_order_acquire))
            {
                return false;
            }
            signal.wait(seen, std::memory_order_acquire);
        }
    }
};

uint64_t splitmix(uint64_t &x)
{
    uint64_t z = (x += 0x9e3779b97f4a7c15ull);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}

// Offsets in ticks from T0 for one phase's Poisson or burst arrivals, computed before T0.
std::vector<int64_t> arrival_offsets(const Options &o, double seconds)
{
    std::vector<int64_t> offsets;
    if (o.arrival == "steady")
    {
        return offsets;
    }
    uint64_t rng = o.seed;
    double t = 0;
    for (uint64_t i = 0;; ++i)
    {
        if (o.arrival == "poisson")
        {
            double u = (double(splitmix(rng) >> 11) + 1.0) / 9007199254740993.0;
            t += -std::log(u) / o.rate;
        }
        else
        {
            // Five seconds at 0.6R, then one second at 1.5R, repeated.
            double mass = 4.5 * o.rate, cycle = std::floor(double(i) / mass);
            double rest = double(i) - cycle * mass;
            t = 6 * cycle + (rest < 3 * o.rate ? rest / (0.6 * o.rate)
                                               : 5 + (rest - 3 * o.rate) / (1.5 * o.rate));
        }
        if (t >= seconds)
        {
            return offsets;
        }
        require(offsets.size() < 10000000, "schedule exceeds the 10M arrival storage limit");
        offsets.push_back(to_ticks(t));
    }
}

// Steady arrival i is at T0 + ticks(i / rate); count those strictly before T1.
uint64_t steady_count(double rate, double duration, int64_t window_ticks)
{
    auto count = uint64_t(std::ceil(duration * rate));
    while (count && to_ticks(double(count - 1) / rate) >= window_ticks)
    {
        --count;
    }
    while (to_ticks(double(count) / rate) < window_ticks)
    {
        ++count;
    }
    return count;
}

// Cursor of `lane` after `remaining` more round-robin arrivals starting at `ordinal`.
size_t advance_cursor(
    size_t cursor, size_t lane, uint64_t ordinal, uint64_t remaining, size_t lanes, size_t frames)
{
    uint64_t first = (lane + lanes - ordinal % lanes) % lanes;
    if (first < remaining)
    {
        uint64_t count = 1 + (remaining - 1 - first) / lanes;
        cursor = (cursor + (count % frames) * lanes) % frames;
    }
    return cursor;
}

// Process-local high-resolution timer, then at most 1 ms of spinning (the timer fires up to
// ~0.5 ms late on this host); no global timer-resolution change.
void pace_until(HANDLE timer, int64_t target)
{
    const int64_t spin = to_ticks(0.001);
    for (int64_t left = target - now(); left > 0; left = target - now())
    {
        if (left > spin)
        {
            LARGE_INTEGER due{};
            due.QuadPart = -std::max<int64_t>(1, int64_t(to_seconds(left - spin) * 1e7));
            SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE);
            WaitForSingleObject(timer, INFINITE);
        }
        else
        {
            YieldProcessor();
        }
    }
}

void set_timeouts(SOCKET s, DWORD ms)
{
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char *>(&ms), sizeof(ms));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char *>(&ms), sizeof(ms));
}

std::atomic<uint64_t> send_calls = 0, receive_calls = 0, sent_bytes = 0, received_bytes = 0;

void send_all(SOCKET s, WSABUF *buffers, DWORD count, int cap)
{
    while (count)
    {
        WSABUF limited = buffers[0];
        DWORD used = count, n = 0;
        if (cap && limited.len > ULONG(cap))
        {
            limited.len = ULONG(cap); // diagnostic partial-send path: one capped buffer
            used = 1;
        }
        ++send_calls;
        require(WSASend(s, cap ? &limited : buffers, cap ? 1 : used, &n, 0, nullptr, nullptr) == 0,
                "send_failed");
        sent_bytes += n;
        while (count && n >= buffers[0].len)
        {
            n -= buffers[0].len;
            ++buffers;
            --count;
        }
        if (count)
        {
            buffers[0].buf += n;
            buffers[0].len -= n;
        }
    }
}

size_t receive_some(SOCKET s, uint8_t *data, size_t n, int cap)
{
    ++receive_calls;
    int got =
        recv(s, reinterpret_cast<char *>(data), int(std::min<size_t>(n, cap ? cap : INT_MAX)), 0);
    require(got > 0, got == 0 ? "response_eof" : "receive_failed");
    received_bytes += uint64_t(got);
    return size_t(got);
}

void receive_all(SOCKET s, uint8_t *data, size_t n, int cap)
{
    while (n)
    {
        size_t got = receive_some(s, data, n, cap);
        data += got;
        n -= got;
    }
}

// Begin (type 1) or End (type 3) on one connection, outside the measured data interval.
void control(Lane &lane, const Options &o, uint16_t type)
{
    set_timeouts(lane.socket.value, DWORD(std::min(o.drain * 1000, 4.0e9)));
    auto h = make_header(type, ++lane.sequence, type == 1 ? 32 : 0);
    WSABUF buffers[2] = {{ULONG(h.size()), reinterpret_cast<char *>(h.data())},
                         {32, reinterpret_cast<char *>(const_cast<uint8_t *>(o.manifest.data()))}};
    send_all(lane.socket.value, buffers, type == 1 ? 2 : 1, o.io_cap);
    std::array<uint8_t, header_bytes + summary_bytes> reply{};
    const size_t body = type == 1 ? 0 : summary_bytes;
    receive_all(lane.socket.value, reply.data(), header_bytes + body, o.io_cap);
    require(read_be(reply.data(), 4) == body && read_be(reply.data() + 4, 2) == 1 &&
                read_be(reply.data() + 6, 2) == (type | 0x8000u) &&
                read_be(reply.data() + 8, 8) == lane.sequence,
            "control_response");
    if (type == 3)
    {
        auto want = lane.expected.encode();
        require(std::equal(want.begin(), want.end(), reply.begin() + header_bytes),
                "end_summary_mismatch");
    }
    lane.expected = {};
    set_timeouts(lane.socket.value, 0); // data I/O is bounded by the phase watchdog instead
}

struct PhaseResult
{
    Measure measure;
    uint64_t offered = 0, admitted = 0, rejected = 0, late_rejected = 0, aborted_rejected = 0;
    uint64_t late_over_1ms = 0, queue_high = 0, bytes_high = 0;
    Histogram lateness;
    double seconds = 0, cohort_seconds = 0, drain_seconds = 0;
    bool timed_out = false;
    std::string error;
    Resources before, after;

    uint64_t unresolved() const
    {
        return admitted - measure.acknowledged - measure.failed;
    }

    // One validity rule for warmup and measurement.
    bool valid() const
    {
        return error.empty() && measure.failed == 0 && unresolved() == 0 &&
               offered == admitted + rejected;
    }
};

class Phase
{
    const Options &o;
    const Corpus &corpus;
    std::vector<std::unique_ptr<Lane>> &lanes;
    const bool closed_loop;
    const uint64_t depth; // closed loop: outstanding frames per connection
    int64_t start = 0, end = 0, deadline = 0;
    std::atomic<bool> go = false, failed = false, producing = true;
    std::atomic<uint64_t> outstanding = 0, outstanding_bytes = 0;
    std::vector<int64_t> offsets; // scheduled arrivals of this phase, relative to T0
    uint64_t planned = 0;
    std::mutex error_mutex;
    PhaseResult r;

    // The first failure wins; later socket errors caused by the shutdown are not reported.
    void fail_phase(std::string_view why, bool drain_expired = false)
    {
        {
            std::lock_guard lock(error_mutex);
            if (r.error.empty())
            {
                r.error = why;
                r.timed_out = drain_expired;
            }
        }
        failed = true;
        for (auto &lane : lanes)
        {
            // shutdown() alone does not release a thread blocked in recv; cancel its I/O too.
            shutdown(lane->socket.value, SD_BOTH);
            CancelIoEx(reinterpret_cast<HANDLE>(lane->socket.value), nullptr);
            lane->poke();
        }
    }

    Result expected(const Frame &f) const
    {
        return o.mode == Mode::transport ? transport_result(f.length) : f.expected;
    }

    void send(Lane &lane, Slot &slot)
    {
        const Frame &f = corpus.frames[slot.frame];
        auto h = make_header(2, slot.sequence, f.length);
        WSABUF buffers[2] = {
            {ULONG(h.size()), reinterpret_cast<char *>(h.data())},
            {ULONG(f.length), reinterpret_cast<char *>(const_cast<uint8_t *>(f.payload.data()))}};
        send_all(lane.socket.value, buffers, 2, o.io_cap);
    }

    void sender(Lane &lane)
    {
        try
        {
            go.wait(false);
            for (;;)
            {
                uint64_t i = lane.sent.load(std::memory_order_relaxed);
                if (closed_loop)
                {
                    // Refill this connection's own slot as soon as an ACK frees one.
                    if (!lane.await(
                            [&] {
                                return i - lane.head.load(std::memory_order_acquire) < depth;
                            },
                            failed))
                    {
                        break;
                    }
                    int64_t t = now();
                    if (t >= end)
                    {
                        break;
                    }
                    Slot &s = lane.ring[i % lane.ring.size()];
                    s = {lane.cursor, ++lane.sequence, t, t};
                    lane.cursor = (lane.cursor + lanes.size()) % corpus.frames.size();
                    ++lane.produced;
                    lane.tail.store(i + 1, std::memory_order_release);
                }
                else
                {
                    if (!lane.await(
                            [&] {
                                return i < lane.tail.load(std::memory_order_acquire) ||
                                       !producing.load(std::memory_order_acquire);
                            },
                            failed) ||
                        i == lane.tail.load(std::memory_order_acquire))
                    {
                        break;
                    }
                    lane.ring[i % lane.ring.size()].first_send = now();
                }
                // Publish before sending: the ACK reader may consume this slot immediately after.
                lane.sent.store(i + 1, std::memory_order_release);
                lane.poke();
                send(lane, lane.ring[i % lane.ring.size()]);
            }
        }
        catch (const std::exception &e)
        {
            fail_phase(e.what());
        }
        lane.sender_done = true;
        lane.poke();
    }

    void reader(Lane &lane)
    {
        std::vector<uint8_t> buffer(ack_bytes * std::min<size_t>(lane.ring.size(), 256));
        size_t have = 0;
        uint64_t h = 0;
        try
        {
            for (;;)
            {
                if (!lane.await(
                        [&] {
                            return h < lane.sent.load(std::memory_order_acquire) ||
                                   lane.sender_done.load(std::memory_order_acquire);
                        },
                        failed) ||
                    h == lane.sent.load(std::memory_order_acquire))
                {
                    break; // failed, or everything sent has been acknowledged
                }
                // Never read past the responses owed, so End controls start on a frame boundary.
                size_t owed =
                    size_t(lane.sent.load(std::memory_order_acquire) - h) * ack_bytes - have;
                have += receive_some(lane.socket.value,
                                     buffer.data() + have,
                                     std::min(owed, buffer.size() - have),
                                     o.io_cap);
                const int64_t t = now();
                size_t used = 0;
                for (; have - used >= ack_bytes; used += ack_bytes, ++h)
                {
                    acknowledge(lane, lane.ring[h % lane.ring.size()], buffer.data() + used, t);
                    lane.head.store(h + 1, std::memory_order_release);
                }
                std::copy(buffer.begin() + ptrdiff_t(used),
                          buffer.begin() + ptrdiff_t(have),
                          buffer.begin());
                have -= used;
                if (used)
                {
                    lane.poke();
                }
            }
        }
        catch (const std::exception &e)
        {
            fail_phase(e.what());
        }
        lane.finished = now();
    }

    void acknowledge(Lane &lane, const Slot &s, const uint8_t *ack, int64_t t)
    {
        const Frame &f = corpus.frames[s.frame];
        const Result want = expected(f);
        Measure &m = lane.measure;
        if (read_be(ack, 4) != 16 || read_be(ack + 4, 2) != 1 || read_be(ack + 6, 2) != 0x8002 ||
            read_be(ack + 8, 8) != s.sequence || read_be(ack + 16, 8) != want.records ||
            read_be(ack + 24, 8) != want.digest)
        {
            ++m.failed;
            fail("ack_mismatch");
        }
        lane.expected.add(want, f.length, s.sequence);
        ++m.acknowledged;
        m.records += want.records;
        m.bytes += f.length;
        if (t < end)
        {
            ++m.window_frames;
            m.window_records += want.records;
            m.window_bytes += f.length;
        }
        const uint64_t latency = to_ns(t - s.intended);
        m.scheduled.add(latency);
        m.submitted.add(to_ns(t - s.first_send));
        m.delay.add(to_ns(s.first_send - s.intended));
        m.sizes[size_class(f.length)].add(latency);
        m.miss1 += latency > 1000000;
        m.miss5 += latency > 5000000;
        m.miss10 += latency > 10000000;
        if (!closed_loop)
        {
            // Release: this slot's reads happen before the generator may reuse its index.
            outstanding.fetch_sub(1, std::memory_order_release);
            outstanding_bytes.fetch_sub(f.length, std::memory_order_release);
        }
    }

    // Scheduled arrivals: independent of ACKs; admission never waits.
    void generate(HANDLE timer)
    {
        uint64_t i = 0;
        for (; i < planned && !failed.load(std::memory_order_relaxed); ++i)
        {
            const int64_t intended =
                start + (o.arrival == "steady" ? to_ticks(double(i) / o.rate) : offsets[i]);
            pace_until(timer, intended);
            Lane &lane = *lanes[i % lanes.size()];
            const size_t frame = lane.cursor;
            lane.cursor = (lane.cursor + lanes.size()) % corpus.frames.size();
            const size_t length = corpus.frames[frame].length;
            const int64_t t = now();
            const uint64_t late = to_ns(t - intended);
            r.lateness.add(late);
            r.late_over_1ms += late > 1000000;
            ++r.offered;
            if (t >= end + to_ticks(0.001))
            {
                ++r.rejected; // over 1 ms past T1: the generator could not keep up (jitter at T1 is only lateness)
                ++r.late_rejected;
                continue;
            }
            const uint64_t frames = outstanding.load(std::memory_order_acquire);
            const uint64_t bytes = outstanding_bytes.load(std::memory_order_acquire);
            if (frames >= uint64_t(o.window) || length > o.inflight_bytes - bytes)
            {
                ++r.rejected;
                continue;
            }
            outstanding.fetch_add(1, std::memory_order_relaxed);
            outstanding_bytes.fetch_add(length, std::memory_order_relaxed);
            r.queue_high = std::max(r.queue_high, frames + 1);
            r.bytes_high = std::max(r.bytes_high, bytes + length);
            ++r.admitted;
            const uint64_t k = lane.tail.load(std::memory_order_relaxed);
            lane.ring[k % lane.ring.size()] = {frame, ++lane.sequence, intended, 0};
            lane.tail.store(k + 1, std::memory_order_release);
            lane.poke();
        }
        if (i < planned)
        {
            // Early fatal error: keep the complete intended demand visible and cursors aligned.
            const uint64_t remaining = planned - i;
            for (size_t l = 0; l < lanes.size(); ++l)
            {
                lanes[l]->cursor = advance_cursor(
                    lanes[l]->cursor, l, i, remaining, lanes.size(), corpus.frames.size());
            }
            r.offered += remaining;
            r.rejected += remaining;
            r.aborted_rejected += remaining;
        }
    }

  public:
    Phase(const Options &options,
          const Corpus &c,
          std::vector<std::unique_ptr<Lane>> &l,
          double seconds)
        : o(options), corpus(c), lanes(l), closed_loop(options.rate == 0),
          depth(uint64_t(options.window) / l.size())
    {
        r.seconds = seconds;
    }

    PhaseResult run()
    {
        for (auto &lane : lanes)
        {
            control(*lane, o, 1);
            lane->tail = lane->sent = lane->head = 0;
            lane->sender_done = false;
            lane->produced = 0;
            lane->measure = {};
        }
        // Everything that can fail, and the schedule's cost, comes before threads start and T0.
        offsets = closed_loop ? std::vector<int64_t>{} : arrival_offsets(o, r.seconds);
        planned = closed_loop             ? 0
                  : o.arrival == "steady" ? steady_count(o.rate, r.seconds, to_ticks(r.seconds))
                                          : offsets.size();
        HANDLE timer = CreateWaitableTimerExW(
            nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
        require(timer != nullptr, "pacing_timer");
        r.before = Resources::sample();
        std::vector<std::thread> threads;
        for (auto &lane : lanes)
        {
            threads.emplace_back([this, l = lane.get()] {
                sender(*l);
            });
            threads.emplace_back([this, l = lane.get()] {
                reader(*l);
            });
        }
        start = now() + to_ticks(0.1);
        end = start + to_ticks(r.seconds);
        deadline = end + to_ticks(o.drain);
        // This thread paces arrivals; it must not be preempted by the connection workers.
        const int priority = GetThreadPriority(GetCurrentThread());
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
        pace_until(timer, start);
        go = true;
        go.notify_all();
        if (!closed_loop)
        {
            try
            {
                generate(timer);
            }
            catch (const std::exception &e)
            {
                fail_phase(e.what());
            }
            producing = false;
            for (auto &lane : lanes)
            {
                lane->poke();
            }
        }
        // Watchdog: all admitted data must finish by T1 + drain.
        auto drained = [&] {
            for (auto &lane : lanes)
            {
                if (!lane->sender_done || lane->head != lane->sent)
                {
                    return false;
                }
            }
            return true;
        };
        while (!failed && !drained())
        {
            if (now() >= deadline)
            {
                fail_phase("drain_deadline", true);
                break;
            }
            Sleep(1);
        }
        SetThreadPriority(GetCurrentThread(), priority);
        for (auto &t : threads)
        {
            t.join();
        }
        if (!failed)
        {
            pace_until(timer, end); // a healthy phase still spans its whole window before End
        }
        CloseHandle(timer);
        int64_t finished = start;
        for (auto &lane : lanes)
        {
            finished = std::max(finished, lane->finished);
            r.measure.merge(lane->measure);
            if (closed_loop)
            {
                r.offered += lane->produced;
                r.admitted += lane->produced;
            }
        }
        r.cohort_seconds = std::max(r.seconds, to_seconds(finished - start));
        r.drain_seconds = std::max(0.0, to_seconds(finished - end));
        if (!failed)
        {
            try
            {
                for (auto &lane : lanes)
                {
                    control(*lane, o, 3);
                }
            }
            catch (const std::exception &e)
            {
                r.error = e.what();
            }
        }
        r.after = Resources::sample();
        return std::move(r);
    }
};
} // namespace

void client_selftest()
{
    for (size_t lanes = 1; lanes <= 7; ++lanes)
    {
        for (size_t frames = 1; frames <= 9; ++frames)
        {
            for (uint64_t ordinal = 0; ordinal < 15; ++ordinal)
            {
                for (uint64_t remaining = 0; remaining < 20; ++remaining)
                {
                    for (size_t lane = 0; lane < lanes; ++lane)
                    {
                        size_t want = lane % frames;
                        for (uint64_t i = ordinal; i < ordinal + remaining; ++i)
                        {
                            want = i % lanes == lane ? (want + lanes) % frames : want;
                        }
                        require(advance_cursor(
                                    lane % frames, lane, ordinal, remaining, lanes, frames) == want,
                                "selftest: aborted cursor arithmetic");
                    }
                }
            }
        }
    }
    for (double rate : {0.1, 1.0, 3.0, 10.0, 1000.0})
    {
        for (double duration : {0.001, 0.1, 0.3, 1.0})
        {
            uint64_t want = 0;
            while (to_ticks(double(want) / rate) < to_ticks(duration))
            {
                ++want;
            }
            require(steady_count(rate, duration, to_ticks(duration)) == want,
                    "selftest: steady arrival count");
        }
    }
    uint64_t seed = 0;
    require(splitmix(seed) == 0xe220a8397b1dcdafull, "selftest: SplitMix64 vector");
}

int run_client(const Options &o)
{
    const Corpus corpus = load_corpus(o.corpus);
    require(corpus.max_frame <= o.inflight_bytes, "inflight-bytes must fit every corpus frame");
    require(o.rate > 0 || uint64_t(o.window) * corpus.max_frame <= o.inflight_bytes,
            "closed loop requires window x largest frame <= inflight-bytes");
    require(uint64_t(o.window) * uint64_t(o.connections) <= (uint64_t(1) << 22),
            "window x connections exceeds the descriptor budget");
    std::vector<std::unique_ptr<Lane>> lanes;
    int send_buffer = 0, receive_buffer = 0;
    for (int i = 0; i < o.connections; ++i)
    {
        auto lane = std::make_unique<Lane>();
        // Overlapped handle: a non-overlapped socket serializes the sender's blocking send
        // behind the reader's blocking recv (Windows synchronous file-object I/O).
        lane->socket.value =
            WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_OVERLAPPED);
        require(lane->socket.value != INVALID_SOCKET, "client_socket");
        configure_socket(lane->socket.value, o.socket_buffer);
        const sockaddr_in address = loopback(o.port);
        require(connect(lane->socket.value,
                        reinterpret_cast<const sockaddr *>(&address),
                        sizeof(address)) == 0,
                "connect");
        std::tie(send_buffer, receive_buffer) = socket_buffers(lane->socket.value);
        lane->cursor = size_t(i) % corpus.frames.size();
        // Scheduled: a lane can hold the whole global window. Closed loop: its own depth.
        lane->ring.resize(o.rate > 0 ? size_t(o.window) : size_t(o.window / o.connections));
        lanes.push_back(std::move(lane));
    }
    if (o.warmup > 0)
    {
        PhaseResult warm = Phase(o, corpus, lanes, o.warmup).run();
        require(warm.valid(), "warmup failed: " + warm.error);
    }
    PhaseResult r = Phase(o, corpus, lanes, o.duration).run();
    const Measure &m = r.measure;
    const uint64_t unresolved = r.unresolved();
    const bool valid = r.valid();
    DWORD_PTR process_mask = 0, system_mask = 0;
    GetProcessAffinityMask(GetCurrentProcess(), &process_mask, &system_mask);

    Json j;
    j.open()
        .str("role", "client")
        .str("implementation", "cpp")
        .str("mode", mode_name(o.mode))
        .boolean("valid", valid);
    r.error.empty() ? j.null("error") : j.str("error", r.error);
    j.str("load_model", o.rate == 0 ? "closed-loop" : "scheduled")
        .open("config")
        .num("connections", o.connections)
        .num("window", o.window)
        .num("depth_per_connection", o.rate == 0 ? o.window / o.connections : 0)
        .num("inflight_bytes", uint64_t(o.inflight_bytes))
        .num("rate", o.rate)
        .raw("arrival", o.rate == 0 ? "null" : bench::quoted(o.arrival)) // closed loop: no schedule
        .raw("seed", o.rate == 0 ? "null" : std::to_string(o.seed))
        .num("duration", o.duration)
        .num("warmup", o.warmup)
        .num("drain_seconds", o.drain)
        .num("socket_buffer", o.socket_buffer)
        .num("io_cap", o.io_cap)
        .close()
        .open("corpus")
        .num("frames", uint64_t(corpus.frames.size()))
        .num("payload_bytes", corpus.payload_bytes)
        .num("max_frame_bytes", corpus.max_frame)
        .close()
        .open("counts")
        .num("offered", r.offered)
        .num("admitted", r.admitted)
        .num("rejected", r.rejected)
        .num("acknowledged", m.acknowledged)
        .num("failed", m.failed)
        .num("unresolved", unresolved)
        .num("timedout", r.timed_out ? unresolved : 0)
        .num("generator_late_rejected", r.late_rejected)
        .num("aborted_schedule_rejected", r.aborted_rejected)
        .close()
        .open("window")
        .num("seconds", r.seconds)
        .num("frames", m.window_frames)
        .num("records", m.window_records)
        .num("payload_bytes", m.window_bytes)
        .close()
        .open("cohort")
        .num("seconds", r.cohort_seconds)
        .num("frames", m.acknowledged)
        .num("records", m.records)
        .num("payload_bytes", m.bytes)
        .num("drain_seconds", r.drain_seconds)
        .num("drain_limit_seconds", o.drain)
        .close()
        .open("latency");
    m.scheduled.write(j, "scheduled");
    m.submitted.write(j, "submitted");
    m.delay.write(j, "client_delay");
    j.open_array("by_size");
    for (const Histogram &h : m.sizes)
    {
        h.write(j, {});
    }
    j.close_array()
        .close()
        .open("misses")
        .num("over_1ms", m.miss1 + r.rejected + m.failed + unresolved)
        .num("over_5ms", m.miss5 + r.rejected + m.failed + unresolved)
        .num("over_10ms", m.miss10 + r.rejected + m.failed + unresolved)
        .close()
        .open("generator");
    r.lateness.write(j, "lateness");
    j.num("late_over_1ms", r.late_over_1ms);
    // Closed loop keeps every connection at its configured depth; there is no admission queue.
    o.rate == 0 ? j.null("queue_high_water") : j.num("queue_high_water", r.queue_high);
    o.rate == 0 ? j.null("inflight_bytes_high_water")
                : j.num("inflight_bytes_high_water", r.bytes_high);
    j.close()
        .open("resources")
        .str("scope", "measured phase: Begin through End controls")
        .num("seconds", to_seconds(r.after.at - r.before.at))
        .num("cpu_seconds", r.after.cpu_seconds - r.before.cpu_seconds)
        .num("logical_cpus", uint64_t(std::popcount(uint64_t(process_mask))))
        .num("allocations", r.after.new_calls - r.before.new_calls)
        .num("allocated_bytes", r.after.new_bytes - r.before.new_bytes)
        .null("gc")
        .num("peak_working_set_bytes", r.after.peak_working_set)
        .close()
        .open("io")
        .str("scope", "client lifetime including warmup and controls")
        .num("send_calls", send_calls.load())
        .num("receive_calls", receive_calls.load())
        .num("sent_bytes", sent_bytes.load())
        .num("received_bytes", received_bytes.load())
        .num("socket_send_buffer", send_buffer)
        .num("socket_receive_buffer", receive_buffer)
        .close();
    write_build(j);
    j.close();
    emit(o, j.take());
    return valid ? 0 : 1;
}
} // namespace bench
