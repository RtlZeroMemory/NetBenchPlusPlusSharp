# C# / .NET 11 vs C++23 TCP ingestion benchmark

Compare two interoperable TCP ingestion applications on Windows: C# with .NET 11 and C++23 with simdjson. Both receive the same length-prefixed JSON batches, validate every field, compute the same aggregates, and return verified acknowledgements. Retained workloads also keep owned records and verify them before eviction.

The repository includes both clients and servers, a deterministic corpus generator, an independent Python correctness oracle, and a runner that records the experiment configuration. It measures these application stacks on loopback; it does not isolate language speed or predict network-card throughput.

## Requirements

| Tool | Requirement |
| --- | --- |
| Operating system | Native Windows x64; the runner does not support WSL |
| .NET | SDK `11.0.100-preview.7.26381.103` and runtime `11.0.0-preview.7.26381.103`; roll-forward is disabled |
| C++ | Visual Studio 2026 C++ tools and Windows SDK, with C++23 support |
| CMake | A version supporting the `Visual Studio 18 2026` generator |
| Python | 64-bit Python 3.11 or newer; no Python packages required |

The runner's default budget requires at least six physical cores: four for the server, at least one for the client, and one spare. On smaller machines, pass `--server-cores 1` for a three-core minimum. It selects one logical CPU per physical core, with no SMT siblings shared between client and server. The current affinity implementation supports a single Windows processor group.

The C++ parser, simdjson v3.12.3, is vendored with its license. CMake verifies its hashes before building. Build details are in the [C# README](src/dotnet/README.md) and [C++ README](src/cpp/README.md).

## Quickstart

Run these commands in PowerShell from the repository root:

```powershell
./bench/build.ps1
dotnet src/dotnet/bin/Release/net11.0/Bench.dll selftest
./src/cpp/build/Release/tcpbench.exe selftest
./bench/run.ps1 smoke --output results/smoke-example
python bench/report.py results/smoke-example
```

The smoke suite generates a small corpus and runs every client/server combination in all three JSON workloads, with closed-loop and scheduled traffic. Read `results/smoke-example/report.md` and the individual trials in `trials.csv`. Choose a new output directory when repeating a run: existing result directories are never overwritten. Smoke timings establish correctness, not comparative performance.

For a manual exchange, first create a small corpus:

```powershell
python tools/corpus.py generate --output data/manual.bin --bytes 1048576 --frame-bytes 4096 --seed 42
python tools/corpus.py verify data/manual.bin
```

Start a C++ server in one terminal:

```powershell
./src/cpp/build/Release/tcpbench.exe server --port 9000 --mode retain-reuse --output results/manual-server.json
```

Run the C# client in another terminal:

```powershell
dotnet src/dotnet/bin/Release/net11.0/Bench.dll client --port 9000 --corpus data/manual.bin --mode retain-reuse --connections 4 --window 64 --duration 10 --warmup 2 --output results/manual-client.json
```

After the client finishes, press Ctrl+C in the server terminal to shut it down and write final metrics. Either executable can fill either role. Match the processing mode on both sides. Manual commands use a zero manifest hash by default; the runner supplies a scenario hash.

## Workloads

| Mode | Work per batch |
| --- | --- |
| `aggregate` | Validate and decode every field; update the digest and 64 category/severity buckets |
| `retain-reuse` | Materialize owned typed rows and UTF-8 text; aggregate, retain, verify, and recycle batch storage |
| `retain-allocate` | The same retained semantics, using newly allocated batch storage |
| `transport` | Receive complete framed bodies and verify transport counters without JSON processing; a diagnostic control |

Both retained modes have byte and batch limits. There is no separate managed UTF-16 object-graph workload. Both programs also provide `process --corpus ... --mode ... --duration ... --warmup ... --output ...` to run the same processing code without TCP.

`--rate 0` selects closed-loop saturation. A positive `--rate` is a global scheduled rate in frames per second, independent of replies, with `--arrival steady`, `poisson`, or `burst`. `--window` and `--inflight-bytes` bound outstanding work across all connections. The [CLI and protocol reference](docs/contract.md) defines exact options, framing, validation, and measurement semantics.

## Run a benchmark campaign

```powershell
./bench/run.ps1 first --output results/first-example
python bench/report.py results/first-example
```

| Suite | Purpose | Measured / warmup per trial | Unique payload target per corpus |
| --- | --- | --- | --- |
| `smoke` | Interoperability and basic load checks | 1 s / 0.2 s | 1 MiB |
| `first` | Four anchor workloads, three randomized pairs, plus client, transport, and scheduled-load checks | 30 s / 5 s | 256 MiB |
| `pilot` | Explore 12 workload/frame-size/connection combinations | 30 s / 5 s | 256 MiB |
| `confirm` | Seven randomized pairs at 50%, 90%, and 120% of the slower preliminary capacity | 120 s / 30 s | 2 GiB |
| `soak` | Long representative trials at 70% of the slower preliminary capacity | 1,800 s / 30 s | 2 GiB |

The `first`, `confirm`, and `soak` anchors are aggregate at 64 KiB/16 connections, aggregate at 1 MiB/1 connection, and each retained mode at 64 KiB/16 connections. Confirmation and soak first calibrate each anchor with 30-second closed-loop trials. Larger suites can take hours, including offline corpus generation.

By default, the `first` suite keeps the C++ client fixed for primary comparisons, repeats each anchor with the C# client, checks transport throughput, and tests the three 64 KiB workloads at scheduled rates of 50%, 90%, and 120% of the slower observed capacity. This is an initial campaign, not an overnight confirmation or a memory-plateau study.

Use the Python runner for all options, for example:

```powershell
python bench/run.py --suite pilot --client csharp --server-cores 2
python bench/run.py --suite smoke --duration 0.5 --warmup 0
python bench/run.py --help
```

Corpora are generated before trials, loaded before timing, and identified by SHA-256. Complete records make actual frame sizes and unique payload totals approximate their targets. Result directories preserve environment and scenario manifests, CPU budgets, commands, hashes, logs, client/server JSON, and combined results. Overrides are recorded and change what an experiment can establish.

## Results and interpretation

The [first sustained results and readable tables](docs/benchmarks/first-pass.md) cover 54 trials on a Ryzen 7 9800X3D. All reconciled successfully; 36 closed-loop/control trials passed the comparison gates, while all 18 scheduled trials failed the strict generator-timing gate. Native throughput was about 12–15% higher in the three 64 KiB workloads; tails varied across workloads and clients, and the 1 MiB case did not establish a consistent winner. Raw results, per-trial CSV and environment/source hashes accompany the report.

The [six-reviewer second round](reviews/round2/README.md) records formatting, simplification, deadline/capacity fixes and independent rechecks. The [review log](reviews/review-log.md) preserves earlier evidence.

Read the generated report together with its raw trial rows:

- **Validity comes first.** Incorrect acknowledgements, missing work, timeouts, and invalid final summaries cannot become successful completions. Invalid or generator-inadequate trials stay visible and are excluded from comparisons.
- **Latency is per batch.** Scheduled latency starts at intended arrival; submitted latency starts at first send attempt. Keep fixed-window throughput separate from whole-cohort throughput, which includes draining outstanding data.
- **A shared client can be a limit.** Keep the client fixed when comparing servers and inspect generator lateness, CPU use, and transport checks. Any scheduled arrival over 1 ms late fails the conservative generator-adequacy gate.
- **Counters have different scopes.** Server resource counters include setup, warmup, and shutdown. Do not divide them by measured-only records and call the result measured CPU or allocation cost. Implementation READMEs explain the scopes.
- **Tail claims need samples.** Histogram quantiles are bucket upper bounds. Preserve sample counts, overflow, and individual trials; never average percentiles. Short runs do not justify p99.9 claims.

Loopback shares caches, memory bandwidth, power, and operating-system scheduling. Affinity does not isolate background Windows activity. Parser and runtime differences are part of this application comparison. GC collection counts alone do not explain latency spikes; suspension traces, instrumentation-cost checks, and longer stability tests are separate work.

## Correctness checks and source guide

After building, run the shared independent checks:

```powershell
python tests/harness.py
python tests/protocol.py --server src/dotnet/bin/Release/net11.0/Bench.dll
python tests/protocol.py --server src/cpp/build/Release/tcpbench.exe
python tests/measurement.py --client src/dotnet/bin/Release/net11.0/Bench.dll
python tests/measurement.py --client src/cpp/build/Release/tcpbench.exe
python tests/differential.py --servers src/dotnet/bin/Release/net11.0/Bench.dll src/cpp/build/Release/tcpbench.exe
```

These cover strict JSON/Unicode/integer handling, framing and epochs, retained-data integrity, faulty peers, latency accounting, and comparison with the Python oracle. Focused regressions and native sanitizer commands are in the [C#](src/dotnet/README.md) and [C++](src/cpp/README.md) READMEs. `python tests/large_frames.py` adds 1 MiB/16 MiB frame and alternate-arrival checks. Debug and sanitizer builds are correctness tools, not performance evidence.

| Location | Contents |
| --- | --- |
| [src/dotnet](src/dotnet) / [src/cpp](src/cpp) | The two implementations and focused regressions |
| [bench](bench) | Build scripts, Windows campaign runner, and report generator |
| [tools/corpus.py](tools/corpus.py) / [tests](tests) | Deterministic corpus, independent oracle, and shared checks |
| [docs/contract.md](docs/contract.md) | CLI, wire format, schema, and measurement reference |
| [reviews/review-log.md](reviews/review-log.md) | Findings, fixes, and verification evidence |
| [Design proposal](tcp-dotnet11-cpp23-benchmark-plan.md) | Experimental rationale and proposed further work |

The applications bind only to IPv4 loopback and implement no TLS, compression, database, automatic retries, or alternative payload codec. Frames have a 16 MiB hard limit; network epochs allow at most one billion records per connection. Precomputed Poisson/burst schedules have a ten-million-arrival limit. The design proposal includes experiments that are not completed results.
