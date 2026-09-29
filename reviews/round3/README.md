# Round 3: rework audit (29 September 2026)

An end-to-end audit of the first pass: methodology, both implementations, the load generator, the runner and the report. Baseline sources and binaries were snapshotted before any change in [`results/audit-baseline-20260929/`](../../results/audit-baseline-20260929/) (hashes match the first pass: C# `cb2e213e…`, C++ `7e195792…`). The first-pass results and their original classification are unchanged.

Independent reviewers (read-only subagents) checked correctness, fairness and unnecessary complexity in three waves after the rewrite, and once more before the campaign. No reviewer build or stress probe ran during a measurement.

## Defects found and fixed

| Area | Defect | Evidence | Fix |
| --- | --- | --- | --- |
| Native client | CPU-saturated (≈2.9 of 3 cores; `notify_all` herd). Its queueing (client delay p99 17–24 ms) dominated the first-pass C++ p99. | `results/diagnostics-baseline-20260929` | Lock-free slot ring, one wait word per lane, batched ACK reads, gathered sends |
| Native client | Non-overlapped sockets serialize a blocking send behind a blocking receive on the same socket; `shutdown` does not unblock `recv` | deadlock reproduced in diagnostics | `WSA_FLAG_OVERLAPPED`; `CancelIoEx` on failure |
| Both servers | Inline completion loops let one connection with buffered frames starve others (closed-loop tails were starvation) | `results/diagnostics-fairness-20260929` | Per-frame requeue (C++ IOCP re-post, C# `Task.Yield`) |
| Transport | 1 MiB anomaly (70–84 MiB/s, ~1 s latency) was TCP flow control with 256 KiB buffers and 64 pipelined 1 MiB frames, not GC | transport + 2 ms spin controls in `results/diagnostics-rework-20260929` | 4 MiB socket buffers declared for the campaign |
| Both clients | Arrival schedules used the measured duration during warmup and were built after T0 | code review | Per-phase schedules built before T0 |
| Both clients | An arrival scheduled µs before T1 and dispatched with normal jitter counted as undispatched demand, which excluded the whole pair | final review (no past trial affected) | Undispatched only if unsent at T1 + 1 ms; declared before the campaign |
| C# parser | Messages with escapes that decode to exactly 4,096 bytes (the limit) were rejected: `CopyString` rejects an exact fit when unescaping. Every HARD C# trial of a first campaign attempt failed. | `results/rework-core-20260929/NOTE.md`; mutation test | Scratch larger than the limit plus an explicit decoded-length check; boundary tests in both selftests and the protocol suite |
| Native server | Start packet posted before `pending` was set (possible crash if the post failed or raced) | review | `pending = true` before posting |
| C# client | Manifest copied into the End request as well as Begin | testing | Only Begin carries it |
| Build | MSVC `/GL`/`/LTCG` made simdjson processing ~30% slower | processing-only A/B on a pinned core | Removed |
| Report | Pair eligibility, rejected demand omitted from percentiles, crash with mixed kinds; unpaired trials eligible; pairs with one missed p99 dropped from ratios | harness fixtures | Offered-demand percentiles (misses = +∞), grouping by kind, strict pairing, misses kept as ∞ ratios |
| Report | Transport controls would all be excluded by the client-CPU gate although declared client-bound | final review | Exempt, shown as lower bounds in MiB/s; declared before the campaign |
| Runner | `--resume --only` could lose the index and changed the seeded order | final review | Backup before resume; unselected phases still consume the shuffle |
| Schema | C# wrote two memory fields the native side did not; native process control omitted `gc` | schema diff of smoke outputs | Removed / added `gc: null` |
| Tests | `cpu_budget` regression was timing-flaky (the Python sender itself takes 22–47 ms for 16 MiB) | repeated runs | Idle-budget design plus deterministic mid-parse selftests |

## C# optimization after the stopped campaign

The first full campaign run was stopped at trial 158 (`results/rework-campaign-20260929/NOTE.md`) to optimize C#, as requested. The A/B evidence is in [`results/diagnostics-csharp-parser-20260929`](../../results/diagnostics-csharp-parser-20260929/README.md).

| Change | Result | Kept |
| --- | --- | --- |
| Unescaped names, `kind` and messages used in place instead of copied through `CopyString` (same accept/reject decisions) | EASY +13–17% processing-only; HARD unchanged | yes |
| Two hand-written unescapers | 7–13% slower than `CopyString` | no |
| `DOTNET_PreferredVectorBitWidth=512` | no consistent effect | no |
| Chunked batch storage (≤ 64 KiB allocations), in **both** languages | C++ neutral, 38% fewer bytes allocated. C# alone: fewer gen2 GCs but twice the pause time | yes (with the next row) |
| C# DATAS off | alone: worse p99. With chunks: +3–6% throughput, p99 20–21 → 16–17 ms, ~2.5× memory | yes, plus a runtime-default campaign phase |

An independent review of these changes found one bug, fixed before the final campaign. An empty message that follows an exactly full 64 KiB text chunk got an offset naming a chunk that was never created. C# then rejected a valid batch; C++ read out of bounds with zero length. Both readers now treat empty messages as empty spans. The review also led to:

- tests for exact-fill-then-empty in both retain modes, verified by mutation;
- a real multi-chunk reuse test with exact capacity after every step;
- escaped-overflow protocol fixtures;
- `static_assert(sizeof(Row) == 48)`;
- the report checking each C# trial's effective GC setting against its cell.

One accepted change in error reason: an unescaped message over 24,576 bytes that is also invalid UTF-8 is now reported as `json_string` rather than `json_message_length`. That is closer to C++, which reports UTF-8 errors first.

## Methodology changes (declared before any campaign trial)

See [methodology](../../docs/benchmarks/methodology.md): EASY/HARD matrix, controlled comparisons, processing-only and transport controls, generator-adequacy policy v2 (lateness p99 ≤ 100 µs, ≤ 0.1% over 1 ms, no undispatched demand, client CPU ≤ 85%) with the first-pass strict rule still reported, disjoint physical cores, fresh processes, randomized pairs.

## Verification before the campaign

- Release: `tests/run_all.ps1`: both selftests, harness, protocol (both servers), differential against the Python oracle, server regressions, measurement (both clients), interop 64/64, large frames 18/18.
- AddressSanitizer: `tests/run_all.ps1 -Asan`: selftest, protocol, server regression, measurement client, interop 64/64; no sanitizer reports. Windows has no ThreadSanitizer, so no race-freedom claim is made.
- Oracle check of every frame of all five campaign corpora in all three modes with the frozen binaries (`results/audit-checks/campaign-corpora-selftest-final.log`).
- Result-schema diff between implementations (only managed GC sub-fields differ).
- Frozen hashes: `results/audit-checks/frozen-binaries.txt`.
