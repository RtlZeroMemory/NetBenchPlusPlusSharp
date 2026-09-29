# Loopback benchmark report

Every raw trial remains available in `results.json` and `trials.csv`.
Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.

Only verified, generator-adequate trials without failed/unresolved requests enter the comparisons below. Unsustainable but correctly reconciled overload trials remain eligible and are counted explicitly.
Eligible trials: 2 / 2. Missing adequacy flags are unverified, not assumed passing.

| Excluded trial | Reasons |
| --- | --- |

| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Phase | Arrival | Trials | Unsustainable trials | Median completed frames/s | Min–max completed frames/s |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| cpp | cpp | aggregate | 65536 | 4 | 1000 | calibration | steady | 1 | 1 | 400.00 | 400.00–400.00 |
| csharp | cpp | aggregate | 65536 | 4 | 1000 | measurement | steady | 1 | 1 | 800.00 | 800.00–800.00 |

Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.
Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.
Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit.

## Paired completion-rate ratios

Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.
Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.

A pair is included only when both members pass. Duplicate or incomplete pairs are excluded. Differences in corpus, duration, warmup, window, seed or arrival shape form separate groups.

| Client / mode / bytes / connections / offered rate / phase / arrival | Pairs | Median ratio | Bootstrap 95% interval |
| --- | ---: | ---: | --- |
