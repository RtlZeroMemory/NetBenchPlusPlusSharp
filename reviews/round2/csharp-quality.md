# Round 2: C# code standards and simplicity

Review only. Read all maintained files in `src/dotnet`, the root and .NET READMEs, `docs/contract.md`, and `reviews/review-log.md`. The existing DLL still hashes to `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`. No source/build changes or benchmarks were made. Locations below refer to this reviewed source.

The implementation has useful concrete types, no dependency/framework excess, and justified preallocation. The main problem is compressed presentation and unrelated responsibilities sharing `Load.cs`; replacing those concrete types with interfaces or strategy layers would make it worse.

## Prioritized changes

1. **P2 — Expose ordering and validation with ordinary blocks.** `Processing.cs:121–143,165–183,214–237`, `Transport.cs:157–190`, and `Load.cs:248,280–310,323–356` combine mutations, checks, and branches on one line. This obscures the atomic batch-commit boundary, epoch transitions, descriptor publication, and failure reconciliation. Use one statement per line, braces for multi-step branches, a multiline field-name selection, and explicit branches for arrival selection. Name the three frame-state cases in the existing switch/branch rather than adding a validator framework. Preserve timestamp placement, ACK publication order, retained verification order, and exception categories. Apply the same readable formatting to setup, output objects, and selftests; do not optimize for physical line count.

2. **P2 — Remove unrelated responsibilities from the client file and shorten phase orchestration.** `Load.cs:13–83,225–368,408–472` contains corpus I/O, a histogram, phase scheduling, post-drain accounting, processing-only mode, and Windows handle/PInvoke management. The `Phase` body alone spans 144 compressed lines including two concurrent workers. Move already-existing coherent types/functions, and extract only deterministic post-worker work into named methods on the existing result/load types: merge connection metrics and reconcile the undispatched schedule. Keep `SendLoop`, `AckLoop`, and their failure/cancellation state adjacent; a new generic executor or one-implementation interface is unnecessary. Explicitly comment which state has one writer and when merged reads become safe.

3. **P2 — Replace the fixed-size histogram dictionaries with arrays.** `Load.cs:136–138,150,197,286,355` always uses exactly the five integer keys `0..4`. Dictionary lookup, conditional insertion during merge, and sorting at export model variability that the contract does not allow. Use five preallocated `Histogram` elements in both existing metric types; merge by index. Keep both public JSON shapes (`size_latency` and `size_class_scheduled_latency`), creating keyed output only during export. This removes dynamic state and hash lookup from successful ACK processing without adding per-frame allocations. Existing boundary checks must still report all five classes, including empty ones.

4. **P3 — Compute exported values once and reuse the required aliases.** `Load.cs:80–82,195–197` computes each quantile twice, exports scheduled latency twice, and exports every size histogram separately for keyed and ordered forms. The aliases are part of current consumers and should remain. Calculate the four quantiles once in `Histogram.ExportCore`, export scheduled/size histograms once before building the client output, and assign the same immutable export objects to both aliases. This is a small clarity/allocation cleanup outside the measured worker interval, not a claimed throughput improvement.

5. **P3 — Narrow locals and member visibility instead of adding encapsulation boilerplate.** `Load.cs:161,177` declares nullable `result`/`resources` outside a `try` although neither is used by `finally`; declare definitely assigned locals at use. `Transport.cs:17–18` exposes `Wire.Socket` and `Wire.Counters`, but searches show all member accesses are inside `Wire`; make them private readonly fields. `Program.cs:7` imports `System.Text` without using it. No meaningful unused counters or retention fields were found; do not delete reported values to make the code appear smaller.

## Minimal modularity outline

Use the existing namespace and concrete types. No new package, factory, interface, inheritance tree, or configuration layer is needed.

| File | Responsibility |
| --- | --- |
| `Program.cs` | Entry point and CLI validation; keep the current small option parser |
| `Reporting.cs` | Existing `Histogram`, `Resources`, `Metadata`, and `JsonOutput`; resource/output work stays outside hot paths |
| `Corpus.cs` | Existing corpus/frame types plus the directly related processing-only control; alternatively retain `Load.ProcessCycle` as a small forwarding-free method if preserving its current test entry point is preferable |
| `Load.cs` | Client connections, preallocated admission, connection/result counters, phase orchestration and send/ACK workers |
| `Scheduling.cs` | Existing finite arrival generation and Windows `Pacer`; preserve SplitMix64 and schedule memory ceilings |
| `Transport.cs` | Existing wire framing and server connection lifecycle |
| `Processing.cs` | Strict JSON parsing, canonical digest, aggregate/retained storage, summary commit |
| `SelfTest.cs`, `regression.py` | Existing runnable checks, expanded only for changed behavior |

These are three additional source files made by moving existing code. Keep parsing helpers beside their caller and retained storage beside its processor; splitting each tiny type into a separate file is unnecessary. An existing static process-control class is acceptable if needed to move the process command cleanly; do not create an instance service for it.

## Documentation that earns its space

- At phase setup: state the generator/send/ACK owners and the `Task.WhenAll` handoff before metrics are read.
- At descriptor publication/return: state the actual send/ACK lifetime requirement and retain it during the bug-review fixes.
- At `Processor.Process`: mark validation/staging versus retained/epoch commit and explain why validation scratch is distinct from committed batches.
- At the drain timestamp: preserve the existing contract explanation that End control exchanges are excluded.
- Name the Windows timer flag/access constants; document the existing high-resolution timer and bounded final spin, without a portability abstraction.
- Add a short module map to the .NET README after the final moves. Avoid XML summaries that merely repeat method names.

## Validation after fixes

Build Release with the pinned SDK and warnings-as-errors; run the existing selftest with `tests/fixtures/golden.bin`, shared protocol cases, faulty-peer measurement cases, and `src/dotnet/regression.py`. Add a small assertion to the existing selftest that exported histogram aliases agree and all five size classes survive merge/export, if the relevant export code changes. Run the existing independent histogram reflection probe because moving the type must retain its namespace/name. Parent integration should rerun cross-language smoke after all language fixes, including a real mixed-size client exchange and fragmented I/O. No sustained benchmark is needed to validate these structural changes.

Retain all externally visible JSON keys, CLI defaults, protocol bytes, integer/digest checks, allocation-free aggregate inner loops, preallocated request pooling, schedule/cursor rules, and timestamp boundaries. Source moves invalidate old line references, not historical review evidence; annotate the new review log rather than rewriting old reports.

`net: no physical-line reduction target; remove the dictionary/duplicate export operations, and intentionally spend lines on readable control flow.`
