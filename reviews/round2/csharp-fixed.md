# C# round 2 fixes and verification

Frozen Release DLL SHA-256: `cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd`.

The pinned SDK build succeeds with zero warnings/errors. `dotnet format whitespace src/dotnet/Bench.csproj --no-restore --verify-no-changes` passes. Production source and the binary are frozen for independent review and the parent's campaign.

## Changes

- Added an optional value-type processing budget using the existing cancellation token and earliest absolute frame/idle deadline. Parsing and retained visits check before work, every 1024 rows, and before commit/completion. The diagnostic pause waits only within that budget and observes shutdown. Processing-only controls and existing callers retain an unlimited default budget.
- Planned evictions are verified before any committed retained state changes. The short commit step does not introduce cancellation points midway through mutation. End verifies retention with its active-frame budget; quiet EOF uses only the shutdown token, without inheriting an expired old frame deadline. Shutdown cancellation can interrupt retained verification.
- `Processor.PeakOwnedCapacityBytes` captures simultaneous retained, reusable spare, and incoming scratch row/text capacities before eviction and on failed parsing/verification. Server cleanup publishes this exact observed capacity high-water instead of a post-commit sample. Processing-only output also includes `owned_storage_peak_bytes`. Scope excludes array/object headers, abandoned arrays awaiting GC, parser/receive buffers, and runtime/OS memory.
- Expanded packed statements, branches, declarations and JSON initializers. Replaced fixed five-class dictionaries with preallocated arrays, extracted result merging and undispatched-demand reconciliation, reused exported histogram aliases, and documented concurrent ownership. The sender copies its timestamp before publishing a pooled descriptor so it never rereads that descriptor after the ACK owner can recycle it.
- Moved existing coherent responsibilities into three files; no package/framework/interface layer was added.

## Module map

| File | Responsibility |
| --- | --- |
| `Program.cs` | CLI validation and entry point |
| `Corpus.cs` | Corpus loader/frame metadata and processing-only command |
| `Reporting.cs` | Histograms, resource snapshots, build metadata and JSON output |
| `Scheduling.cs` | SplitMix64, finite arrival schedules and Windows pacing |
| `Load.cs` | Client ownership, admission, phases, send/ACK loops and reconciliation |
| `Transport.cs` | Framing/deadlines and server lifecycle |
| `Processing.cs` | Processing budget, strict JSON, canonical digest, retention and summary commit |
| `SelfTest.cs` | In-process correctness checks |
| `regression.py`, `server_regression.py` | Failed-drain and server budget/capacity socket regressions |

## Evidence on the frozen build

Evidence directory: [csharp-fixed/cb2e213e9089](csharp-fixed/cb2e213e9089/). The aggregate record is [evidence.json](csharp-fixed/cb2e213e9089/evidence.json).

- Selftest: **289** checks with the golden corpus; **1,819** with the smoke corpus. New checks cover rejected budgets without state mutation, retained-visit cancellation, atomic multi-eviction verification failure/recovery, simultaneous/failed scratch peaks, fixed size-class merging, and histogram aliases.
- Shared protocol/oracle: **236** checks across aggregate, retain-reuse and retain-allocate.
- Shared faulty-peer measurement: **10/10** cases, including accounting after early failure, withheld ACK drain timing and an honest ACK inside the absolute drain deadline.
- Existing failed-drain regression: passed.
- Existing independent histogram probe: **100,091** samples passed against the new DLL. The original probe project could not rebuild because old nested generated files produced duplicate assembly attributes; its already-built independent probe DLL ran successfully. The failed tool-build log is preserved separately and the benchmark source/binary was unchanged.
- New server regressions: **11/11** cases. Partial input, 900 ms diagnostic pauses, and ordinary 15.8 MB valid batches close after roughly **103–117 ms** under a 100 ms processing budget, return no success ACK, count zero expired completions and release the sole connection slot. Both retained modes are covered; the ordinary-batch cases verify the complete body was received. An idle budget earlier than the frame budget also wins.
- Shutdown interrupts a 10-second diagnostic pause in **37 ms** in the frozen-build run. Quiet EOF after a prior frame deadline succeeds. Both retention modes report **3,536 bytes** for the same two-batch simultaneous owned-array peak, with a **1,038-byte** canonical peak.

The new persistent check is `python src/dotnet/server_regression.py`; it defaults to a fresh timestamped evidence directory and accepts `--output-dir` only for a new directory. The review wrapper [verify.py](csharp-fixed/verify.py) routes shared suites to fresh review evidence without overwriting earlier findings.

These are bounded correctness/lifecycle checks, not sustained throughput comparisons. Checks are cooperative: one JSON token, an array resize, or the final short atomic commit is not preemptible. No hard real-time bound is claimed. Sustained benchmarking is reserved for the parent after independent verification.

Post-campaign auxiliary-tool follow-up: the parent changed `reviews/csharp-measurement/Probe.csproj` to compile only `Probe.cs`, preventing stale generated output from entering its recursive source glob. `dotnet run --project reviews/csharp-measurement/Probe.csproj -c Release -- src/dotnet/bin/Release/net11.0/Bench.dll` then rebuilt successfully and passed all 100,091 histogram samples. Production source and the frozen benchmark DLL were unchanged; the earlier failed build log remains historical evidence.
