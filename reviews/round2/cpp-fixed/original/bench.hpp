#pragma once
#include <winsock2.h>
#include <windows.h>
#include <psapi.h>
#include <simdjson.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <numeric>
#include <span>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace bench {
constexpr uint64_t offset = 14695981039346656037ull, prime = 1099511628211ull;
constexpr size_t hard_limit = 16777216, summary_size = 1056;
using Bytes = std::vector<uint8_t>;
[[noreturn]] inline void fail(std::string_view reason) { throw std::runtime_error(std::string(reason)); }
inline void require(bool ok, std::string_view reason) { if (!ok) fail(reason); }
inline uint64_t read_be(const uint8_t* p, size_t n) { uint64_t x=0; while(n--) x=(x<<8)|*p++; return x; }
inline void write_be(uint8_t* p, uint64_t x, size_t n) { while(n) { p[--n]=uint8_t(x); x>>=8; } }
inline void hash_byte(uint64_t& h, uint8_t v) { h=(h^v)*prime; }
inline void hash_int(uint64_t& h, uint64_t v, size_t n) { for(size_t i=n; i; --i) hash_byte(h,uint8_t(v>>((i-1)*8))); }
inline int64_t now() { LARGE_INTEGER v; QueryPerformanceCounter(&v); return v.QuadPart; }
inline int64_t frequency() { static auto f=[] { LARGE_INTEGER v; QueryPerformanceFrequency(&v); return v.QuadPart; }(); return f; }
inline double seconds(int64_t ticks) { return double(ticks)/double(frequency()); }
inline uint64_t nanoseconds(int64_t ticks) { return ticks<=0?0:uint64_t((long double)ticks*1000000000.0L/frequency()); }
inline int64_t ticks(double sec) { return int64_t(sec*double(frequency())); }
inline std::string escaped(std::string_view s) { std::string r="\""; for(unsigned char c:s) { if(c=='"'||c=='\\') {r+='\\';r+=char(c);} else if(c<32) {char b[7]; snprintf(b,7,"\\u%04x",c);r+=b;} else r+=char(c); } return r+'"'; }

struct Options {
    std::string command, mode="aggregate", corpus, output, arrival="steady";
    int port=9000, max_connections=32, workers=4, connections=4, window=8;
    size_t max_frame=1048576, retain_batches=8, retain_bytes=67108864, inflight_bytes=67108864;
    int idle_ms=5000, frame_ms=30000, socket_buffer=262144, pause_every=0, pause_ms=0, io_cap=0, control_stdin=0;
    double duration=10, warmup=2, rate=0, drain=30, run_seconds=0;
    uint64_t seed=42;
    std::array<uint8_t,32> manifest{};
};
Options options(int argc, char** argv);
void emit(const Options&, const std::string&);
std::string resources();
std::string build_info();

struct Result {
    uint64_t records=0, digest=offset;
    std::array<uint64_t,64> counts{};
    std::array<int64_t,64> sums{};
    bool operator==(const Result&) const = default;
};
struct Epoch {
    uint64_t batches=0, bytes=0, records=0, digest=offset;
    std::array<uint64_t,64> counts{};
    std::array<int64_t,64> sums{};
    void check(const Result&, size_t) const;
    void add(const Result&, size_t, uint64_t);
    std::array<uint8_t,summary_size> encode() const;
};
struct Row { uint64_t id=0,timestamp=0; uint32_t source=0,flags=0,begin=0,length=0; int64_t value=0; uint8_t kind=0; };
struct Batch { std::vector<Row> rows; Bytes text; uint64_t canonical=0,digest=offset; void clear() {rows.clear();text.clear();canonical=0;digest=offset;} };
class Processor {
    simdjson::ondemand::parser parser;
    std::string mode;
    size_t cap_bytes, cap_batches, scratch=0;
    std::vector<Batch> slots;
    std::vector<size_t> live;
    Result parse(const uint8_t*,size_t,size_t,bool);
    Result visit(const Batch&) const;
public:
    uint64_t retained=0, peak_retained=0;
    uint64_t last_decode_ns=0,last_visit_ns=0;
    Processor(const Options&);
    Result apply(const uint8_t*,size_t,size_t,Epoch&,uint64_t,bool sample=false);
    void verify() const;
    size_t owned_capacity() const;
};
struct Frame { Bytes payload; size_t length=0; Result expected; };
std::vector<Frame> load_corpus(const std::string&);
void selftest();
int server(const Options&);
int client(const Options&);
int process(const Options&);

struct Histogram {
    // Exact ns through 1023; 256 sub-buckets per power of two, with explicit >=60s overflow.
    static constexpr size_t slots=1024+54*256;
    std::array<uint64_t,slots> buckets{};
    uint64_t count=0, maximum=0, overflow=0;
    void clear(){buckets.fill(0);count=maximum=overflow=0;}
    void merge(const Histogram& h){for(size_t i=0;i<slots;++i)buckets[i]+=h.buckets[i];count+=h.count;maximum=std::max(maximum,h.maximum);overflow+=h.overflow;}
    static size_t index(uint64_t v) { if(v<1024)return size_t(v); unsigned e=std::bit_width(v)-1; return 1024+(e-10)*256+size_t((v>>(e-8))-256); }
    static uint64_t upper(size_t i) { if(i<1024)return i; size_t n=i-1024; unsigned shift=unsigned(n/256)+2; uint64_t a=257+n%256; if(shift>=64 || a>(UINT64_MAX>>shift))return UINT64_MAX; return (a<<shift)-1; }
    void add(uint64_t v) { ++count;maximum=std::max(maximum,v);if(v>=60000000000ull)++overflow;else ++buckets[index(v)]; }
    std::string quantile(double q) const { if(!count)return "null"; uint64_t target=uint64_t(std::ceil(q*double(count))), n=0;for(size_t i=0;i<slots;++i)if((n+=buckets[i])>=target)return std::to_string(upper(i));return "null"; }
    std::string json(bool stage=false) const;
};
struct Socket {
    SOCKET value=INVALID_SOCKET;
    Socket()=default; explicit Socket(SOCKET s):value(s){}
    Socket(const Socket&)=delete; Socket& operator=(const Socket&)=delete;
    Socket(Socket&& s) noexcept:value(std::exchange(s.value,INVALID_SOCKET)){}
    ~Socket(){ if(value!=INVALID_SOCKET)closesocket(value); }
};
void socket_options(SOCKET,const Options&);
extern std::atomic<uint64_t> blocking_sends,blocking_receives,blocking_sent_bytes,blocking_received_bytes;
void send_exact(SOCKET,const uint8_t*,size_t,int cap=0,int64_t deadline=0);
void receive_exact(SOCKET,uint8_t*,size_t,int cap=0,int64_t deadline=0);
std::array<uint8_t,16> header(uint16_t,uint64_t,size_t);
void check_response(const uint8_t*,uint16_t,uint64_t,size_t);
}
