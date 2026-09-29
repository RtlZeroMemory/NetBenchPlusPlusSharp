# C# / .NET implementation

This executable provides the `server`, `client`, `process`, and `selftest` commands for the shared TCP ingestion benchmark. It uses .NET asynchronous sockets, `Utf8JsonReader`, and owned arrays for retained records. There are no NuGet dependencies.

For the cross-language quickstart and campaigns, see the [repository README](../../README.md). The [shared reference](../../docs/contract.md) defines the CLI, JSON schema, wire protocol, and accounting rules.

## Build and check

Run from the repository root in PowerShell:

```powershell
dotnet build src/dotnet/Bench.csproj -c Release
dotnet src/dotnet/bin/Release/net11.0/Bench.dll selftest --corpus tests/fixtures/golden.bin
python tests/protocol.py --server src/dotnet/bin/Release/net11.0/Bench.dll
python tests/measurement.py --client src/dotnet/bin/Release/net11.0/Bench.dll
python src/dotnet/regression.py
python src/dotnet/server_regression.py
```

The SDK is pinned to `11.0.100-preview.7.26381.103` in [global.json](../../global.json), and the runtime to `11.0.0-preview.7.26381.103` in [Bench.csproj](Bench.csproj). Roll-forward is disabled. The project targets x64, enables server and concurrent GC, and treats warnings as errors. Benchmark Release builds on native Windows; client pacing uses Windows waitable timers.

`selftest` checks parsing, digests, retained storage, framing helpers, histograms, and corpus expectations. The Python suites use independent socket peers. `regression.py` checks failure accounting and measured drain time; `server_regression.py` checks CPU-processing deadlines, shutdown, owned-array peaks and quiet EOF. Regenerate shared fixtures when needed with `python tests/protocol.py --export-fixtures`.

## Commands and diagnostic options

Arguments use `--name value`. Unknown or duplicate options and invalid ranges fail with a nonzero exit code. The server listens on `127.0.0.1` and emits a flushed JSON `ready` event; Ctrl+C requests graceful shutdown. Final JSON is written to stdout and, when supplied, `--output`.

For a processing-only control using the corpus from the quickstart:

```powershell
dotnet src/dotnet/bin/Release/net11.0/Bench.dll process --corpus data/manual.bin --mode retain-reuse --duration 5 --warmup 1 --output results/dotnet-process.json
```

This runs full-corpus cycles through the same processor and checks record counts, digests, and both category arrays after every frame during warmup and measurement. A full cycle finishes before the duration is checked again, so elapsed time can exceed the requested duration. Do not subtract processing-only throughput from socket throughput to infer network cost.

| Option | Applies to | Meaning |
| --- | --- | --- |
| `--control-stdin 1` | Server | A `stop` line or EOF requests cancellation, drains asynchronous operations, verifies retained data, and writes final metrics. Disabled by default. |
| `--io-cap N` | Server/client | Cap each socket operation to N bytes to exercise partial I/O. Unlimited by default; diagnostic use only. |
| `--pause-every N --pause-ms M` | Server | Inject a processing delay every N data frames per connection. Both must be positive or both zero; disabled by default. |
| `--run-seconds N` | Server | Request shutdown after N seconds. Default 0 runs until canceled. |
| `--workers N` | Server | Record the intended comparison budget; this does not cap the .NET thread pool. |

The campaign runner applies affinity before runtime initialization and sets `DOTNET_PROCESSOR_COUNT` to the assigned logical-CPU count. Those settings establish the resource budget. A manual invocation does not acquire that isolation merely by setting `--workers`.

## Ownership and load generation

Each client connection has one send owner and one concurrent acknowledgement owner. `--window` and `--inflight-bytes` bound outstanding frames and payload bytes globally, across all connections. Corpus cursors advance for every offered arrival and continue across the warmup-to-measurement transition.

`--rate 0` selects closed-loop load. A positive rate schedules arrivals independently of replies. Steady arrivals are calculated by ordinal/rate. Poisson and burst schedules are prepared before timing and have a ten-million-arrival storage limit. Burst load repeats five seconds at 0.6 times the reference rate and one second at 1.5 times that rate.

Pacing uses a process-local high-resolution Windows waitable timer and a final spin of at most 200 microseconds. It does not change the machine's timer resolution. A scheduled run is `generator_adequate` only if it is valid, no arrival is more than 1 ms late, and no arrival remains undispatched. Closed-loop runs bypass the scheduled-lateness gate. These checks alone do not prove that the server is the limiting component.

Retained modes own typed row arrays and decoded UTF-8 arrays. Reuse keeps at most one spare batch; allocate creates fresh arrays. The retention-byte limit counts canonical row data. The owned-capacity high-water mark includes growth slack and incoming validation scratch while older batches are still retained, including a failed parse's scratch growth. It excludes object/array headers, abandoned arrays awaiting GC, parser/receive buffers and other runtime memory. Aggregate processing avoids managed strings and record objects in its inner loop; use measured process allocation counters for allocation claims.

Processing observes the earlier frame/idle deadline and server shutdown before work, every 1024 records, and before committing a batch. All planned eviction candidates are verified before retained state changes. Diagnostic pauses observe the same budget; quiet EOF verification observes shutdown without inheriting an old frame deadline. These checks are cooperative: a single parser call, allocation, GC or OS scheduling delay is not preemptible by the budget.

## Measurement fields

`offered = admitted + rejected`, and `admitted = acknowledged + failed + unresolved`. `timedout` is a subset of `unresolved`. Rejected requests have no successful latency sample and count as deadline misses. Undispatched demand is labeled `generator_late_rejected`, or `aborted_schedule_rejected` after an early fatal error; neither disappears from offered demand.

The fixed window starts at T0 and ends at T1. Admitted data has until T1 plus the configured drain allowance to complete. `cohort_seconds` ends at the later of T1 or completion of all data send/ACK workers, including failed waiting. `drain_seconds` is observed time after T1; `drain_limit_seconds` and `configured_drain_seconds` preserve the requested allowance. Final End controls are verified afterward, outside both throughput denominators.

Scheduled latency starts at intended arrival; submitted latency starts at first send attempt. Both measure a whole frame/batch. The dependency-free histogram follows the shared log-linear definition: exact nanoseconds through 1023, then 256 sub-buckets per power of two. Quantiles are bucket upper bounds. Samples at or above 60 seconds have an explicit overflow count; a percentile in overflow is `null`. Size histograms preserve all five classes in order: at most 4 KiB, 64 KiB, 256 KiB, 1 MiB, and larger, up to the 16 MiB hard limit.

Server stages sample every 1024th data frame per connection and reset at BeginEpoch. The final epoch of each connection is merged on close. Aggregate decode includes categorization; retained visits include eviction verification; processing time includes injected pauses. Stage p99 is `null` below 10,000 samples and p99.9 below 1,000,000. Raw buckets, counts, and maxima remain available. These display gates do not change client histogram calculations or justify short-run client tail claims.

| Resource/counter group | Interval covered |
| --- | --- |
| Server resources and socket counters | Server lifecycle, including setup, warmup, drain, and shutdown |
| Client resources | Measured phase plus phase setup, Begin/End controls, and drain |
| Client socket counters | All client exchanges, including warmup and controls |
| Processing-only resources | Timed full-corpus cycles after warmup |

Keep each counter's scope attached to comparisons. GC pause fields are runtime observations, not a complete pause timeline or causal attribution. Use a separate EventPipe diagnostic to investigate GC suspension.

## Source map

| File | Responsibility |
| --- | --- |
| [Program.cs](Program.cs) | Entry point and validated command-line options |
| [Transport.cs](Transport.cs) | Framing, exact socket I/O, connection lifetimes and server |
| [Processing.cs](Processing.cs) | Strict JSON parsing, owned records, digests and retention |
| [Load.cs](Load.cs) | Client connections, bounded admission and send/ACK coordination |
| [Corpus.cs](Corpus.cs) | Corpus loading and processing-only control |
| [Reporting.cs](Reporting.cs) | Histograms, resource snapshots and JSON output |
| [Scheduling.cs](Scheduling.cs) | Arrival schedules and Windows pacing |
| [SelfTest.cs](SelfTest.cs) | Focused in-process correctness checks |

Use `dotnet format whitespace src/dotnet/Bench.csproj --verify-no-changes` to check whitespace against the repository [.editorconfig](../../.editorconfig).

## Review evidence

The [review log](../../reviews/review-log.md) records verified builds, historical findings, regressions, and remaining experimental limits. The [C# documentation review](../../reviews/round2/csharp-docs.md) records this README audit. Old evidence belongs to its recorded binary hashes; source changes require fresh benchmark runs.
