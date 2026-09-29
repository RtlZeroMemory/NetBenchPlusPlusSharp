# Loopback benchmark report

Every raw trial remains available in `results.json` and `trials.csv`.
Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.

Only verified, generator-adequate trials without failed/unresolved requests enter the comparisons below. Unsustainable but correctly reconciled overload trials remain eligible and are counted explicitly.
Eligible trials: 24 / 24. Missing adequacy flags are unverified, not assumed passing.

| Excluded trial | Reasons |
| --- | --- |

| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Phase | Arrival | Trials | Unsustainable trials | Median completed frames/s | Min–max completed frames/s |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| cpp | cpp | aggregate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 120136.00 | 120136.00–120136.00 |
| cpp | cpp | aggregate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| cpp | cpp | retain-allocate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 115956.00 | 115956.00–115956.00 |
| cpp | cpp | retain-allocate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| cpp | cpp | retain-reuse | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 119470.00 | 119470.00–119470.00 |
| cpp | cpp | retain-reuse | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| cpp | csharp | aggregate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 126240.00 | 126240.00–126240.00 |
| cpp | csharp | aggregate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| cpp | csharp | retain-allocate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 134452.00 | 134452.00–134452.00 |
| cpp | csharp | retain-allocate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| cpp | csharp | retain-reuse | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 131716.00 | 131716.00–131716.00 |
| cpp | csharp | retain-reuse | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | cpp | aggregate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 58890.00 | 58890.00–58890.00 |
| csharp | cpp | aggregate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | cpp | retain-allocate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 49838.00 | 49838.00–49838.00 |
| csharp | cpp | retain-allocate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | cpp | retain-reuse | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 54404.00 | 54404.00–54404.00 |
| csharp | cpp | retain-reuse | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | csharp | aggregate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 60154.00 | 60154.00–60154.00 |
| csharp | csharp | aggregate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | csharp | retain-allocate | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 46636.00 | 46636.00–46636.00 |
| csharp | csharp | retain-allocate | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |
| csharp | csharp | retain-reuse | 4096 | 4 | 0 | measurement | steady | 1 | 0 | 58470.00 | 58470.00–58470.00 |
| csharp | csharp | retain-reuse | 4096 | 4 | 100 | measurement | steady | 1 | 0 | 100.00 | 100.00–100.00 |

Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.
Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.
Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit.

## Paired completion-rate ratios

Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.
Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.

A pair is included only when both members pass. Duplicate or incomplete pairs are excluded. Differences in corpus, duration, warmup, window, seed or arrival shape form separate groups.

| Client / mode / bytes / connections / offered rate / phase / arrival | Pairs | Median ratio | Bootstrap 95% interval |
| --- | ---: | ---: | --- |
| cpp / aggregate / 4096 / 4 / 0 / measurement / steady | 1 | 0.4902 | insufficient independent pairs |
| cpp / aggregate / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
| cpp / retain-allocate / 4096 / 4 / 0 / measurement / steady | 1 | 0.4298 | insufficient independent pairs |
| cpp / retain-allocate / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
| cpp / retain-reuse / 4096 / 4 / 0 / measurement / steady | 1 | 0.4554 | insufficient independent pairs |
| cpp / retain-reuse / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
| csharp / aggregate / 4096 / 4 / 0 / measurement / steady | 1 | 0.4765 | insufficient independent pairs |
| csharp / aggregate / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
| csharp / retain-allocate / 4096 / 4 / 0 / measurement / steady | 1 | 0.3469 | insufficient independent pairs |
| csharp / retain-allocate / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
| csharp / retain-reuse / 4096 / 4 / 0 / measurement / steady | 1 | 0.4439 | insufficient independent pairs |
| csharp / retain-reuse / 4096 / 4 / 100 / measurement / steady | 1 | 1.0000 | insufficient independent pairs |
