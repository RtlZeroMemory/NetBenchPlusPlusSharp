#include "bench.hpp"

namespace bench
{
static_assert(__cplusplus >= 202302L, "C++23 or later is required");
static_assert(sizeof(void *) == 8, "x64 is required");

Options options(int argc, char **argv)
{
    require(argc >= 2, "expected server|client|process|selftest");
    Options o;
    o.command = argv[1];
    require(o.command == "server" || o.command == "client" || o.command == "process" ||
                o.command == "selftest",
            "unknown_command");
    if (o.command == "process")
    {
        o.duration = 5;
        o.warmup = 1;
    }
    require((argc - 2) % 2 == 0, "options_require_values");
    std::map<std::string, std::string> args;
    for (int i = 2; i < argc; i += 2)
    {
        std::string key = argv[i];
        require(key.starts_with("--") && args.emplace(key.substr(2), argv[i + 1]).second,
                "invalid_or_duplicate_option");
    }
    if (o.command == "selftest")
    {
        require(args.empty(), "selftest_options");
    }
    auto str = [&](const char *key, std::string &v) {
        auto it = args.find(key);
        if (it != args.end())
        {
            v = it->second;
            args.erase(it);
        }
    };
    auto num = [&](const char *key, auto &value) {
        auto it = args.find(key);
        if (it == args.end())
        {
            return;
        }
        using T = std::remove_reference_t<decltype(value)>;
        T n{};
        auto [end, ec] =
            std::from_chars(it->second.data(), it->second.data() + it->second.size(), n);
        require(ec == std::errc{} && end == it->second.data() + it->second.size(),
                "invalid_numeric_option");
        value = n;
        args.erase(it);
    };
    str("mode", o.mode);
    str("output", o.output);
    if (o.command == "server" || o.command == "client")
    {
        num("port", o.port);
        num("socket-buffer", o.socket_buffer);
        num("io-cap", o.io_cap);
        std::string hash(64, '0');
        str("manifest-hash", hash);
        require(hash.size() == 64, "manifest_hash");
        for (size_t i = 0; i < 32; ++i)
        {
            unsigned n{};
            auto [end, ec] = std::from_chars(hash.data() + 2 * i, hash.data() + 2 * i + 2, n, 16);
            require(ec == std::errc{} && end == hash.data() + 2 * i + 2, "manifest_hash");
            o.manifest[i] = uint8_t(n);
        }
    }
    if (o.command == "server" || o.command == "process")
    {
        num("retain-batches", o.retain_batches);
        num("retain-bytes", o.retain_bytes);
    }
    if (o.command == "server")
    {
        num("max-frame", o.max_frame);
        num("max-connections", o.max_connections);
        num("workers", o.workers);
        num("idle-timeout-ms", o.idle_ms);
        num("frame-timeout-ms", o.frame_ms);
        num("run-seconds", o.run_seconds);
        num("pause-every", o.pause_every);
        num("pause-ms", o.pause_ms);
        num("control-stdin", o.control_stdin);
    }
    if (o.command == "client" || o.command == "process")
    {
        str("corpus", o.corpus);
        num("duration", o.duration);
        num("warmup", o.warmup);
        require(!o.corpus.empty(), "corpus_required");
    }
    if (o.command == "client")
    {
        num("connections", o.connections);
        num("window", o.window);
        num("inflight-bytes", o.inflight_bytes);
        num("rate", o.rate);
        str("arrival", o.arrival);
        num("seed", o.seed);
        num("drain-seconds", o.drain);
    }
    require(args.empty(), args.empty() ? "" : "unknown_option: " + args.begin()->first);
    require(o.mode == "aggregate" || o.mode == "retain-reuse" || o.mode == "retain-allocate" ||
                o.mode == "transport",
            "mode");
    require(o.port > 0 && o.port <= 65535 && o.max_connections > 0 && o.max_connections <= 1024 &&
                o.connections > 0 && o.connections <= 1024,
            "connection_options");
    require(o.workers > 0 && o.workers <= 256 && o.window > 0 && o.window <= 1048576 &&
                o.max_frame > 0 && o.max_frame <= hard_limit,
            "capacity_options");
    require(o.retain_batches > 0 && o.retain_batches <= 1024 && o.retain_bytes > 0 &&
                o.retain_bytes <= 2147483648ull && o.inflight_bytes > 0 &&
                o.inflight_bytes <= 2147483648ull,
            "retention_options");
    require(o.idle_ms > 0 && o.frame_ms > 0 && o.socket_buffer > 0 && o.socket_buffer <= 16777216 &&
                o.io_cap >= 0,
            "socket_options");
    require(std::isfinite(o.duration) && o.duration > 0 && o.duration <= 86400 &&
                std::isfinite(o.warmup) && o.warmup >= 0 && o.warmup <= 86400 &&
                std::isfinite(o.drain) && o.drain > 0 && o.drain <= 3600,
            "duration_options");
    require(std::isfinite(o.rate) && o.rate >= 0 && o.rate <= 10000000 &&
                std::isfinite(o.run_seconds) && o.run_seconds >= 0 && o.run_seconds <= 604800,
            "rate_options");
    require(o.arrival == "steady" || o.arrival == "poisson" || o.arrival == "burst", "arrival");
    require(o.pause_every >= 0 && o.pause_ms >= 0 && o.pause_ms <= 60000, "pause_options");
    require(o.control_stdin == 0 || o.control_stdin == 1, "control_stdin");
    if (o.command == "server")
    {
        require(uint64_t(o.max_frame) * o.max_connections * 6 <= 1073741824ull,
                "transport_parser_budget");
        if (o.mode.starts_with("retain"))
        {
            require(uint64_t(o.retain_bytes) * o.max_connections <= 2147483648ull,
                    "retention_budget");
        }
    }
    return o;
}

void emit(const Options &o, const std::string &s)
{
    if (!o.output.empty())
    {
        std::ofstream f(o.output, std::ios::binary | std::ios::trunc);
        require(bool(f), "output_open");
        f << s << '\n';
        require(bool(f), "output_write");
    }
    std::cout << s << std::endl;
}

std::string build_info()
{
    std::ostringstream s;
    s << "\"implementation\":\"cpp23\",\"compiler\":\"MSVC " << _MSC_FULL_VER
      << "\",\"cplusplus\":" << __cplusplus << ",\"build_configuration\":" << escaped(BENCH_CONFIG)
      << ",\"windows_sdk\":" << escaped(BENCH_WINDOWS_SDK)
      << ",\"simdjson\":\"3.12.3\",\"simdjson_implementation\":"
      << escaped(simdjson::get_active_implementation()->name())
      << ",\"qpc_frequency\":" << frequency();
#ifdef BENCH_ASAN
    s << ",\"sanitized\":true";
#else
    s << ",\"sanitized\":false";
#endif
    s << ",\"native_allocation_counters\":null,\"native_allocation_note\":\"Use ETW heap "
         "diagnostic for full native allocation/free accounting; ordinary runs report process "
         "memory\"";
    return s.str();
}

std::string resources()
{
    PROCESS_MEMORY_COUNTERS_EX m{};
    m.cb = sizeof(m);
    require(GetProcessMemoryInfo(GetCurrentProcess(),
                                 reinterpret_cast<PROCESS_MEMORY_COUNTERS *>(&m),
                                 sizeof(m)) != 0,
            "process_memory");
    FILETIME create, exit, kernel, user;
    require(GetProcessTimes(GetCurrentProcess(), &create, &exit, &kernel, &user) != 0,
            "process_times");
    auto t = [](FILETIME f) {
        return (uint64_t(f.dwHighDateTime) << 32) | f.dwLowDateTime;
    };
    DWORD_PTR pm = 0, sm = 0;
    GetProcessAffinityMask(GetCurrentProcess(), &pm, &sm);
    std::ostringstream s;
    s << "\"resource_scope\":\"process-lifetime including setup warmup and shutdown; not "
         "measured-cohort CPU\",\"cpu_seconds\":"
      << std::setprecision(12) << double(t(kernel) + t(user)) / 10000000.0
      << ",\"private_bytes\":" << m.PrivateUsage << ",\"peak_commit_bytes\":" << m.PeakPagefileUsage
      << ",\"working_set_bytes\":" << m.WorkingSetSize
      << ",\"peak_working_set_bytes\":" << m.PeakWorkingSetSize << ",\"affinity_mask\":" << pm;
    return s.str();
}

std::string Histogram::json(bool stage) const
{
    std::ostringstream s;
    s << "{\"count\":" << count << ",\"p50_ns\":" << quantile(.5) << ",\"p90_ns\":" << quantile(.9)
      << ",\"p99_ns\":" << (stage && count < 10000 ? "null" : quantile(.99))
      << ",\"p999_ns\":" << (stage && count < 1000000 ? "null" : quantile(.999))
      << ",\"max_ns\":" << maximum << ",\"overflow_60s\":" << overflow << ",\"buckets\":[";
    bool first = true;
    for (size_t i = 0; i < slots; ++i)
    {
        if (buckets[i])
        {
            if (!first)
            {
                s << ',';
            }
            first = false;
            s << '[' << upper(i) << ',' << buckets[i] << ']';
        }
    }
    return s.str() + "]}";
}
} // namespace bench

int main(int argc, char **argv)
{
    try
    {
        auto o = bench::options(argc, argv);
        if (o.command == "selftest")
        {
            bench::selftest();
            return 0;
        }
        if (o.command == "process")
        {
            return bench::process(o);
        }
        WSADATA w{};
        bench::require(WSAStartup(MAKEWORD(2, 2), &w) == 0, "WSAStartup");
        int result = o.command == "server" ? bench::server(o) : bench::client(o);
        WSACleanup();
        return result;
    }
    catch (const std::exception &e)
    {
        std::cerr << "{\"event\":\"error\",\"reason\":" << bench::escaped(e.what()) << "}\n";
        return 1;
    }
}
