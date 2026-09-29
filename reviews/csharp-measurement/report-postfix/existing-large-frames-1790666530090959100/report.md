# Loopback benchmark report

Every raw trial remains available in `results.json` and `trials.csv`.
Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.

Only verified, generator-adequate trials without failed/unresolved requests enter the comparisons below. Unsustainable but correctly reconciled overload trials remain eligible and are counted explicitly.
Eligible trials: 17 / 18. Missing adequacy flags are unverified, not assumed passing.

| Excluded trial | Reasons |
| --- | --- |
| F:\SocketServerClient\results\large-frames-1790666530090959100\0016-aggregate-cpp-csharp | generator_inadequate_or_unverified |

| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Phase | Arrival | Trials | Unsustainable trials | Median completed frames/s | Min–max completed frames/s |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| cpp | csharp | aggregate | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 1166.00 | 1166.00–1166.00 |
| cpp | csharp | aggregate | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 64.00 | 64.00–64.00 |
| cpp | csharp | aggregate | 65536 | 4 | 100 | arrival-correctness | burst | 1 | 0 | 74.52 | 74.52–74.52 |
| cpp | csharp | retain-allocate | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 1008.00 | 1008.00–1008.00 |
| cpp | csharp | retain-allocate | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 58.00 | 58.00–58.00 |
| cpp | csharp | retain-reuse | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 1036.00 | 1036.00–1036.00 |
| cpp | csharp | retain-reuse | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 58.00 | 58.00–58.00 |
| cpp | csharp | transport | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 154.00 | 154.00–154.00 |
| csharp | cpp | aggregate | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 552.00 | 552.00–552.00 |
| csharp | cpp | aggregate | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 32.00 | 32.00–32.00 |
| csharp | cpp | aggregate | 65536 | 4 | 100 | arrival-correctness | burst | 1 | 0 | 74.52 | 74.52–74.52 |
| csharp | cpp | aggregate | 65536 | 4 | 100 | arrival-correctness | poisson | 1 | 0 | 99.00 | 99.00–99.00 |
| csharp | cpp | retain-allocate | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 358.00 | 358.00–358.00 |
| csharp | cpp | retain-allocate | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 50.00 | 50.00–50.00 |
| csharp | cpp | retain-reuse | 1048576 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 498.00 | 498.00–498.00 |
| csharp | cpp | retain-reuse | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 24.00 | 24.00–24.00 |
| csharp | cpp | transport | 16777216 | 4 | 0 | large-frame-correctness | steady | 1 | 0 | 152.00 | 152.00–152.00 |

Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.
Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.
Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit.

## Paired completion-rate ratios

Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.
Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.

A pair is included only when both members pass. Duplicate or incomplete pairs are excluded. Differences in corpus, duration, warmup, window, seed or arrival shape form separate groups.

| Client / mode / bytes / connections / offered rate / phase / arrival | Pairs | Median ratio | Bootstrap 95% interval |
| --- | ---: | ---: | --- |
