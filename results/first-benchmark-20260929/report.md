# Loopback benchmark report

Every raw trial remains available in `results.json` and `trials.csv`.
Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.

Only verified, generator-adequate trials without failed/unresolved requests enter the comparisons below. Unsustainable but correctly reconciled overload trials remain eligible and are counted explicitly.
Eligible trials: 36 / 54. Missing adequacy flags are unverified, not assumed passing.

| Excluded trial | Reasons |
| --- | --- |
| F:\SocketServerClient\results\first-benchmark-20260929\0037-aggregate-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0038-aggregate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0039-aggregate-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0040-aggregate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0041-aggregate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0042-aggregate-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0043-retain-reuse-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0044-retain-reuse-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0045-retain-reuse-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0046-retain-reuse-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0047-retain-reuse-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0048-retain-reuse-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0049-retain-allocate-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0050-retain-allocate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0051-retain-allocate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0052-retain-allocate-csharp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0053-retain-allocate-cpp-cpp | generator_inadequate_or_unverified |
| F:\SocketServerClient\results\first-benchmark-20260929\0054-retain-allocate-csharp-cpp | generator_inadequate_or_unverified |

| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Phase | Arrival | Trials | Unsustainable trials | Median completed frames/s | Min–max completed frames/s |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| cpp | cpp | aggregate | 1048576 | 1 | 0 | closed-loop | steady | 3 | 0 | 83.13 | 76.97–83.80 |
| cpp | cpp | aggregate | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 26251.20 | 25951.77–26441.23 |
| cpp | cpp | retain-allocate | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 21309.20 | 21251.13–21548.67 |
| cpp | cpp | retain-reuse | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 21498.07 | 21493.33–22367.40 |
| cpp | cpp | transport | 1048576 | 1 | 0 | transport-control | steady | 1 | 0 | 1016.13 | 1016.13–1016.13 |
| cpp | cpp | transport | 65536 | 16 | 0 | transport-control | steady | 1 | 0 | 28132.77 | 28132.77–28132.77 |
| cpp | csharp | aggregate | 1048576 | 1 | 0 | alternate-client | steady | 1 | 0 | 86.90 | 86.90–86.90 |
| cpp | csharp | aggregate | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 27277.63 | 27277.63–27277.63 |
| cpp | csharp | retain-allocate | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 21798.90 | 21798.90–21798.90 |
| cpp | csharp | retain-reuse | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 22782.67 | 22782.67–22782.67 |
| csharp | cpp | aggregate | 1048576 | 1 | 0 | closed-loop | steady | 3 | 0 | 77.70 | 69.40–83.80 |
| csharp | cpp | aggregate | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 22737.47 | 21855.10–23499.83 |
| csharp | cpp | retain-allocate | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 18611.83 | 18450.50–19547.20 |
| csharp | cpp | retain-reuse | 65536 | 16 | 0 | closed-loop | steady | 3 | 0 | 19215.73 | 18561.83–19648.10 |
| csharp | cpp | transport | 1048576 | 1 | 0 | transport-control | steady | 1 | 0 | 970.17 | 970.17–970.17 |
| csharp | cpp | transport | 65536 | 16 | 0 | transport-control | steady | 1 | 0 | 29259.70 | 29259.70–29259.70 |
| csharp | csharp | aggregate | 1048576 | 1 | 0 | alternate-client | steady | 1 | 0 | 97.47 | 97.47–97.47 |
| csharp | csharp | aggregate | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 24817.83 | 24817.83–24817.83 |
| csharp | csharp | retain-allocate | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 19285.57 | 19285.57–19285.57 |
| csharp | csharp | retain-reuse | 65536 | 16 | 0 | alternate-client | steady | 1 | 0 | 18915.83 | 18915.83–18915.83 |

Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.
Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.
Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit.

## Paired completion-rate ratios

Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.
Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.

A pair is included only when both members pass. Duplicate or incomplete pairs are excluded. Differences in corpus, duration, warmup, window, seed or arrival shape form separate groups.

| Client / mode / bytes / connections / offered rate / phase / arrival | Pairs | Median ratio | Bootstrap 95% interval |
| --- | ---: | ---: | --- |
| cpp / aggregate / 1048576 / 1 / 0 / closed-loop / steady | 3 | 1.0080 | insufficient independent pairs |
| cpp / aggregate / 65536 / 16 / 0 / closed-loop / steady | 3 | 0.8661 | insufficient independent pairs |
| cpp / retain-allocate / 65536 / 16 / 0 / closed-loop / steady | 3 | 0.8758 | insufficient independent pairs |
| cpp / retain-reuse / 65536 / 16 / 0 / closed-loop / steady | 3 | 0.8636 | insufficient independent pairs |
| cpp / transport / 1048576 / 1 / 0 / transport-control / steady | 1 | 0.9548 | insufficient independent pairs |
| cpp / transport / 65536 / 16 / 0 / transport-control / steady | 1 | 1.0401 | insufficient independent pairs |
| csharp / aggregate / 1048576 / 1 / 0 / alternate-client / steady | 1 | 1.1216 | insufficient independent pairs |
| csharp / aggregate / 65536 / 16 / 0 / alternate-client / steady | 1 | 0.9098 | insufficient independent pairs |
| csharp / retain-allocate / 65536 / 16 / 0 / alternate-client / steady | 1 | 0.8847 | insufficient independent pairs |
| csharp / retain-reuse / 65536 / 16 / 0 / alternate-client / steady | 1 | 0.8303 | insufficient independent pairs |
