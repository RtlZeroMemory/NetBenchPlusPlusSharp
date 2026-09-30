#include "bench.hpp"

#include <algorithm>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <thread>

namespace bench
{
namespace
{
std::atomic<bool> interrupted = false;

BOOL WINAPI on_console(DWORD event)
{
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT || event == CTRL_CLOSE_EVENT)
    {
        interrupted = true;
        return TRUE;
    }
    return FALSE;
}

struct Stages
{
    Histogram receive, processing, decode, visit;

    void clear()
    {
        receive.clear();
        processing.clear();
        decode.clear();
        visit.clear();
    }

    void merge(const Stages &s)
    {
        receive.merge(s.receive);
        processing.merge(s.processing);
        decode.merge(s.decode);
        visit.merge(s.visit);
    }
};

struct Totals
{
    uint64_t frames = 0, records = 0, bytes = 0;
    uint64_t receive_calls = 0, send_calls = 0, received = 0, sent = 0;
};

// Server-wide state. The measured interval runs from the first Begin to the last End across
// all connections, so its resource counters and processed-work denominators share one scope.
struct Server
{
    const Options &o;
    std::atomic<bool> stopping = false;
    bool inline_completions = true; // every connection skipped completion packets on success
    HANDLE port = nullptr;
    std::mutex mutex; // guards everything below
    uint64_t accepted = 0, rejected = 0, accept_errors = 0, graceful = 0, peak_retained = 0,
             peak_owned = 0;
    int send_buffer = 0, receive_buffer = 0;
    std::map<std::string, uint64_t> closed;
    Totals lifetime;
    Stages stages;
    int active_epochs = 0;
    bool interval_complete = true;
    Resources interval_before;
    Totals interval_work;
    std::string measured; // JSON of the last completed interval

    explicit Server(const Options &options) : o(options) {}

    void begin_epoch()
    {
        std::lock_guard lock(mutex);
        if (active_epochs++ == 0)
        {
            interval_before = Resources::sample();
            interval_work = {};
            interval_complete = true;
        }
    }

    void end_epoch(const Epoch *epoch)
    {
        std::lock_guard lock(mutex);
        if (epoch)
        {
            interval_work.frames += epoch->batches;
            interval_work.records += epoch->records;
            interval_work.bytes += epoch->bytes;
        }
        else
        {
            interval_complete = false; // a connection closed inside its epoch
        }
        if (--active_epochs > 0)
        {
            return;
        }
        const Resources after = Resources::sample();
        Json j;
        j.open()
            .boolean("complete", interval_complete)
            .num("seconds", to_seconds(after.at - interval_before.at))
            .num("frames", interval_work.frames)
            .num("records", interval_work.records)
            .num("payload_bytes", interval_work.bytes)
            .num("cpu_seconds", after.cpu_seconds - interval_before.cpu_seconds)
            .num("allocations", after.new_calls - interval_before.new_calls)
            .num("allocated_bytes", after.new_bytes - interval_before.new_bytes)
            .null("gc")
            .num("peak_working_set_bytes", after.peak_working_set)
            .close();
        measured = j.take();
    }
};

struct Connection
{
    enum class Phase
    {
        header,
        body,
        send
    };

    Server &server;
    Socket socket;
    OVERLAPPED io{};     // the single outstanding socket operation
    OVERLAPPED resume{}; // posted to (re)queue the connection's next turn on a worker
    std::mutex mutex;    // owns all fields below; completions and the scanner take it
    bool pending = false, closed = false, epoch_active = false, inline_completions = false;
    std::string reason;
    Phase phase = Phase::header;
    size_t position = 0, length = 0, response_length = 0;
    uint16_t type = 0;
    uint64_t sequence = 0, last_sequence = 0;
    // frame_start is zero between frames: a quiet connection has no deadline.
    int64_t frame_start = 0, progress = 0, receive_start = now(), body_done = 0;
    std::array<uint8_t, header_bytes> head{};
    std::vector<uint8_t> body;
    std::array<uint8_t, header_bytes + summary_bytes> response{};
    Processor processor;
    Epoch epoch;
    Stages stages;
    Totals totals;

    Connection(Server &s, SOCKET sock)
        : server(s), socket(sock),
          body(std::max<size_t>(32, s.o.max_frame) + simdjson::SIMDJSON_PADDING),
          processor(s.o.mode, s.o.max_frame, s.o.retain_batches, s.o.retain_bytes, s.o.fast_parser)
    {
    }

    Budget budget() const
    {
        Budget b{&server.stopping};
        if (frame_start)
        {
            b.deadline = frame_start + to_ticks(server.o.frame_ms / 1000.0);
            int64_t idle = progress + to_ticks(server.o.idle_ms / 1000.0);
            if (idle < b.deadline)
            {
                b.deadline = idle;
                b.reason = "idle_timeout";
            }
        }
        return b;
    }

    // Closing cancels pending I/O; buffers stay owned until its completion is dequeued.
    void close(std::string why)
    {
        if (closed)
        {
            return;
        }
        closed = true;
        reason = std::move(why);
        if (epoch_active)
        {
            epoch_active = false;
            server.end_epoch(nullptr);
        }
        CancelIoEx(reinterpret_cast<HANDLE>(socket.value), nullptr);
        closesocket(std::exchange(socket.value, INVALID_SOCKET));
    }

    bool expire(int64_t t)
    {
        if (!closed)
        {
            Budget b = budget();
            if (b.stop->load(std::memory_order_relaxed))
            {
                close("shutdown");
            }
            else if (b.deadline && t >= b.deadline)
            {
                close(std::string(b.reason));
            }
        }
        return closed;
    }

    // Starts the next operation. Returns the byte count when it completed synchronously
    // (no completion packet is queued), otherwise nothing.
    std::optional<DWORD> issue()
    {
        if (expire(now()))
        {
            return std::nullopt;
        }
        uint8_t *base = phase == Phase::send     ? response.data()
                        : phase == Phase::header ? head.data()
                                                 : body.data();
        size_t end = phase == Phase::send     ? response_length
                     : phase == Phase::header ? header_bytes
                                              : length;
        WSABUF buffer{
            ULONG(std::min(end - position, size_t(server.o.io_cap ? server.o.io_cap : INT_MAX))),
            reinterpret_cast<char *>(base + position)};
        io = {};
        DWORD transferred = 0, flags = 0;
        pending = true;
        int rc = phase == Phase::send
                     ? WSASend(socket.value, &buffer, 1, &transferred, 0, &io, nullptr)
                     : WSARecv(socket.value, &buffer, 1, &transferred, &flags, &io, nullptr);
        ++(phase == Phase::send ? totals.send_calls : totals.receive_calls);
        if (rc == 0 && inline_completions)
        {
            pending = false;
            return transferred;
        }
        if (rc != 0 && WSAGetLastError() != WSA_IO_PENDING)
        {
            pending = false;
            close("io_error");
        }
        return std::nullopt;
    }

    // Handles one completed operation and any that complete synchronously after it.
    void complete(bool ok, DWORD n)
    {
        for (;;)
        {
            if (closed)
            {
                return;
            }
            if (!ok)
            {
                close("io_error");
                return;
            }
            if (!advance(n))
            {
                return;
            }
            std::optional<DWORD> done = issue();
            if (!done)
            {
                return;
            }
            n = *done;
        }
    }

    // Returns true when another operation should be issued.
    bool advance(DWORD n)
    {
        const int64_t t = now();
        if (expire(t))
        {
            return false;
        }
        if (n == 0)
        {
            if (phase == Phase::header && position == 0)
            {
                // Clean EOF between frames: verify retained data; only shutdown applies.
                processor.verify(Budget{&server.stopping});
                close(epoch_active ? "eof_in_epoch" : "graceful");
            }
            else
            {
                close(phase == Phase::send ? "send_zero" : "truncated");
            }
            return false;
        }
        (phase == Phase::send ? totals.sent : totals.received) += n;
        position += n;
        if (phase != Phase::send)
        {
            frame_start = frame_start ? frame_start : t; // first byte of a frame
            progress = t;
        }
        if (phase == Phase::send)
        {
            progress = t;
            if (position < response_length)
            {
                return true;
            }
            phase = Phase::header;
            position = 0;
            frame_start = 0;
            receive_start = t;
            // One frame per turn: requeue behind other connections instead of looping inline
            // while this connection's next frame is already buffered (which starves the rest).
            pending = true;
            require(PostQueuedCompletionStatus(
                        server.port, 0, reinterpret_cast<ULONG_PTR>(this), &resume) != 0,
                    "iocp_post");
            return false;
        }
        if (phase == Phase::header)
        {
            if (position < header_bytes)
            {
                return true;
            }
            parse_header();
            if (length > 0)
            {
                phase = Phase::body;
                position = 0;
                return true;
            }
        }
        else if (position < length)
        {
            return true;
        }
        body_done = t;
        process_frame();
        return !closed;
    }

    void parse_header()
    {
        length = size_t(read_be(head.data(), 4));
        type = uint16_t(read_be(head.data() + 6, 2));
        sequence = read_be(head.data() + 8, 8);
        require(read_be(head.data() + 4, 2) == 1 && type >= 1 && type <= 3 &&
                    last_sequence != UINT64_MAX && sequence == last_sequence + 1 &&
                    length <= hard_limit,
                "frame_header");
        require(type == 1   ? length == 32 && !epoch_active
                : type == 3 ? length == 0 && epoch_active
                            : length > 0 && length <= server.o.max_frame && epoch_active,
                "frame_state_or_length");
        last_sequence = sequence;
    }

    void process_frame()
    {
        const Budget b = budget();
        size_t n = 0;
        if (type == 1)
        {
            require(std::equal(server.o.manifest.begin(), server.o.manifest.end(), body.begin()),
                    "manifest_mismatch");
            epoch = {};
            stages.clear();
            epoch_active = true;
            server.begin_epoch();
        }
        else if (type == 3)
        {
            processor.verify(b);
            auto summary = epoch.encode();
            std::copy(summary.begin(), summary.end(), response.begin() + header_bytes);
            n = summary_bytes;
        }
        else
        {
            const bool sample = (epoch.batches + 1) % stage_sample_every == 0;
            if (server.o.pause_every && (epoch.batches + 1) % uint64_t(server.o.pause_every) == 0)
            {
                spin_pause(server.o.pause_ms, b);
            }
            Result r =
                processor.apply(body.data(), length, body.size(), epoch, sequence, b, sample);
            write_be(response.data() + header_bytes, r.records, 8);
            write_be(response.data() + header_bytes + 8, r.digest, 8);
            n = 16;
            if (sample)
            {
                stages.receive.add(to_ns(body_done - receive_start));
                stages.processing.add(to_ns(now() - body_done));
                if (server.o.mode != Mode::transport)
                {
                    stages.decode.add(to_ns(int64_t(processor.last_decode_ticks)));
                }
                if (retains(server.o.mode))
                {
                    stages.visit.add(to_ns(int64_t(processor.last_visit_ticks)));
                }
            }
        }
        b.check(); // expired work never publishes a success response or counts
        if (type == 2)
        {
            totals.frames++;
            totals.records += read_be(response.data() + header_bytes, 8);
            totals.bytes += length;
        }
        else if (type == 3)
        {
            epoch_active = false;
            server.end_epoch(&epoch);
        }
        auto h = make_header(uint16_t(type | 0x8000), sequence, n);
        std::copy(h.begin(), h.end(), response.begin());
        response_length = header_bytes + n;
        phase = Phase::send;
        position = 0;
    }

    // Called once the connection is closed and no operation is pending.
    void finish()
    {
        std::lock_guard lock(server.mutex);
        server.stages.merge(stages);
        server.peak_retained = std::max(server.peak_retained, processor.peak_retained);
        server.peak_owned = std::max(server.peak_owned, processor.peak_owned);
        ++(reason == "graceful" ? server.graceful : server.closed[reason]);
        server.inline_completions = server.inline_completions && inline_completions;
        Totals &l = server.lifetime;
        l.frames += totals.frames;
        l.records += totals.records;
        l.bytes += totals.bytes;
        l.receive_calls += totals.receive_calls;
        l.send_calls += totals.send_calls;
        l.received += totals.received;
        l.sent += totals.sent;
    }
};

bool stop_requested(HANDLE input, std::string &control)
{
    DWORD available = 0;
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr))
    {
        return true; // EOF or broken pipe
    }
    char data[128];
    DWORD got = 0;
    if (available && ReadFile(input, data, std::min<DWORD>(available, sizeof(data)), &got, nullptr))
    {
        control.append(data, got);
    }
    return control.find("stop") != std::string::npos || control.size() > 1024;
}
} // namespace

int run_server(const Options &o)
{
    interrupted = false;
    SetConsoleCtrlHandler(on_console, TRUE);
    const Resources started = Resources::sample();
    Server server(o);
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    require(!o.control_stdin || GetFileType(input) == FILE_TYPE_PIPE,
            "control_stdin_requires_pipe");
    HANDLE port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, nullptr, 0, DWORD(o.workers));
    require(port != nullptr, "iocp_create");
    server.port = port;
    Socket listener(WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_OVERLAPPED));
    require(listener.value != INVALID_SOCKET, "listen_socket");
    int exclusive = 1;
    setsockopt(listener.value,
               SOL_SOCKET,
               SO_EXCLUSIVEADDRUSE,
               reinterpret_cast<const char *>(&exclusive),
               sizeof(exclusive));
    const sockaddr_in address = loopback(o.port);
    require(bind(listener.value, reinterpret_cast<const sockaddr *>(&address), sizeof(address)) ==
                0,
            "bind");
    require(listen(listener.value, SOMAXCONN) == 0, "listen");
    u_long nonblocking = 1;
    require(ioctlsocket(listener.value, FIONBIO, &nonblocking) == 0, "listener_nonblocking");

    // A worker copies the registry reference before touching a connection, so a completion
    // can never outlive its OVERLAPPED and buffers.
    std::shared_mutex registry_mutex; // workers look up shared; the acceptor writes
    std::map<Connection *, std::shared_ptr<Connection>> registry;
    auto worker = [&] {
        for (;;)
        {
            DWORD n = 0;
            ULONG_PTR key = 0;
            OVERLAPPED *ov = nullptr;
            BOOL ok = GetQueuedCompletionStatus(port, &n, &key, &ov, INFINITE);
            if (!ov)
            {
                return;
            }
            std::shared_ptr<Connection> c;
            {
                std::shared_lock lock(registry_mutex);
                c = registry.at(reinterpret_cast<Connection *>(key));
            }
            std::lock_guard lock(c->mutex);
            try
            {
                if (ov == &c->resume)
                {
                    c->pending = false;
                    if (auto done = c->issue())
                    {
                        c->complete(true, *done);
                    }
                }
                else
                {
                    require(ov == &c->io && c->pending, "completion_owner");
                    c->pending = false;
                    c->complete(ok != 0, n);
                }
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
    std::vector<std::thread> workers;
    for (int i = 0; i < o.workers; ++i)
    {
        workers.emplace_back(worker);
    }
    auto accept_one = [&] {
        WSAPOLLFD poll{listener.value, POLLRDNORM, 0};
        require(WSAPoll(&poll, 1, 10) != SOCKET_ERROR, "accept_poll");
        Socket accepted(accept(listener.value, nullptr, nullptr));
        if (accepted.value == INVALID_SOCKET)
        {
            if (WSAGetLastError() != WSAEWOULDBLOCK)
            {
                std::lock_guard lock(server.mutex);
                ++server.accept_errors; // e.g. a peer reset before the accept completed
            }
            return;
        }
        if (registry.size() >= size_t(o.max_connections))
        {
            std::lock_guard lock(server.mutex);
            ++server.rejected;
            return;
        }
        u_long blocking = 0;
        require(ioctlsocket(accepted.value, FIONBIO, &blocking) == 0, "accepted_blocking");
        configure_socket(accepted.value, o.socket_buffer);
        auto c =
            std::make_shared<Connection>(server, std::exchange(accepted.value, INVALID_SOCKET));
        auto handle = reinterpret_cast<HANDLE>(c->socket.value);
        require(CreateIoCompletionPort(handle, port, reinterpret_cast<ULONG_PTR>(c.get()), 0) ==
                    port,
                "iocp_associate");
        // Synchronous completions are handled inline, as .NET sockets do.
        c->inline_completions =
            SetFileCompletionNotificationModes(handle, FILE_SKIP_COMPLETION_PORT_ON_SUCCESS) != 0;
        {
            std::lock_guard lock(server.mutex);
            ++server.accepted;
            std::tie(server.send_buffer, server.receive_buffer) = socket_buffers(c->socket.value);
        }
        c->pending = true; // the queued start packet owns the connection until dequeued
        {
            std::lock_guard lock(registry_mutex);
            registry.emplace(c.get(), c);
        }
        if (!PostQueuedCompletionStatus(port, 0, reinterpret_cast<ULONG_PTR>(c.get()), &c->resume))
        {
            c->pending = false;
            fail("iocp_post");
        }
    };
    {
        Json ready;
        ready.open().str("event", "ready").num("port", o.port).str("implementation", "cpp").close();
        emit(Options{}, ready.take());
    }
    std::string fatal, control;
    std::vector<std::shared_ptr<Connection>> snapshot;
    for (bool stopping = false;;)
    {
        stopping = stopping || interrupted || (o.control_stdin && stop_requested(input, control));
        server.stopping = stopping;
        // Deadline scanner: skips connections busy in a worker; processing checks its own budget
        // cooperatively. When stopping, it closes every connection and waits for pending I/O.
        snapshot.clear();
        {
            std::shared_lock lock(registry_mutex);
            for (auto &[key, c] : registry)
            {
                snapshot.push_back(c);
            }
        }
        for (auto &c : snapshot)
        {
            std::unique_lock lock(c->mutex, std::try_to_lock);
            if (lock.owns_lock() && c->expire(now()) && !c->pending)
            {
                c->finish();
                std::lock_guard registry_lock(registry_mutex);
                registry.erase(c.get());
            }
        }
        if (stopping)
        {
            if (snapshot.empty())
            {
                break;
            }
            Sleep(1);
            continue;
        }
        try
        {
            accept_one();
        }
        catch (const std::exception &e)
        {
            fatal = e.what(); // stop accepting; the sweep above drains every connection
            stopping = true;
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
    CloseHandle(port);
    SetConsoleCtrlHandler(on_console, FALSE);

    const Resources end = Resources::sample();
    const Totals &l = server.lifetime;
    Json j;
    j.open()
        .str("role", "server")
        .str("implementation", "cpp")
        .str("mode", mode_name(o.mode))
        .boolean("valid", fatal.empty());
    fatal.empty() ? j.null("error") : j.str("error", fatal);
    j.open("config")
        .num("max_frame", uint64_t(o.max_frame))
        .num("max_connections", o.max_connections)
        .num("workers", o.workers)
        .num("retain_batches", uint64_t(o.retain_batches))
        .num("retain_bytes", uint64_t(o.retain_bytes))
        .num("socket_buffer", o.socket_buffer)
        .num("frame_timeout_ms", o.frame_ms)
        .num("idle_timeout_ms", o.idle_ms)
        .num("pause_every", o.pause_every)
        .num("pause_ms", o.pause_ms)
        .num("io_cap", o.io_cap)
        .str("worker_policy", "fixed IOCP worker threads")
        .close()
        .open("connections")
        .num("accepted", server.accepted)
        .num("rejected", server.rejected)
        .num("accept_errors", server.accept_errors)
        .num("graceful", server.graceful)
        .open("closed");
    for (const auto &[why, count] : server.closed)
    {
        j.num(why, count);
    }
    j.close()
        .close()
        .open("lifetime")
        .num("seconds", to_seconds(end.at - started.at))
        .num("frames", l.frames)
        .num("records", l.records)
        .num("payload_bytes", l.bytes)
        .num("cpu_seconds", end.cpu_seconds)
        .num("peak_working_set_bytes", end.peak_working_set)
        .close();
    server.measured.empty() ? j.null("measured") : j.raw("measured", server.measured);
    j.open("retention")
        .num("canonical_peak_per_connection", server.peak_retained)
        .num("owned_capacity_peak_per_connection", server.peak_owned)
        .close()
        .open("io")
        .num("receive_calls", l.receive_calls)
        .num("send_calls", l.send_calls)
        .num("received_bytes", l.received)
        .num("sent_bytes", l.sent)
        .boolean("inline_completions", server.inline_completions)
        .num("socket_send_buffer", server.send_buffer)
        .num("socket_receive_buffer", server.receive_buffer)
        .close()
        .open("stages")
        .num("sample_every", stage_sample_every);
    server.stages.receive.write(j, "receive", true);
    server.stages.processing.write(j, "processing", true);
    server.stages.decode.write(j, "decode", true);
    server.stages.visit.write(j, "retained_visit", true);
    j.close();
    write_build(j, o.fast_parser);
    j.close();
    emit(o, j.take());
    return fatal.empty() ? 0 : 1;
}
} // namespace bench
