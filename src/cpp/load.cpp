#include "bench.hpp"

namespace bench
{
struct ClientConnection
{
    Socket socket;
    uint64_t sequence = 0;
    size_t cursor = 0;
    Epoch expected;
    int send_head = -1, send_tail = -1, ack_head = -1, ack_tail = -1;
};

struct Request
{
    int send_next = -1, ack_next = -1;
    size_t frame = 0;
    uint64_t sequence = 0;
    int64_t scheduled = 0, first = 0;
};

struct Metrics
{
    uint64_t offered = 0, admitted = 0, first_send = 0, acknowledged = 0, rejected = 0,
             bad_results = 0, timedout = 0, late_rejected = 0, aborted_rejected = 0,
             scheduler_late = 0;
    uint64_t records = 0, bytes = 0, window_completed = 0, window_records = 0, window_bytes = 0,
             queue_high = 0, bytes_high = 0;
    uint64_t missed1 = 0, missed5 = 0, missed10 = 0;
    int64_t start = 0, end = 0, finish = 0;
    std::string error;
    Histogram scheduled, submitted, delay, lateness;
    std::array<Histogram, 5> sizes; // payload <=4KiB, <=64KiB, <=256KiB, <=1MiB, larger
};

static Result expected(const Options &o, const Frame &f)
{
    if (o.mode != "transport")
    {
        return f.expected;
    }
    Result r;
    r.digest = f.length;
    return r;
}

static void control(ClientConnection &c, const Options &o, uint16_t type)
{
    require(c.sequence != UINT64_MAX, "client_sequence_overflow");
    uint64_t seq = ++c.sequence;
    auto h = header(type, seq, type == 1 ? 32 : 0);
    int64_t deadline = now() + ticks(o.drain);
    send_exact(c.socket.value, h.data(), h.size(), o.io_cap, deadline);
    if (type == 1)
    {
        send_exact(c.socket.value, o.manifest.data(), 32, o.io_cap, deadline);
    }
    std::array<uint8_t, 16> reply{};
    receive_exact(c.socket.value, reply.data(), reply.size(), o.io_cap, deadline);
    check_response(reply.data(), type, seq, type == 1 ? 0 : summary_size);
    if (type == 3)
    {
        std::array<uint8_t, summary_size> b{};
        receive_exact(c.socket.value, b.data(), b.size(), o.io_cap, deadline);
        require(b == c.expected.encode(), "end_summary_mismatch");
    }
    else
    {
        c.expected = {};
    }
}

static uint64_t splitmix(uint64_t &x)
{
    uint64_t z = (x += 0x9e3779b97f4a7c15ull);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}

static double gap(uint64_t &rng, double rate)
{
    double u = (double(splitmix(rng) >> 11) + 1.0) / 9007199254740993.0;
    return -std::log(u) / rate;
}

static std::vector<int64_t> arrival_offsets(const Options &options, double duration)
{
    std::vector<int64_t> arrivals;
    if (options.rate == 0 || options.arrival == "steady")
    {
        return arrivals;
    }
    uint64_t rng = options.seed;
    double elapsed = options.arrival == "poisson" ? gap(rng, options.rate) : 0;
    for (uint64_t ordinal = 0; elapsed < duration; ++ordinal)
    {
        require(arrivals.size() < 10000000, "schedule_limit_10000000");
        arrivals.push_back(ticks(elapsed));
        if (options.arrival == "poisson")
        {
            elapsed += gap(rng, options.rate);
        }
        else
        {
            double count = double(ordinal + 1);
            double cycle = std::floor(count / (4.5 * options.rate));
            double remaining = count - cycle * 4.5 * options.rate;
            elapsed = 6 * cycle + (remaining < 3 * options.rate
                                       ? remaining / (.6 * options.rate)
                                       : 5 + (remaining - 3 * options.rate) / (1.5 * options.rate));
        }
    }
    return arrivals;
}

static uint64_t scheduled_count(const Options &options,
                                const std::vector<int64_t> &arrivals,
                                double duration,
                                int64_t window_ticks)
{
    if (options.rate == 0)
    {
        return 0;
    }
    if (options.arrival != "steady")
    {
        return uint64_t(std::lower_bound(arrivals.begin(), arrivals.end(), window_ticks) -
                        arrivals.begin());
    }
    uint64_t count = uint64_t(std::ceil(duration * options.rate));
    // Both floating schedules and integer QPC ticks must remain strictly before T1.
    while (count && (double(count - 1) / options.rate >= duration ||
                     ticks(double(count - 1) / options.rate) >= window_ticks))
    {
        --count;
    }
    while (double(count) / options.rate < duration &&
           ticks(double(count) / options.rate) < window_ticks)
    {
        ++count;
    }
    return count;
}

static size_t advance_aborted_cursor(size_t cursor,
                                     size_t connection,
                                     uint64_t ordinal,
                                     uint64_t remaining,
                                     size_t connections,
                                     size_t frames)
{
    uint64_t first = (connection + connections - ordinal % connections) % connections;
    if (first < remaining)
    {
        uint64_t count = 1 + (remaining - 1 - first) / connections;
        cursor = (cursor + (count % frames) * connections) % frames;
    }
    return cursor;
}

void load_selftest()
{
    for (size_t connections = 1; connections <= 7; ++connections)
    {
        for (size_t frames = 1; frames <= 9; ++frames)
        {
            for (uint64_t ordinal = 0; ordinal < 15; ++ordinal)
            {
                for (uint64_t remaining = 0; remaining < 20; ++remaining)
                {
                    for (size_t connection = 0; connection < connections; ++connection)
                    {
                        size_t cursor = connection % frames;
                        size_t want = cursor;
                        for (uint64_t i = ordinal; i < ordinal + remaining; ++i)
                        {
                            if (i % connections == connection)
                            {
                                want = (want + connections) % frames;
                            }
                        }
                        require(advance_aborted_cursor(
                                    cursor, connection, ordinal, remaining, connections, frames) ==
                                    want,
                                "selftest_aborted_cursor");
                    }
                }
            }
        }
    }
    Options options;
    for (double rate : {.1, 1.0, 3.0, 10.0, 1000.0})
    {
        options.rate = rate;
        for (double duration : {.001, .1, .3, 1.0})
        {
            uint64_t want = 0;
            while (double(want) / rate < duration && ticks(double(want) / rate) < ticks(duration))
            {
                ++want;
            }
            require(scheduled_count(options, {}, duration, ticks(duration)) == want,
                    "selftest_scheduled_count");
        }
    }
}

static std::unique_ptr<Metrics> phase(const Options &o,
                                      const std::vector<Frame> &corpus,
                                      std::vector<std::unique_ptr<ClientConnection>> &connections,
                                      double duration)
{
    auto metrics = std::make_unique<Metrics>();
    auto &m = *metrics;

    struct Handle
    {
        HANDLE value;

        ~Handle()
        {
            if (value)
            {
                CloseHandle(value);
            }
        }
    } timer{CreateWaitableTimerExW(
        nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS)},
        cancel{CreateEventW(nullptr, TRUE, FALSE, nullptr)};

    require(timer.value && cancel.value, "high_resolution_timer");
    auto arrivals = arrival_offsets(o, duration);
    for (auto &c : connections)
    {
        control(*c, o, 1);
    }
    // ponytail: a global mutex owns the bounded descriptor pool; shard only if client calibration
    // shows contention.
    std::mutex mutex;
    std::condition_variable cv;
    bool stopping = false;
    std::atomic<bool> failed = false;
    size_t occupied = 0, occupied_bytes = 0;
    std::vector<Request> pool(size_t(o.window));
    std::vector<int> free;
    free.reserve(size_t(o.window));
    for (int i = o.window; i > 0;)
    {
        free.push_back(--i);
    }
    std::vector<std::thread> threads;
    threads.reserve(connections.size() * 2);
    auto abort = [&](std::string_view why) {
        std::lock_guard lock(mutex);
        if (!failed)
        {
            failed = true;
            SetEvent(cancel.value);
            m.error = why;
            if (why.ends_with("timeout") || why == "io_deadline")
            {
                m.timedout = occupied - m.bad_results;
            }
            for (auto &c : connections)
            {
                shutdown(c->socket.value, SD_BOTH);
            }
        }
        cv.notify_all();
    };
    auto pace = [&](int64_t until) {
        // Process-local high-resolution timer, then a bounded 200us spin. No system timer-period
        // changes.
        while (!failed)
        {
            int64_t remaining = until - now();
            if (remaining <= 0)
            {
                break;
            }
            if (remaining > ticks(.0002))
            {
                LARGE_INTEGER due{};
                due.QuadPart =
                    -std::max(int64_t(1), int64_t(seconds(remaining - ticks(.0002)) * 10000000));
                if (!SetWaitableTimer(timer.value, &due, 0, nullptr, nullptr, FALSE))
                {
                    abort("pacing_timer");
                    break;
                }
                HANDLE handles[] = {timer.value, cancel.value};
                DWORD wait = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
                if (wait == WAIT_FAILED)
                {
                    abort("pacing_wait");
                    break;
                }
            }
            else
            {
                YieldProcessor();
            }
        }
    };
    int64_t data_deadline = 0;
    auto send_requests = [&](ClientConnection *c) {
        try
        {
            for (;;)
            {
                size_t frame_index;
                uint64_t seq;
                {
                    std::unique_lock lock(mutex);
                    cv.wait(lock, [&] {
                        return stopping || failed || c->send_head != -1;
                    });
                    if (failed || (stopping && c->send_head == -1))
                    {
                        return;
                    }
                    int id = c->send_head;
                    auto &r = pool[size_t(id)];
                    c->send_head = r.send_next;
                    if (c->send_head == -1)
                    {
                        c->send_tail = -1;
                    }
                    r.first = now();
                    frame_index = r.frame;
                    seq = r.sequence;
                    ++m.first_send;
                    cv.notify_all();
                }
                const auto &f = corpus[frame_index];
                auto h = header(2, seq, f.length);
                send_exact(c->socket.value, h.data(), h.size(), o.io_cap, data_deadline);
                send_exact(c->socket.value, f.payload.data(), f.length, o.io_cap, data_deadline);
            }
        }
        catch (const std::exception &e)
        {
            abort(e.what());
        }
    };
    auto read_acknowledgements = [&](ClientConnection *c) {
        try
        {
            for (;;)
            {
                int id;
                Request r;
                {
                    std::unique_lock lock(mutex);
                    cv.wait(lock, [&] {
                        return failed || stopping ||
                               (c->ack_head != -1 && pool[size_t(c->ack_head)].first != 0);
                    });
                    if (failed || (stopping && c->ack_head == -1))
                    {
                        return;
                    }
                    if (c->ack_head == -1)
                    {
                        continue;
                    }
                    id = c->ack_head;
                    r = pool[size_t(id)];
                    if (!r.first)
                    {
                        continue;
                    }
                }
                std::array<uint8_t, 32> reply{};
                receive_exact(c->socket.value, reply.data(), reply.size(), o.io_cap, data_deadline);
                const auto &f = corpus[r.frame];
                auto want = expected(o, f);
                bool valid = read_be(reply.data(), 4) == 16 && read_be(reply.data() + 4, 2) == 1 &&
                             read_be(reply.data() + 6, 2) == 0x8002 &&
                             read_be(reply.data() + 8, 8) == r.sequence &&
                             read_be(reply.data() + 16, 8) == want.records &&
                             read_be(reply.data() + 24, 8) == want.digest;
                int64_t stamp = now();
                {
                    std::lock_guard lock(mutex);
                    if (failed)
                    {
                        return;
                    }
                    if (!valid)
                    {
                        ++m.bad_results;
                        fail("ack_mismatch");
                    }
                    c->expected.add(want, f.length, r.sequence);
                    ++m.acknowledged;
                    m.records += want.records;
                    m.bytes += f.length;
                    if (stamp < m.end)
                    {
                        ++m.window_completed;
                        m.window_records += want.records;
                        m.window_bytes += f.length;
                    }
                    auto latency = nanoseconds(stamp - r.scheduled);
                    m.scheduled.add(latency);
                    m.submitted.add(nanoseconds(stamp - r.first));
                    m.delay.add(nanoseconds(r.first - r.scheduled));
                    m.sizes[f.length <= 4096      ? 0
                            : f.length <= 65536   ? 1
                            : f.length <= 262144  ? 2
                            : f.length <= 1048576 ? 3
                                                  : 4]
                        .add(latency);
                    if (latency > 1000000)
                    {
                        ++m.missed1;
                    }
                    if (latency > 5000000)
                    {
                        ++m.missed5;
                    }
                    if (latency > 10000000)
                    {
                        ++m.missed10;
                    }
                    c->ack_head = pool[size_t(id)].ack_next;
                    if (c->ack_head == -1)
                    {
                        c->ack_tail = -1;
                    }
                    // Only the ACK owner recycles a slot; its sender no longer references it.
                    free.push_back(id);
                    --occupied;
                    occupied_bytes -= f.length;
                    cv.notify_all();
                }
            }
        }
        catch (const std::exception &e)
        {
            abort(e.what());
        }
    };
    for (auto &connection : connections)
    {
        auto *c = connection.get();
        c->send_head = c->send_tail = c->ack_head = c->ack_tail = -1;
        threads.emplace_back(send_requests, c);
        threads.emplace_back(read_acknowledgements, c);
    }
    // Published before the first admission unlocks the pool mutex and wakes either worker.
    m.start = now() + ticks(.05);
    m.end = m.start + ticks(duration);
    data_deadline = m.end + ticks(o.drain);
    uint64_t planned = scheduled_count(o, arrivals, duration, m.end - m.start);
    uint64_t ordinal = 0;
    while (true)
    {
        if (o.rate > 0 && ordinal >= planned)
        {
            break;
        }
        int64_t scheduled = o.rate > 0
                                ? m.start + (o.arrival == "steady" ? ticks(double(ordinal) / o.rate)
                                                                   : arrivals[size_t(ordinal)])
                                : now();
        if (o.rate == 0 && scheduled < m.start)
        {
            scheduled = m.start;
        }
        if (scheduled >= m.end)
        {
            break;
        }
        pace(scheduled);
        std::unique_lock lock(mutex);
        if (failed)
        {
            break;
        }
        size_t ci = size_t(ordinal % connections.size());
        auto &c = *connections[ci];
        const auto &f = corpus[c.cursor];
        if (o.rate == 0)
        {
            while (!failed && now() < m.end &&
                   (free.empty() || f.length > o.inflight_bytes - occupied_bytes))
            {
                cv.wait_for(lock, std::chrono::milliseconds(1));
            }
            if (failed || now() >= m.end)
            {
                break;
            }
            scheduled = now();
        }
        ++m.offered;
        size_t frame = c.cursor;
        c.cursor = (c.cursor + connections.size()) % corpus.size();
        ++ordinal;
        int64_t offered_at = now();
        auto late = nanoseconds(offered_at - scheduled);
        m.lateness.add(late);
        if (late > 1000000)
        {
            ++m.scheduler_late;
        }
        if (o.rate > 0 && offered_at >= m.end)
        {
            ++m.rejected;
            ++m.late_rejected;
        }
        else if (free.empty() || f.length > o.inflight_bytes - occupied_bytes)
        {
            ++m.rejected;
        }
        else
        {
            int id = free.back();
            free.pop_back();
            auto &r = pool[size_t(id)];
            r = {};
            r.frame = frame;
            r.scheduled = scheduled;
            require(c.sequence != UINT64_MAX, "client_sequence_overflow");
            r.sequence = ++c.sequence;
            if (c.send_tail != -1)
            {
                pool[size_t(c.send_tail)].send_next = id;
            }
            else
            {
                c.send_head = id;
            }
            c.send_tail = id;
            if (c.ack_tail != -1)
            {
                pool[size_t(c.ack_tail)].ack_next = id;
            }
            else
            {
                c.ack_head = id;
            }
            c.ack_tail = id;
            ++m.admitted;
            ++occupied;
            occupied_bytes += f.length;
            m.queue_high = std::max(m.queue_high, uint64_t(occupied));
            m.bytes_high = std::max(m.bytes_high, uint64_t(occupied_bytes));
            cv.notify_all();
        }
    }
    pace(m.end);
    {
        std::unique_lock lock(mutex);
        if (o.rate > 0 && ordinal < planned)
        {
            uint64_t remaining = planned - ordinal;
            m.offered += remaining;
            m.rejected += remaining;
            m.aborted_rejected += remaining;
            for (size_t i = 0; i < connections.size(); ++i)
            {
                auto &cursor = connections[i]->cursor;
                cursor = advance_aborted_cursor(
                    cursor, i, ordinal, remaining, connections.size(), corpus.size());
            }
        }
        while (!failed && occupied && now() < data_deadline)
        {
            cv.wait_for(lock, std::chrono::milliseconds(1));
        }
        if (occupied && !failed)
        {
            failed = true;
            m.timedout = occupied;
            m.error = "drain_timeout";
            for (auto &c : connections)
            {
                shutdown(c->socket.value, SD_BOTH);
            }
        }
        stopping = true;
        cv.notify_all();
    }
    for (auto &thread : threads)
    {
        thread.join();
    }
    m.finish = now();
    if (!failed)
    {
        try
        {
            for (auto &c : connections)
            {
                control(*c, o, 3);
            }
        }
        catch (const std::exception &e)
        {
            m.error = e.what();
        }
    }
    return metrics;
}

int client(const Options &o)
{
    auto corpus = load_corpus(o.corpus);
    uint64_t unique_bytes = 0;
    for (const auto &f : corpus)
    {
        require(f.length <= o.inflight_bytes, "frame_exceeds_inflight_budget");
        unique_bytes += f.length;
    }
    std::vector<std::unique_ptr<ClientConnection>> connections;
    connections.reserve(size_t(o.connections));
    int effective_send = 0, effective_receive = 0;
    for (int i = 0; i < o.connections; ++i)
    {
        auto c = std::make_unique<ClientConnection>();
        c->socket.value =
            WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_OVERLAPPED);
        require(c->socket.value != INVALID_SOCKET, "client_socket");
        socket_options(c->socket.value, o);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(uint16_t(o.port));
        require(connect(c->socket.value, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0,
                "connect");
        socket_buffers(c->socket.value, effective_send, effective_receive);
        c->cursor = size_t(i) % corpus.size();
        connections.push_back(std::move(c));
    }
    if (o.warmup > 0)
    {
        auto warm = phase(o, corpus, connections, o.warmup);
        require(warm->error.empty(), "warmup_failed: " + warm->error);
    }
    auto result = phase(o, corpus, connections, o.duration);
    auto &m = *result;
    uint64_t unresolved = m.admitted - m.acknowledged - m.bad_results;
    bool valid = m.error.empty() && unresolved == 0 && m.bad_results == 0 &&
                 m.offered == m.admitted + m.rejected;
    bool generator_adequate =
        m.error.empty() &&
        (o.rate == 0 || (m.scheduler_late == 0 && m.late_rejected == 0 && m.aborted_rejected == 0));
    double cohort = std::max(o.duration, seconds(m.finish - m.start));
    bool sustainable = valid && generator_adequate && m.rejected == 0 && m.timedout == 0 &&
                       (o.rate == 0 || double(m.window_completed) >= double(m.offered) * .99);
    auto totalmiss = [&](uint64_t n) {
        return n + m.rejected + unresolved + m.bad_results;
    };
    std::ostringstream s;
    s << std::setprecision(12)
      << "{\"kind\":\"client\",\"language\":\"cpp\",\"mode\":" << escaped(o.mode)
      << ",\"success\":" << (valid ? "true" : "false")
      << ",\"valid\":" << (valid ? "true" : "false")
      << ",\"sustainable\":" << (sustainable ? "true" : "false") << "," << build_info() << ","
      << resources() << ",\"duration_seconds\":" << o.duration
      << ",\"window_seconds\":" << o.duration << ",\"cohort_seconds\":" << cohort
      << ",\"drain_seconds\":" << std::max(0.0, seconds(m.finish - m.end))
      << ",\"configured_drain_seconds\":" << o.drain << ",\"drain_limit_seconds\":" << o.drain
      << ",\"cohort_scope\":\"T0 through max(window end, data-worker finish), excluding End "
         "control\",\"offered\":"
      << m.offered << ",\"admitted\":" << m.admitted << ",\"first_send_started\":" << m.first_send
      << ",\"acknowledged\":" << m.acknowledged << ",\"completed_frames\":" << m.acknowledged
      << ",\"rejected\":" << m.rejected << ",\"generator_late_rejected\":" << m.late_rejected
      << ",\"aborted_schedule_rejected\":" << m.aborted_rejected
      << ",\"scheduler_late_count\":" << m.scheduler_late
      << ",\"scheduler_late_over_1ms\":" << m.scheduler_late
      << ",\"generator_adequate\":" << (generator_adequate ? "true" : "false")
      << ",\"pacing\":\"process-local high-resolution waitable timer plus <=200us "
         "spin\",\"failed\":"
      << m.bad_results << ",\"timedout\":" << m.timedout << ",\"unresolved\":" << unresolved
      << ",\"completed_records\":" << m.records << ",\"completed_payload_bytes\":" << m.bytes
      << ",\"window_completed_frames\":" << m.window_completed
      << ",\"window_records\":" << m.window_records
      << ",\"window_payload_bytes\":" << m.window_bytes
      << ",\"window_frames_per_second\":" << m.window_completed / o.duration
      << ",\"window_records_per_second\":" << m.window_records / o.duration
      << ",\"window_payload_bytes_per_second\":" << m.window_bytes / o.duration
      << ",\"cohort_frames_per_second\":" << m.acknowledged / cohort
      << ",\"queue_high_water\":" << m.queue_high
      << ",\"inflight_bytes_high_water\":" << m.bytes_high
      << ",\"deadline_misses_1ms\":" << totalmiss(m.missed1)
      << ",\"deadline_misses_5ms\":" << totalmiss(m.missed5)
      << ",\"deadline_misses_10ms\":" << totalmiss(m.missed10) << ",\"rate\":" << o.rate
      << ",\"arrival\":" << escaped(o.arrival)
      << ",\"load_model\":" << escaped(o.rate == 0 ? "closed-loop" : "scheduled")
      << ",\"connections\":" << o.connections << ",\"window\":" << o.window
      << ",\"inflight_bytes_limit\":" << o.inflight_bytes
      << ",\"unique_payload_bytes\":" << unique_bytes << ",\"corpus_frames\":" << corpus.size()
      << ",\"data_request_protocol_bytes\":" << m.bytes + m.acknowledged * 16
      << ",\"data_ack_protocol_bytes\":" << m.acknowledged * 32
      << ",\"native_client_threads\":" << o.connections * 2 + 1
      << ",\"cursor_rule\":\"connection-local start=index; advance=connections per offered "
         "request; persists across phases\",\"seed\":"
      << o.seed
      << ",\"io_counter_scope\":\"process-lifetime including controls and "
         "warmup\",\"send_operations\":"
      << blocking_sends << ",\"receive_operations\":" << blocking_receives
      << ",\"sent_protocol_bytes\":" << blocking_sent_bytes
      << ",\"received_protocol_bytes\":" << blocking_received_bytes
      << ",\"socket_buffer_requested\":" << o.socket_buffer
      << ",\"socket_send_buffer_effective\":" << effective_send
      << ",\"socket_receive_buffer_effective\":" << effective_receive
      << ",\"scheduled_latency\":" << m.scheduled.json()
      << ",\"submitted_latency\":" << m.submitted.json() << ",\"client_delay\":" << m.delay.json()
      << ",\"scheduler_lateness\":" << m.lateness.json()
      << ",\"latency_ns\":{\"p50\":" << m.scheduled.quantile(.5)
      << ",\"p90\":" << m.scheduled.quantile(.9) << ",\"p99\":" << m.scheduled.quantile(.99)
      << ",\"p999\":" << m.scheduled.quantile(.999) << ",\"max\":" << m.scheduled.maximum
      << "},\"size_latency\":[";
    for (size_t i = 0; i < m.sizes.size(); ++i)
    {
        if (i)
        {
            s << ',';
        }
        s << m.sizes[i].json();
    }
    s << "],\"error\":" << escaped(m.error) << '}';
    emit(o, s.str());
    return valid ? 0 : 1;
}

int process(const Options &supplied)
{
    auto corpus = load_corpus(supplied.corpus);
    Options o = supplied;
    o.max_frame = 1;
    uint64_t unique_bytes = 0;
    for (const auto &f : corpus)
    {
        o.max_frame = std::max(o.max_frame, f.length);
        unique_bytes += f.length;
    }
    Processor processor(o);

    struct Totals
    {
        uint64_t batches = 0, records = 0, bytes = 0;
    };

    auto phase = [&](double duration, Totals &total) {
        int64_t start = now(), finish = start + ticks(duration);
        uint64_t seq = 0;
        Epoch epoch;
        do
        {
            for (const auto &f : corpus)
            {
                auto want = expected(o, f);
                if (want.records > 1000000000ull - epoch.records)
                {
                    epoch = {};
                    seq = 0;
                }
                auto r =
                    processor.apply(f.payload.data(), f.length, f.payload.size(), epoch, ++seq);
                require(r == want, "process_oracle_mismatch");
                require(total.batches < UINT64_MAX && r.records <= UINT64_MAX - total.records &&
                            f.length <= UINT64_MAX - total.bytes,
                        "process_counter_overflow");
                ++total.batches;
                total.records += r.records;
                total.bytes += f.length;
            }
        } while (now() < finish);
        processor.verify();
        return seconds(now() - start);
    };
    if (o.warmup > 0)
    {
        Totals warm;
        phase(o.warmup, warm);
    }
    Totals measured;
    double elapsed = phase(o.duration, measured);
    std::ostringstream s;
    s << std::setprecision(12)
      << "{\"kind\":\"process\",\"language\":\"cpp\",\"mode\":" << escaped(o.mode)
      << ",\"control\":\"full-corpus-processing-only\",\"success\":true,\"valid\":true,"
      << build_info() << "," << resources() << ",\"duration_seconds\":" << elapsed
      << ",\"window_seconds\":" << elapsed << ",\"completed_frames\":" << measured.batches
      << ",\"completed_records\":" << measured.records
      << ",\"completed_payload_bytes\":" << measured.bytes
      << ",\"frames_per_second\":" << measured.batches / elapsed
      << ",\"records_per_second\":" << measured.records / elapsed
      << ",\"payload_bytes_per_second\":" << measured.bytes / elapsed
      << ",\"unique_payload_bytes\":" << unique_bytes
      << ",\"retained_canonical_bytes\":" << processor.retained
      << ",\"owned_storage_capacity\":" << processor.owned_capacity() << "}";
    emit(o, s.str());
    return 0;
}
} // namespace bench
