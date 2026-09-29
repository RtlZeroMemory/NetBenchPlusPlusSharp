# First benchmark report audit

Independent output-only review of [the report](../../docs/benchmarks/first-pass.md), its structured summary and trial CSV, the published gzip, and `results/first-benchmark-20260929/results.json`. No benchmark was rerun and no source, executable, or trial result was changed.

## Verified

- The gzip decompresses byte-for-byte to the campaign `results.json`; the published CSV matches the campaign CSV. All 54 trial rows preserve server/client identities, workload settings, counts, and p50/p99 values. Build hashes and CPU masks are constant across the campaign.
- Every trial has valid client output, no runner error, zero failed/timed-out/unresolved requests, `offered = admitted + rejected`, and `admitted = acknowledged`. Histogram bucket counts plus overflow equal acknowledged requests. Eligibility is 36 accepted rows and 18 excluded scheduled rows; every scheduled row has at least one arrival over 1 ms late, including a minimum of one event. Offered scheduled rates match 50%, 90%, and 120% of the slower primary median frame rate.
- All eight primary summary rows agree exactly with recomputed median throughput, per-trial p50/p99 arrays, and maximum server peak working set. The printed primary table rounds these values correctly. Client memory is not included in the server working-set column.
- Allocation ranges and each separate generation collection range agree with the three primary managed-server resource snapshots. They are cumulative server-lifetime observations, not measured-window allocation rates, additive independent GC categories, or pause-duration evidence.
- All four median paired C#/C++ ratios match pairing by repetition ID. Native throughput improvements from separate medians are 15.453%, 11.878%, and 14.493% for the three 64 KiB modes. The large-frame paired ratios are 1.0081, 1.0095, and 0.8281; their median 1.008 correctly differs from the ratio of server medians.
- The eight alternate-client rows and four transport controls agree with raw data and have the correct client labels. Stage p50 ranges and sample counts agree; stage p99 is null. Each primary 1 MiB trial has two stage samples and 2,146–2,578 acknowledged batches. All three primary managed 1 MiB servers recorded zero collections in every generation.
- Headline totals recompute to 8.957606013 billion records and 1.6599618679 TiB of measured-window payload, correctly rounded to 8.96 billion and 1.66 TiB. Transport contributes payload but zero processed JSON records. Corpus sizes/hashes are consistent by frame target and both unique payload sets exceed 256 MiB.

## Wording corrections applied by the parent

1. Replace “Four server workers/resource cores” with **four server CPU cores; four native IOCP workers and the managed runtime thread pool**. The managed `--workers` setting is not a fixed four-thread executor.
2. Replace “Client choice materially changed some tails” with **alternate-client runs showed different observed tails**. One alternate pair shows sensitivity, but does not isolate causality from run-to-run variation.
3. Fresh managed allocation totals are **62.7–65.5 GiB** when rounded to one decimal; use that instead of “about 62–65 GiB.” The precise MiB table is already correct.

The regenerated report incorporates all three corrections. Its five artifact links resolve locally. Raw campaign JSON SHA-256 is `1e68e387ae3b4d306d04390ca1b0af4da911c7758365bb55b7449967b157f4f6`.

No numerical table correction or data-integrity defect was found. Keep the common-client label, three-run count, saturation-latency definition, percentile-range definition, and server-only memory scope beside any screenshot/excerpt. Keep the 1 MiB diagnostic caveat and all scheduled-load exclusions visible; neither phase establishes a reliable tail-latency or sustainable-capacity winner.
