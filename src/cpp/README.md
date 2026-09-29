# C++23 implementation

Windows x64 server, client and processing-only control for the shared benchmark. The [repository README](../../README.md) covers campaigns and results; the [contract](../../docs/contract.md) defines the protocol, validation, load model and result schema.

## Build and check

From the repository root in PowerShell (Visual Studio 2026 C++ tools, Windows SDK, CMake with the `Visual Studio 18 2026` generator):

```powershell
cmake -S src/cpp -B src/cpp/build -G "Visual Studio 18 2026" -A x64
cmake --build src/cpp/build --config Release
./src/cpp/build/Release/tcpbench.exe selftest --corpus tests/fixtures/golden.bin
./tests/run_all.ps1
```

CMake verifies the vendored simdjson 3.12.3 files against pinned SHA-256 hashes and requires C++23 on x64. Release is `/O2` without `/GL`/`/LTCG`: whole-program optimization measured about 30% slower JSON processing with simdjson on MSVC 19.51. All validation and integrity checks stay enabled in Release.

AddressSanitizer build for correctness only (never performance):

```powershell
cmake -S src/cpp -B src/cpp/build-asan -G "Visual Studio 18 2026" -A x64 -DBENCH_ASAN=ON
cmake --build src/cpp/build-asan --config RelWithDebInfo
./tests/run_all.ps1 -Asan
```

Windows ThreadSanitizer is not available; no race-freedom claim is made beyond review and the concurrent stress in the suites.

## Source map

| File | Responsibility |
| --- | --- |
| [bench.hpp](bench.hpp) | Shared types: options, JSON writer, histogram, epoch/result, owned rows, budget, processor, corpus, resources |
| [main.cpp](main.cpp) | Option parsing, clocks, JSON writer, histogram, build/resource metadata, socket helpers, global `operator new` counting |
| [processing.cpp](processing.cpp) | Strict parsing with simdjson On-Demand, canonical digest, retention and eviction, corpus loading, processing-only control, selftest |
| [server.cpp](server.cpp) | IOCP server: one operation per connection, inline synchronous completions, per-frame requeue, deadline scanner, measured interval |
| [client.cpp](client.cpp) | Load generator: per-connection sender and ACK reader around a lock-free slot ring, closed-loop and scheduled modes, pacing, accounting |

## Design notes

- **Server.** Each connection has exactly one outstanding overlapped operation, owned through `pending`; a completion is dequeued before its buffers can be released, and the registry keeps the connection alive until then. Sockets use `FILE_SKIP_COMPLETION_PORT_ON_SUCCESS`, so synchronous completions continue inline within a frame (as .NET sockets do). After each response the connection re-posts itself to the IOCP, so connections take turns instead of one worker serving a connection whose next frame is already buffered. A 10 ms scanner closes expired connections; processing checks the same frame/idle budget every 1,024 records.
- **Client.** Each connection has a sender and an ACK reader sharing a slot ring (`tail` produced, `sent`, `head` acknowledged) and a single wait/notify word. Closed loop: the sender refills its own connection when an acknowledgement frees a slot. Scheduled: a raised-priority generator paces arrivals (high-resolution waitable timer plus at most 1 ms of spin), admits them against global frame/byte caps, and never waits. Sends gather header and payload; the reader reads batches of acknowledgements but never past what is owed. Sockets are overlapped handles: a non-overlapped socket would serialize the sender's blocking send behind the reader's blocking receive. On failure or the drain deadline, sockets are shut down and pending I/O cancelled.
- **Processing.** Keys are compared raw unless they contain an escape; integers are validated and parsed from the raw token in one pass (On-Demand does not validate unread scalars); stage 1 validates all UTF-8. Retained rows and text use the same chunked layout as C# (first chunk grows from 16 rows / 256 bytes; later chunks 1,024 rows / 64 KiB; messages never straddle), so large batches never need one large block or a large copy.
- **Allocation counters** count global `operator new` calls and bytes in per-thread cache-line shards; they do not see allocations made outside `operator new` (for example by the OS or `_aligned_malloc`). ASAN builds leave them at zero.

Diagnostic options (`--pause-every/--pause-ms` busy spin, `--io-cap` partial I/O) are documented in the contract and never used in performance cells except the labelled fragmentation stress.
