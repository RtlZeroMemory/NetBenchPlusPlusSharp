# C# / .NET implementation

Server, client, processing-only control and selftest for the shared benchmark, using asynchronous `Socket` APIs and `Utf8JsonReader`, with no NuGet dependencies. The [repository README](../../README.md) covers campaigns and results; the [contract](../../docs/contract.md) defines the protocol, validation, load model and result schema.

## Build and check

From the repository root in PowerShell:

```powershell
dotnet build src/dotnet/Bench.csproj -c Release
dotnet src/dotnet/bin/Release/net11.0/Bench.dll selftest --corpus tests/fixtures/golden.bin
./tests/run_all.ps1
dotnet format whitespace src/dotnet/Bench.csproj --verify-no-changes
```

The SDK (`11.0.100-preview.7.26381.103`, [global.json](../../global.json)) and runtime (`11.0.0-preview.7.26381.103`, [Bench.csproj](Bench.csproj)) are pinned with roll-forward disabled. The project targets x64 Release with Server GC and concurrent GC, with DATAS (the dynamic heap adaptation that .NET 9+ enables by default) turned off for throughput; the campaign also runs the allocation-heavy cells with the runtime default, invariant globalization, and warnings as errors. Tiered compilation and dynamic PGO are runtime defaults; campaign warmups (15–20 s) cover tier-up.

## Source map

| File | Responsibility |
| --- | --- |
| [Program.cs](Program.cs) | Entry point, options (same names, ranges and rules as the native parser), header writer, socket configuration |
| [Processing.cs](Processing.cs) | Strict parsing with `Utf8JsonReader`, canonical digest, owned batches, retention and eviction, epoch summaries, budget |
| [FastJson.cs](FastJson.cs) | The alternative schema-specific parser (`--parser fast`): same rules, SIMD string scanning, integer key matching, SWAR integers |
| [Utf8Check.cs](Utf8Check.cs) | AVX2 UTF-8 validation (the Keiser-Lemire lookup algorithm used by simdjson) for the fast parser |
| [Server.cs](Server.cs) | Asynchronous server: one loop per connection, per-frame `Task.Yield`, 10 ms deadline scanner, measured interval |
| [Client.cs](Client.cs) | Load generator: per-connection sender and ACK reader tasks, closed-loop credits, raised-priority generator thread, pacing |
| [Corpus.cs](Corpus.cs) | Corpus loading and the processing-only control |
| [Report.cs](Report.cs) | Histogram, JSON output, resource snapshots, build metadata |
| [SelfTest.cs](SelfTest.cs) | In-process checks, case for case with the native selftest, run once per parser |
| [SelfTestFuzz.cs](SelfTestFuzz.cs) | Differential fuzzing: fast parser vs `Utf8JsonReader`, `Utf8Check` vs `Utf8.IsValid`, inputs placed against a no-access guard page |

## Design notes

- **Parsing.** `Utf8JsonReader.Read()` validates JSON structure and escape syntax but not string UTF-8 or surrogate pairing. An unescaped name or `kind` that equals a valid value is plain ASCII and is matched in place; anything else is decoded with `CopyString`, which unescapes and rejects lone surrogates and invalid UTF-8. Unescaped messages are checked in place with `Utf8.IsValid` and hashed or retained directly; escaped messages use `CopyString` into a scratch larger than the 4,096-byte limit (it rejects an exact fit), then the decoded length is checked. Together these measured +13–17% on EASY content and no change on HARD content. Two hand-written unescapers measured 7–13% slower than `CopyString` and were discarded ([diagnostics](../../results/diagnostics-csharp-parser-20260929)). Integers are validated and parsed from the raw token in one pass, as in C++. The FNV digest uses a plain loop: a force-inlined unrolled form measured about 20% slower overall.
- **Retention.** Rows and text are stored in chunks, identically to C++: the first chunk grows from 16 rows / 256 bytes by doubling into uninitialized arrays (`GC.AllocateUninitializedArray`, like `std::vector::reserve`), later chunks hold 1,024 rows (48 KiB) or 64 KiB of text. No array reaches the large-object heap, whose collections are gen2-only. Reuse keeps evicted batches on a free list (at most `retain-batches + 1` batches); allocate creates fresh batches and drops evicted ones for the GC.
- **Server.** Receives and sends complete synchronously when data is ready (the runtime's inline completion). After each response the connection awaits `Task.Yield()` so connections take turns on the thread pool. Deadlines are checked at every completion, by a 10 ms scanner that disposes expired sockets, and every 1,024 records during processing. `--workers` is recorded only; the CPU mask (and `DOTNET_PROCESSOR_COUNT`, set by the runner) is the budget.
- **Client.** Closed loop uses a per-connection `SemaphoreSlim` of `window / connections` credits; scheduled load uses a dedicated highest-priority thread with a high-resolution waitable timer and at most 1 ms of spin, admitting against global `Interlocked` caps. Each request is one gathered `SendAsync` (header and payload). Acknowledgements are read in batches, never past what is owed.
- **Counters.** `allocated_bytes` is `GC.GetTotalAllocatedBytes(precise: true)` over the interval; `gc` gives collection counts and `GC.GetTotalPauseDuration()` over the same interval. Pause totals are runtime observations, not a latency attribution.
- **Fast parser.** `--parser fast` swaps `Utf8JsonReader` for [FastJson](FastJson.cs), written for this one schema. It validates UTF-8 once per frame, scans strings 64 bytes at a time for `"`, `\` and control bytes, matches the seven keys with one or two integer compares, parses integers eight digits at a time, and decodes escapes with speculative 32-byte copies. It accepts and rejects exactly what the `Utf8JsonReader` path does. The selftest fuzzes one against the other (60,000 documents, both retention paths) with every input ending at a no-access page, and planted bugs, including a one-byte over-read, are caught. `BENCH_PARSER=fast` makes it the default so the shared test suites can run it; the campaign runner always passes `--parser` explicitly. Results are in the [parser challenge](../../docs/benchmarks/parser-challenge.md).
