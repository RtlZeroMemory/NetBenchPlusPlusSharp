# Loopback benchmark report

Every raw trial remains available in `results.json` and `trials.csv`.
Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.

| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Trials | Median completed frames/s | Min–max completed frames/s |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cpp | csharp | aggregate | 65536 | 1 | 1000 | 1 | 1000.00 | 1000.00–1000.00 |
| csharp | csharp | aggregate | 65536 | 1 | 1000 | 1 | 1000.00 | 1000.00–1000.00 |

Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.
Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.
Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit.

## Paired completion-rate ratios

Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.
Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.

| Client / mode / bytes / connections / offered rate / phase | Pairs | Median ratio | Bootstrap 95% interval |
| --- | ---: | ---: | --- |
| csharp / aggregate / 65536 / 1 / 1000 / measurement | 1 | 1.0000 | insufficient independent pairs |
