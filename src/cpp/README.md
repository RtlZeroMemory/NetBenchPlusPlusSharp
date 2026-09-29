# C++23 implementation

Windows x64 client, IOCP server, and processing-only control for the shared TCP ingestion benchmark. Start with the [project guide](../../README.md) for cross-language runs and benchmark results; the [implementation contract](../../docs/contract.md) defines the CLI, strict JSON schema, wire format, and measurement rules.

## Build and check

Use PowerShell from the repository root. Requirements: Visual Studio 2026 C++ tools and Windows SDK, CMake with the `Visual Studio 18 2026` generator, and Python 3.11+ for the checks. Python uses only its standard library.

```powershell
cmake -S src/cpp -B src/cpp/build -G "Visual Studio 18 2026" -A x64
cmake --build src/cpp/build --config Release -j 4
./src/cpp/build/Release/tcpbench.exe selftest

python tests/protocol.py --export-fixtures
python src/cpp/check.py
python src/cpp/regressions.py
python tests/protocol.py --server src/cpp/build/Release/tcpbench.exe
python tests/measurement.py --client src/cpp/build/Release/tcpbench.exe
```

CMake requires C++23; compile-time checks require that standard and 64-bit pointers. The supported target is Windows x64. Schema, range, and integrity checks remain enabled in Release. Benchmark results report the compiler, C++ standard value, Windows SDK, build configuration, selected parser implementation, and QPC frequency. CTest also exposes the executable selftest as `native_selftest`.

The native check covers all four modes, partial I/O, arrival shapes, and graceful shutdown. Regressions target reviewed deadline and stage-timing defects; shared tests cover protocol conformance and client accounting against an independent peer. These are correctness checks, not performance evidence.

## Run locally

Create a small functional corpus:

```powershell
python tools/corpus.py generate --output data/smoke.bin --bytes 1048576 --frame-bytes 4096 --seed 42
python tools/corpus.py verify data/smoke.bin
```

Start the server in one terminal:

```powershell
./src/cpp/build/Release/tcpbench.exe server --port 9000 --mode aggregate
```

Wait for its `ready` JSON line, then run the client in another terminal from the repository root:

```powershell
New-Item -ItemType Directory -Force results | Out-Null
./src/cpp/build/Release/tcpbench.exe client --port 9000 --corpus data/smoke.bin --mode aggregate --duration 10 --warmup 2 --output results/native-client.json
```

Stop the server with Ctrl+C after the client finishes. Both sides use the all-zero manifest hash by default for manual runs; the [campaign runner](../../bench/run.py) supplies a scenario hash and collects final server output through graceful shutdown.

Use matching modes on both ends: `aggregate`, `retain-reuse`, `retain-allocate`, or the `transport` control that skips JSON processing. `--rate 0` is closed loop; a positive rate schedules total frames per second across all connections. `--window` and `--inflight-bytes` are global admission limits.

To run the same processor without TCP:

```powershell
./src/cpp/build/Release/tcpbench.exe process --corpus data/smoke.bin --mode aggregate --duration 5 --warmup 1
```

Processing-only phases finish a full corpus pass, so elapsed time can exceed the requested duration. Use the reported elapsed time. Neither this control nor the small manual corpus establishes server capacity.

## Modules and ownership

| File | Responsibility |
| --- | --- |
| [bench.hpp](bench.hpp) | Shared options/results, processor declaration, bounded histograms, socket owner, cross-file declarations |
| [main.cpp](main.cpp) | CLI validation, dispatch, build/resource metadata, histogram serialization |
| [processing.cpp](processing.cpp) | Strict JSON traversal, canonical digest/categories, retained storage, corpus validation, selftest |
| [transport.cpp](transport.cpp) | Socket/framing helpers, per-connection IOCP state machine, server lifecycle |
| [load.cpp](load.cpp) | Client scheduling, send/ACK workers, admission/drain accounting, processing-only control |

The server keeps **at most one pending IOCP operation per connection**. Registry and worker references retain the connection and buffers until canceled completions have been observed. The deadline scanner skips busy processing locks; frame/no-progress deadlines also cover response backpressure. Quiet connections between frames remain open. Deadline handling is cooperative and subject to Windows scheduling, not a hard real-time guarantee.

The client has one sender and one concurrent ACK-reader thread per connection, plus the scheduler. A preallocated global request pool holds separate send and ACK links; a mutex protects the pool and queues, and ACK completion releases each slot. Keep one client fixed when comparing servers, then check important results with the alternate client.

Parsing uses vendored **simdjson v3.12.3**. [CMakeLists.txt](CMakeLists.txt) verifies both amalgamation files against pinned SHA-256 hashes before compiling; [the bundled license](vendor/LICENSE.simdjson) applies to that dependency. On-Demand views stay within parsing. Retained modes copy typed rows and decoded UTF-8 text into owned storage before committing a valid batch.

Retention limits count canonical bytes (`38 + decoded message length` per row) and batches **per connection**. Actual row/text vector capacity includes layout, growth slack, recycled capacity, and validation scratch. Its high-water mark includes incoming scratch while older batches are still retained, including scratch growth during a failed parse. Reuse can keep high-water capacity in `retain-batches + 1` slots; allocate releases evicted vectors. These capacities exclude parser/input/response buffers, histograms, allocator overhead, and temporary overlap inside vector reallocation; they are not total process memory.

Processing observes the earlier frame/idle deadline and server shutdown before work, every 1024 records, and before committing a batch. All planned eviction candidates are verified before retained state changes. Diagnostic pauses check at most every 10 ms; quiet EOF verification observes shutdown without inheriting an old frame deadline. These checks are cooperative and cannot preempt a single parser call, allocation or OS scheduling delay.

## Read the metrics

| Values | Scope |
| --- | --- |
| Client `window_*` | Completions inside the fixed measured window |
| Client cohort counters/rates | Measured requests through the later of window end or data-worker finish, including failed drain waits; excludes End controls |
| CPU and memory peaks | Process lifetime, including setup and warmup; current memory fields are snapshots at reporting |
| Send/receive operation counters | Socket calls across the process lifetime, including controls and warmup; not a count of every syscall |
| Server completed frames/bytes/records | Server lifetime, including warmup |
| Server stage histograms | Last epoch per connection, reset at Begin and merged at connection cleanup |

Data I/O has the absolute window-end-plus-drain deadline; Begin/End exchanges have separate bounded deadlines. Early fatal errors retain all intended scheduled demand as `aborted_schedule_rejected`; closed loop has no synthetic future demand. Check `valid`, `generator_adequate`, reconciliation, and rejection counts before comparing rates. Native allocation/free counters are unavailable in ordinary runs; use a separate heap/ETW diagnostic for those claims.

Scheduled pacing uses a process-local high-resolution waitable timer and at most 200 microseconds of final spin. Any scheduled lateness over 1 ms makes `generator_adequate=false`. Poisson and burst schedules are prepared before timing and have a ten-million-arrival storage cap; steady arrivals are calculated by ordinal/rate. Network epochs permit at most one billion records per connection.

Histograms use the shared log-linear nanosecond buckets and report bucket upper bounds. Values at least 60 seconds contribute to count, overflow, and maximum, but have no ordinary bucket. Empty histograms and quantiles whose rank falls in overflow return `null`. Size classes are ordered: ≤4 KiB, ≤64 KiB, ≤256 KiB, ≤1 MiB, larger.

Stage samples are frames 1024, 2048, and so on in each epoch. Receive elapsed includes waiting from header-receive issue; aggregate decode includes categorization, retained visit includes eviction verification, and processing includes diagnostic pauses. Stage p99 requires 10,000 samples and p99.9 requires 1,000,000; raw buckets remain available below those gates.

## Diagnostics

| Option | Use |
| --- | --- |
| `--io-cap N` on server/client; default `0` | Cap each send/receive request to exercise partial I/O deterministically; diagnostic results only |
| `--control-stdin 1` on server; default `0` | Require piped stdin; `stop` or EOF initiates cancellation/drain and final metrics, without a blocking stdin thread |
| `--pause-every N --pause-ms M` on server; defaults `0` | Inject a processing pause every Nth data frame; diagnostic results only |
| `--arrival burst --rate R` on client | Repeat five seconds at `0.6R`, then one second at `1.5R`; R is the reference rate, not the six-second average |

AddressSanitizer builds are for correctness diagnostics only:

```powershell
cmake -S src/cpp -B src/cpp/build-asan -G "Visual Studio 18 2026" -A x64 -DBENCH_ASAN=ON
cmake --build src/cpp/build-asan --config RelWithDebInfo -j 4
python tests/protocol.py --export-fixtures
python src/cpp/check.py --exe src/cpp/build-asan/RelWithDebInfo/tcpbench.exe
python src/cpp/regressions.py --exe src/cpp/build-asan/RelWithDebInfo/tcpbench.exe
python tests/protocol.py --server src/cpp/build-asan/RelWithDebInfo/tcpbench.exe
python tests/measurement.py --client src/cpp/build-asan/RelWithDebInfo/tcpbench.exe
```

CMake copies the installed MSVC ASAN runtime beside the executable when available. Windows ThreadSanitizer support is not claimed. See the [review log](../../reviews/review-log.md) for checks actually completed and residual limits.

## Format maintained code

Use `clang-format` from PATH with the repository [.clang-format](../../.clang-format). Keep the explicit file list to exclude vendored and generated code:

```powershell
clang-format -i src/cpp/bench.hpp src/cpp/main.cpp src/cpp/processing.cpp src/cpp/transport.cpp src/cpp/load.cpp
clang-format --dry-run --Werror src/cpp/bench.hpp src/cpp/main.cpp src/cpp/processing.cpp src/cpp/transport.cpp src/cpp/load.cpp
```
