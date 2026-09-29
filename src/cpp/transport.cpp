#include "bench.hpp"

namespace bench
{
std::atomic<uint64_t> blocking_sends = 0, blocking_receives = 0, blocking_sent_bytes = 0,
                      blocking_received_bytes = 0;

std::array<uint8_t, 16> header(uint16_t type, uint64_t seq, size_t length)
{
    std::array<uint8_t, 16> b{};
    write_be(b.data(), length, 4);
    write_be(b.data() + 4, 1, 2);
    write_be(b.data() + 6, type, 2);
    write_be(b.data() + 8, seq, 8);
    return b;
}

void check_response(const uint8_t *p, uint16_t type, uint64_t seq, size_t length)
{
    require(read_be(p, 4) == length && read_be(p + 4, 2) == 1 &&
                read_be(p + 6, 2) == uint16_t(type | 0x8000) && read_be(p + 8, 8) == seq,
            "response_header");
}

void socket_options(SOCKET s, const Options &o)
{
    int one = 1;
    require(setsockopt(
                s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char *>(&one), sizeof(one)) ==
                0,
            "TCP_NODELAY");
    require(setsockopt(s,
                       SOL_SOCKET,
                       SO_SNDBUF,
                       reinterpret_cast<const char *>(&o.socket_buffer),
                       sizeof(int)) == 0,
            "SO_SNDBUF");
    require(setsockopt(s,
                       SOL_SOCKET,
                       SO_RCVBUF,
                       reinterpret_cast<const char *>(&o.socket_buffer),
                       sizeof(int)) == 0,
            "SO_RCVBUF");
}

void socket_buffers(SOCKET socket, int &send, int &receive)
{
    int length = sizeof(int);
    require(getsockopt(socket, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<char *>(&send), &length) ==
                0,
            "effective_send_buffer");
    length = sizeof(int);
    require(
        getsockopt(socket, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<char *>(&receive), &length) == 0,
        "effective_receive_buffer");
}

static void remaining_timeout(SOCKET s, int option, int64_t deadline)
{
    auto remaining = deadline - now();
    require(remaining > 0, "io_deadline");
    DWORD ms = DWORD(std::max(1.0, std::ceil(seconds(remaining) * 1000)));
    require(setsockopt(s, SOL_SOCKET, option, reinterpret_cast<const char *>(&ms), sizeof(ms)) == 0,
            "control_timeout_option");
}

void send_exact(SOCKET s, const uint8_t *data, size_t n, int cap, int64_t deadline)
{
    while (n)
    {
        remaining_timeout(s, SO_SNDTIMEO, deadline);
        size_t request = std::min(n, size_t(cap ? cap : INT_MAX));
        ++blocking_sends;
        int got = send(s, reinterpret_cast<const char *>(data), int(request), 0);
        if (got == SOCKET_ERROR)
        {
            fail(WSAGetLastError() == WSAETIMEDOUT ? "send_timeout" : "send_failed");
        }
        require(got > 0, "send_failed");
        blocking_sent_bytes += got;
        data += got;
        n -= size_t(got);
    }
}

void receive_exact(SOCKET s, uint8_t *data, size_t n, int cap, int64_t deadline)
{
    while (n)
    {
        remaining_timeout(s, SO_RCVTIMEO, deadline);
        size_t request = std::min(n, size_t(cap ? cap : INT_MAX));
        ++blocking_receives;
        int got = recv(s, reinterpret_cast<char *>(data), int(request), 0);
        if (got == SOCKET_ERROR)
        {
            fail(WSAGetLastError() == WSAETIMEDOUT ? "receive_timeout" : "receive_failed");
        }
        require(got > 0, "response_eof");
        blocking_received_bytes += got;
        data += got;
        n -= size_t(got);
    }
}

static std::atomic<bool> interrupted = false;

static BOOL WINAPI ctrl(DWORD event)
{
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT || event == CTRL_CLOSE_EVENT)
    {
        interrupted = true;
        return TRUE;
    }
    return FALSE;
}

struct StageMetrics
{
    Histogram receive, process, decode, visit;
    uint64_t processing_total = 0;

    void clear()
    {
        receive.clear();
        process.clear();
        decode.clear();
        visit.clear();
        processing_total = 0;
    }

    void merge(const StageMetrics &s)
    {
        receive.merge(s.receive);
        process.merge(s.process);
        decode.merge(s.decode);
        visit.merge(s.visit);
        processing_total += s.processing_total;
    }
};

struct ServerStats
{
    std::atomic<uint64_t> accepted = 0, excess = 0, recv_ops = 0, send_ops = 0, recv_bytes = 0,
                          send_bytes = 0, batches = 0, bytes = 0, records = 0;
    std::atomic<bool> stopping = false;
    uint64_t graceful = 0, peak_connections = 0, peak_retained = 0, peak_owned = 0;
    int effective_send = 0, effective_receive = 0;
    std::map<std::string, uint64_t> rejections;
    std::unique_ptr<StageMetrics> stages = std::make_unique<StageMetrics>();
};

struct Connection
{
    enum class Phase
    {
        Header,
        Body,
        Send
    } phase = Phase::Header;
    const Options &o;
    ServerStats &stats;
    SOCKET socket;
    OVERLAPPED ov{};
    // All frame/processor state is owned under this mutex. Pending I/O retains the registry entry.
    std::mutex mutex;
    bool pending = false, closed = false, epoch_active = false;
    std::string reason;
    size_t position = 0, length = 0, response_length = 0;
    uint16_t type = 0;
    uint64_t sequence = 0, last_sequence = 0;
    int64_t started = 0, progress = 0, receive_issued = now();
    std::array<uint8_t, 16> head{};
    Bytes body;
    std::array<uint8_t, 16 + summary_size> response{};
    Processor processor;
    Epoch epoch;
    std::unique_ptr<StageMetrics> stages = std::make_unique<StageMetrics>();

    Connection(SOCKET s, const Options &opts, ServerStats &st)
        : o(opts), stats(st), socket(s),
          body(std::max(size_t(32), o.max_frame) + simdjson::SIMDJSON_PADDING), processor(o)
    {
    }

    ~Connection()
    {
        if (socket != INVALID_SOCKET)
        {
            closesocket(socket);
        }
    }

    void close(std::string why)
    {
        if (closed)
        {
            return;
        }
        closed = true;
        reason = std::move(why);
        if (socket != INVALID_SOCKET)
        {
            CancelIoEx(reinterpret_cast<HANDLE>(socket), nullptr);
            closesocket(socket);
            socket = INVALID_SOCKET;
        }
        // pending remains true until GQCS observes this OVERLAPPED's completion.
    }

    ProcessingBudget processing_budget() const
    {
        ProcessingBudget budget{&stats.stopping};
        if (started)
        {
            budget.deadline = started + ticks(o.frame_ms / 1000.0);
            int64_t idle_deadline = progress + ticks(o.idle_ms / 1000.0);
            if (idle_deadline < budget.deadline)
            {
                budget.deadline = idle_deadline;
                budget.timeout = "idle_timeout";
            }
        }
        // started is zero between frames, including graceful EOF: only shutdown applies there.
        return budget;
    }

    bool expired(int64_t stamp = now())
    {
        if (!closed)
        {
            auto why = processing_budget().expired(stamp);
            if (!why.empty())
            {
                close(std::string(why));
            }
        }
        return closed;
    }

    void issue()
    {
        if (expired())
        {
            return;
        }
        require(!pending && !closed, "io_ownership");
        ov = {};
        WSABUF buffer{};
        size_t remaining;
        if (phase == Phase::Send)
        {
            buffer.buf = reinterpret_cast<char *>(response.data() + position);
            remaining = response_length - position;
        }
        else if (phase == Phase::Header)
        {
            buffer.buf = reinterpret_cast<char *>(head.data() + position);
            remaining = 16 - position;
        }
        else
        {
            buffer.buf = reinterpret_cast<char *>(body.data() + position);
            remaining = length - position;
        }
        buffer.len = ULONG(std::min(remaining, size_t(o.io_cap ? o.io_cap : INT_MAX)));
        require(buffer.len > 0, "zero_io");
        DWORD transferred = 0, flags = 0;
        pending = true;
        int result = phase == Phase::Send
                         ? WSASend(socket, &buffer, 1, &transferred, 0, &ov, nullptr)
                         : WSARecv(socket, &buffer, 1, &transferred, &flags, &ov, nullptr);
        if (phase == Phase::Send)
        {
            ++stats.send_ops;
        }
        else
        {
            ++stats.recv_ops;
        }
        if (result == SOCKET_ERROR && WSAGetLastError() != WSA_IO_PENDING)
        {
            pending = false;
            close("io_submit");
        }
        // Both synchronous success and pending success dispatch only through IOCP.
    }

    void respond(std::span<const uint8_t> b)
    {
        if (expired())
        {
            return;
        }
        auto h = header(uint16_t(type | 0x8000), sequence, b.size());
        std::copy(h.begin(), h.end(), response.begin());
        std::copy(b.begin(), b.end(), response.begin() + 16);
        response_length = 16 + b.size();
        phase = Phase::Send;
        position = 0;
        progress = now();
        issue();
    }

    void diagnostic_pause(const ProcessingBudget &budget) const
    {
        int64_t finish = now() + ticks(o.pause_ms / 1000.0);
        for (int64_t remaining = finish - now(); remaining > 0; remaining = finish - now())
        {
            budget.check();
            // A diagnostic gate must release its worker and connection slot promptly on stop.
            Sleep(DWORD(std::clamp(std::ceil(seconds(remaining) * 1000), 1.0, 10.0)));
        }
        budget.check();
    }

    void process_frame()
    {
        if (expired())
        {
            return;
        }
        const auto budget = processing_budget();
        if (type == 1)
        {
            require(!epoch_active, "nested_begin");
            require(std::equal(o.manifest.begin(), o.manifest.end(), body.begin()),
                    "manifest_mismatch");
            epoch = {};
            stages->clear();
            epoch_active = true;
            respond({});
        }
        else if (type == 3)
        {
            require(epoch_active, "end_outside_epoch");
            processor.verify(budget);
            epoch_active = false;
            auto b = epoch.encode();
            respond(b);
        }
        else
        {
            require(epoch_active, "data_outside_epoch");
            bool sample = ((epoch.batches + 1) % 1024) == 0;
            int64_t begin = sample ? now() : 0;
            if (o.pause_every && ((epoch.batches + 1) % uint64_t(o.pause_every) == 0))
            {
                diagnostic_pause(budget);
            }
            if (expired())
            {
                return;
            }
            Result r =
                processor.apply(body.data(), length, body.size(), epoch, sequence, sample, budget);
            if (sample)
            {
                auto elapsed = nanoseconds(now() - begin);
                stages->processing_total += elapsed;
                stages->receive.add(nanoseconds(progress - receive_issued));
                stages->process.add(elapsed);
                if (o.mode != "transport")
                {
                    stages->decode.add(processor.last_decode_ns);
                }
                if (o.mode.starts_with("retain"))
                {
                    stages->visit.add(processor.last_visit_ns);
                }
            }
            ++stats.batches;
            stats.bytes += length;
            stats.records += r.records;
            std::array<uint8_t, 16> b{};
            write_be(b.data(), r.records, 8);
            write_be(b.data() + 8, r.digest, 8);
            respond(b);
        }
    }

    void completed(bool ok, DWORD n)
    {
        require(pending, "completion_without_pending");
        pending = false;
        if (closed)
        {
            return;
        }
        int64_t stamp = now();
        if (expired(stamp))
        {
            return;
        }
        if (!ok)
        {
            close("io_completion");
            return;
        }
        if (n == 0)
        {
            if (phase == Phase::Header && position == 0)
            {
                processor.verify(processing_budget());
                close("graceful");
            }
            else
            {
                close(phase == Phase::Send ? "send_zero" : "truncated_frame");
            }
            return;
        }
        if (phase == Phase::Send)
        {
            stats.send_bytes += n;
        }
        else
        {
            stats.recv_bytes += n;
        }
        if (!started)
        {
            started = stamp;
        }
        progress = stamp;
        position += n;
        if (phase == Phase::Send)
        {
            require(position <= response_length, "send_overrun");
            if (position < response_length)
            {
                issue();
                return;
            }
            phase = Phase::Header;
            position = 0;
            started = 0;
            progress = 0;
            receive_issued = stamp;
            issue();
            return;
        }
        if (phase == Phase::Header)
        {
            require(position <= 16, "header_overrun");
            if (position < 16)
            {
                issue();
                return;
            }
            length = size_t(read_be(head.data(), 4));
            type = uint16_t(read_be(head.data() + 6, 2));
            sequence = read_be(head.data() + 8, 8);
            require(read_be(head.data() + 4, 2) == 1, "version");
            require(type >= 1 && type <= 3, "type");
            require(last_sequence != UINT64_MAX && sequence == last_sequence + 1, "sequence");
            require(length <= hard_limit, "hard_frame_limit");
            require(type == 1   ? length == 32
                    : type == 3 ? length == 0
                                : length > 0 && length <= o.max_frame,
                    "frame_length");
            require(type == 1 ? !epoch_active : epoch_active, "epoch_state");
            last_sequence = sequence;
            position = 0;
            if (length)
            {
                phase = Phase::Body;
                issue();
            }
            else
            {
                process_frame();
            }
            return;
        }
        require(position <= length, "body_overrun");
        if (position < length)
        {
            issue();
        }
        else
        {
            process_frame();
        }
    }
};

static bool stop_requested(HANDLE input, std::string &control)
{
    DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr))
    {
        require(GetLastError() == ERROR_BROKEN_PIPE, "stdin_peek");
        return true;
    }
    if (!available)
    {
        return false;
    }
    char data[128];
    DWORD got = 0;
    if (!ReadFile(input, data, std::min(available, DWORD(sizeof(data))), &got, nullptr) || got == 0)
    {
        return true;
    }
    control.append(data, got);
    require(control.size() <= 1024, "stdin_command_length");
    return control.find("stop\n") != std::string::npos ||
           control.find("stop\r\n") != std::string::npos;
}

int server(const Options &o)
{
    interrupted = false;
    SetConsoleCtrlHandler(ctrl, TRUE);
    ServerStats stats;
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (o.control_stdin)
    {
        require(GetFileType(input) == FILE_TYPE_PIPE, "control_stdin_requires_pipe");
    }
    HANDLE port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, nullptr, 0, DWORD(o.workers));
    require(port != nullptr, "iocp_create");

    struct PortGuard
    {
        HANDLE p;

        ~PortGuard()
        {
            CloseHandle(p);
        }
    } guard{port};

    Socket listener(WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_OVERLAPPED));
    require(listener.value != INVALID_SOCKET, "listen_socket");
    int exclusive = 1;
    require(setsockopt(listener.value,
                       SOL_SOCKET,
                       SO_EXCLUSIVEADDRUSE,
                       reinterpret_cast<char *>(&exclusive),
                       sizeof(exclusive)) == 0,
            "exclusive_address");
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(uint16_t(o.port));
    require(bind(listener.value, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0, "bind");
    require(listen(listener.value, SOMAXCONN) == 0, "listen");
    u_long nonblocking = 1;
    require(ioctlsocket(listener.value, FIONBIO, &nonblocking) == 0, "listener_nonblocking");
    std::mutex registry_mutex;
    std::map<Connection *, std::shared_ptr<Connection>> registry;
    std::vector<std::thread> workers;
    auto complete_io = [&] {
        for (;;)
        {
            DWORD n = 0;
            ULONG_PTR key = 0;
            OVERLAPPED *ov = nullptr;
            BOOL ok = GetQueuedCompletionStatus(port, &n, &key, &ov, INFINITE);
            if (!ov)
            {
                break;
            }
            std::shared_ptr<Connection> c;
            {
                // Copy ownership before releasing the registry lock. Closing a socket alone
                // never frees its OVERLAPPED or buffers while a completion can still arrive.
                std::lock_guard lock(registry_mutex);
                auto found = registry.find(reinterpret_cast<Connection *>(key));
                if (found != registry.end())
                {
                    c = found->second;
                }
            }
            if (!c)
            {
                std::terminate();
            }
            std::lock_guard lock(c->mutex);
            try
            {
                require(ov == &c->ov, "completion_owner");
                c->completed(ok != 0, n);
            }
            catch (const simdjson::simdjson_error &)
            {
                c->close("json_syntax");
            }
            catch (const std::exception &e)
            {
                c->close(e.what());
            }
        }
    };
    for (int i = 0; i < o.workers; ++i)
    {
        workers.emplace_back(complete_io);
    }
    std::cout << "{\"event\":\"ready\",\"port\":" << o.port << ",\"mode\":" << escaped(o.mode)
              << "," << build_info() << "}" << std::endl;
    int64_t start = now();
    bool stopping = false;
    std::string fatal, control;
    std::vector<std::shared_ptr<Connection>> snapshot;
    snapshot.reserve(size_t(o.max_connections));
    try
    {
        for (;;)
        {
            if (o.control_stdin && !stopping)
            {
                stopping = stop_requested(input, control);
            }
            if (interrupted || (o.run_seconds > 0 && seconds(now() - start) >= o.run_seconds))
            {
                stopping = true;
            }
            if (stopping)
            {
                stats.stopping = true;
            }
            snapshot.clear();
            {
                std::lock_guard registry_lock(registry_mutex);
                for (const auto &[key, c] : registry)
                {
                    snapshot.push_back(c);
                }
            }
            for (auto &keep : snapshot)
            {
                auto &c = *keep;
                std::unique_lock lock(c.mutex, std::try_to_lock);
                if (!lock.owns_lock())
                {
                    continue;
                }
                stats.peak_retained = std::max(stats.peak_retained, c.processor.peak_retained);
                stats.peak_owned = std::max(stats.peak_owned, uint64_t(c.processor.peak_owned));
                c.expired();
                if (c.closed && !c.pending)
                {
                    stats.stages->merge(*c.stages);
                    if (c.reason == "graceful")
                    {
                        ++stats.graceful;
                    }
                    else
                    {
                        ++stats.rejections[c.reason];
                    }
                    std::lock_guard registry_lock(registry_mutex);
                    registry.erase(&c);
                }
            }
            {
                std::lock_guard registry_lock(registry_mutex);
                if (stopping && registry.empty())
                {
                    break;
                }
            }
            if (stopping)
            {
                Sleep(1);
                continue;
            }
            WSAPOLLFD poll{listener.value, POLLRDNORM, 0};
            int ready = WSAPoll(&poll, 1, 10);
            require(ready != SOCKET_ERROR, "accept_poll");
            if (ready <= 0)
            {
                continue;
            }
            SOCKET accepted = accept(listener.value, nullptr, nullptr);
            if (accepted == INVALID_SOCKET)
            {
                require(WSAGetLastError() == WSAEWOULDBLOCK, "accept");
                continue;
            }
            Socket accepted_guard(accepted);
            std::lock_guard registry_lock(registry_mutex);
            if (registry.size() >= size_t(o.max_connections))
            {
                ++stats.excess;
                continue;
            }
            socket_options(accepted, o);
            u_long blocking = 0;
            require(ioctlsocket(accepted, FIONBIO, &blocking) == 0, "accepted_blocking");
            socket_buffers(accepted, stats.effective_send, stats.effective_receive);
            auto c = std::make_shared<Connection>(accepted, o, stats);
            accepted_guard.value = INVALID_SOCKET;
            require(CreateIoCompletionPort(reinterpret_cast<HANDLE>(accepted),
                                           port,
                                           reinterpret_cast<ULONG_PTR>(c.get()),
                                           0) == port,
                    "iocp_associate");
            registry.emplace(c.get(), c);
            ++stats.accepted;
            stats.peak_connections = std::max(stats.peak_connections, uint64_t(registry.size()));
            std::lock_guard lock(c->mutex);
            c->issue();
        }
    }
    catch (const std::exception &e)
    {
        fatal = e.what();
        stats.stopping = true;
        for (;;)
        {
            snapshot.clear();
            {
                std::lock_guard registry_lock(registry_mutex);
                if (registry.empty())
                {
                    break;
                }
                for (const auto &[key, c] : registry)
                {
                    snapshot.push_back(c);
                }
            }
            for (auto &keep : snapshot)
            {
                auto &c = *keep;
                std::unique_lock lock(c.mutex, std::try_to_lock);
                if (!lock.owns_lock())
                {
                    continue;
                }
                c.close("server_failure");
                stats.peak_retained = std::max(stats.peak_retained, c.processor.peak_retained);
                stats.peak_owned = std::max(stats.peak_owned, uint64_t(c.processor.peak_owned));
                if (!c.pending)
                {
                    std::lock_guard registry_lock(registry_mutex);
                    registry.erase(&c);
                }
            }
            Sleep(1);
        }
    }
    for (size_t i = 0; i < workers.size(); ++i)
    {
        PostQueuedCompletionStatus(port, 0, 0, nullptr);
    }
    for (auto &t : workers)
    {
        t.join();
    }
    SetConsoleCtrlHandler(ctrl, FALSE);
    std::ostringstream s;
    s << "{\"kind\":\"server\",\"language\":\"cpp\",\"mode\":" << escaped(o.mode)
      << ",\"valid\":" << (fatal.empty() ? "true" : "false")
      << ",\"duration_seconds\":" << seconds(now() - start) << "," << build_info() << ","
      << resources() << ",\"accepted_connections\":" << stats.accepted
      << ",\"excess_connections\":" << stats.excess
      << ",\"graceful_connections\":" << stats.graceful
      << ",\"peak_connections\":" << stats.peak_connections
      << ",\"completed_frames\":" << stats.batches << ",\"completed_payload_bytes\":" << stats.bytes
      << ",\"completed_records\":" << stats.records << ",\"receive_operations\":" << stats.recv_ops
      << ",\"send_operations\":" << stats.send_ops
      << ",\"received_protocol_bytes\":" << stats.recv_bytes
      << ",\"sent_protocol_bytes\":" << stats.send_bytes << ",\"workers\":" << o.workers
      << ",\"max_frame\":" << o.max_frame << ",\"input_capacity_per_connection\":"
      << std::max(size_t(32), o.max_frame) + simdjson::SIMDJSON_PADDING
      << ",\"peak_retained_canonical_per_connection\":" << stats.peak_retained
      << ",\"peak_owned_storage_per_connection\":" << stats.peak_owned
      << ",\"owned_storage_scope\":\"row/text vector capacity including retained and incoming "
         "scratch; "
         "excludes parser, allocator metadata and transient reallocation overlap\""
      << ",\"socket_buffer_requested\":" << o.socket_buffer
      << ",\"socket_send_buffer_effective\":" << stats.effective_send
      << ",\"socket_receive_buffer_effective\":" << stats.effective_receive
      << ",\"processing_samples\":" << stats.stages->process.count
      << ",\"processing_sample_ns_total\":" << stats.stages->processing_total
      << ",\"diagnostic_faults\":" << ((o.pause_every || o.io_cap) ? "true" : "false")
      << ",\"rejection_reasons\":{";
    bool first = true;
    for (const auto &[why, n] : stats.rejections)
    {
        if (!first)
        {
            s << ',';
        }
        first = false;
        s << escaped(why) << ':' << n;
    }
    s << "},\"stage_sample_every\":1024,\"stage_scope\":\"last epoch per connection, reset at "
         "Begin and merged at connection cleanup; receive includes waiting from header issue; "
         "aggregate decode includes categorization; retained visit includes eviction revisit; "
         "processing includes injected pause\",\"receive_elapsed_ns\":"
      << stats.stages->receive.json(true)
      << ",\"processing_elapsed_ns\":" << stats.stages->process.json(true)
      << ",\"decode_materialize_ns\":" << stats.stages->decode.json(true)
      << ",\"retained_visit_ns\":" << stats.stages->visit.json(true)
      << ",\"fatal\":" << escaped(fatal) << '}';
    emit(o, s.str());
    return fatal.empty() ? 0 : 1;
}
} // namespace bench
