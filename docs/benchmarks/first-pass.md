# First benchmark pass (29 September 2026)

> **Superseded, kept as historical evidence.** A later audit found that the common native client was CPU-saturated, closed-loop tails measured client queueing and connection starvation, and the 1 MiB anomaly was TCP flow control. See the [methodology](methodology.md) and the [final results](results.md). Nothing below has been changed or reclassified.

All **54 trials** reconciled successfully with zero failed or unresolved requests. The measured windows processed **8.96 billion JSON records** and transferred **1.66 TiB of payload**, including transport controls. There are **36 comparison-eligible closed-loop/control trials**. All **18 scheduled-load trials** failed the predeclared generator-timing gate and are retained as diagnostics, not latency/SLO comparisons.

For the three 64 KiB workloads, native application throughput was about **12–15% higher** by the ratio of server medians. With the common C++ client, C# had lower observed p99 latency, although allocation-mode tails varied substantially. This is an application-and-client comparison, not a universal language ranking or proof that GC caused any latency spike.

## Readable results

Throughput columns are medians of three runs. Latency columns show the **lowest–highest per-trial percentile**, without averaging or pooling percentiles. Peak RAM is the maximum observed server process working set across the three runs, including setup, warmup and shutdown; it excludes the separate client process. Higher throughput is better; lower latency is better.

| Workload | Server | Million records/s ↑ | JSON MiB/s ↑ | p50 latency ms ↓ | p99 latency ms ↓ | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| JSON totals · 64 KiB | C# | 8.076 | 1,419 | 2.54–2.72 | 6.62–7.01 | 58.1 |
| JSON totals · 64 KiB | C++ | 9.324 | 1,638 | 0.68–0.76 | 26.48–27.20 | 18.9 |
| Retain records · reuse | C# | 6.825 | 1,199 | 3.10–3.28 | 7.77–8.03 | 71.8 |
| Retain records · reuse | C++ | 7.636 | 1,341 | 0.70–0.74 | 33.82–34.34 | 27.9 |
| Retain records · allocate | C# | 6.611 | 1,161 | 1.70–3.33 | 8.06–25.76 | 109.8 |
| Retain records · allocate | C++ | 7.569 | 1,330 | 0.69–0.71 | 35.78–36.18 | 32.2 |
| JSON totals · 1 MiB* | C# | 0.442 | 78 | 799.01–964.69 | 998.24 | 43.6 |
| JSON totals · 1 MiB* | C++ | 0.473 | 83 | 754.97–872.42 | 998.24–1,002.44 | 11.7 |

Each JSON object is one record. “JSON totals” validates and aggregates directly; “reuse” keeps owned typed records in reusable storage; “allocate” creates fresh owned storage for each batch. p50 describes the middle completed batch; p99 describes the slow end (99% of acknowledged batches completed within that time). These are saturation latencies with up to 64 outstanding batches, not unloaded response times. Quantiles are histogram bucket upper bounds. MiB = 1,048,576 bytes.

The 64 KiB cases use 16 connections. **The 1 MiB case uses one connection and is diagnostic:** its throughput varied, paired results did not produce a consistent winner, and only 2,146–2,578 acknowledged batches occurred per primary trial. Its empirical p99 values are not precise tail estimates. Around 64 MiB can be queued in that configuration, so near-one-second batch latency must not be called a one-second GC pause. Both native and managed runs showed the effect, and the managed server recorded no GC in those trials. The cause of the low processed throughput needs a separate transport/queueing investigation.

## Allocation and GC observations

Ranges below cover the three primary managed server runs. They include setup, five seconds of warmup, the measured window, drain and shutdown. Allocation volume is cumulative memory allocated, not memory retained or peak RAM. Generation counters describe young (0), older (1) and full-heap (2) collections; do not add them as independent categories.

| C# workload | Allocated MiB per server run | Gen 0 collections | Gen 1 collections | Gen 2 collections |
| --- | ---: | ---: | ---: | ---: |
| JSON totals · 64 KiB | 24.4–25.1 | 3 | 1 | 0 |
| Retain records · reuse | 36.7–37.2 | 3 | 1 | 0 |
| Retain records · allocate | 64,166.8–67,057.1 | 1,653–1,824 | 1,649–1,820 | 2–136 |
| JSON totals · 1 MiB* | 3.4 | 0 | 0 | 0 |

Reuse sharply reduced managed allocation pressure. Fresh allocation produced about 62.7–65.5 GiB per server run and widely varying generation-2 counts. Counters alone do not establish pause duration or causality. C++ has no managed GC; native allocation/free counts were not instrumented. Runtime “last GC” observations are not a full pause timeline and are not reported as a benchmark-wide pause percentage.

## Variation and paired comparison

| Workload | C# million records/s, min–max | C++ million records/s, min–max | Median paired C# / C++ ratio |
| --- | ---: | ---: | ---: |
| JSON totals · 64 KiB | 7.763–8.347 | 9.218–9.392 | 0.866 |
| Retain records · reuse | 6.593–6.979 | 7.634–7.945 | 0.864 |
| Retain records · allocate | 6.553–6.943 | 7.548–7.654 | 0.876 |
| JSON totals · 1 MiB* | 0.395–0.477 | 0.438–0.477 | 1.008 |

The paired ratio uses corresponding repetition IDs, so it need not equal the ratio of two separate medians. Three pairs are exploratory; no bootstrap confidence interval is claimed. The large-frame case illustrates why pairing and raw variation matter.

## Client and transport checks

One additional pair per workload used the C# client, under the same three-core client budget. These are sensitivity checks, not three-repeat confirmation:

| Workload | C# million records/s | C++ million records/s | C# p99 ms | C++ p99 ms |
| --- | ---: | ---: | ---: | ---: |
| JSON totals · 64 KiB | 8.815 | 9.689 | 27.98 | 31.06 |
| Retain records · reuse | 6.719 | 8.092 | 8.03 | 36.96 |
| Retain records · allocate | 6.850 | 7.743 | 27.85 | 38.54 |
| JSON totals · 1 MiB* | 0.555 | 0.495 | 1000.34 | 1000.34 |

Alternate-client runs showed different tails: managed aggregate p99 was 27.98 ms with the alternate client, versus 6.62–7.01 ms with the common native client. One additional pair cannot isolate the cause of that difference; do not attribute it to the server language alone.

The transport-only controls transferred the same payloads and acknowledged frames without JSON processing. They used the C++ client:

| Frame target / connections | C# server MiB/s | C++ server MiB/s |
| --- | ---: | ---: |
| 64 KiB / 16 | 1,826 | 1,755 |
| 1024 KiB / 1 | 970 | 1,016 |

The 64 KiB transport rates leave limited headroom over native aggregation. This experiment does not prove an isolated server ceiling. Do not subtract transport time/throughput from JSON results to derive parser cost. Native and managed server CPU/resource counters cover their full server lifetimes; client/process-only resource scopes differ and are not compared as measured CPU per record.

## Receiving versus processing

Server histograms sample every 1024th data frame. These are per-trial p50 ranges, not averaged or additive components:

| Workload / server | Receive p50 µs, min–max | Processing p50 µs, min–max | Samples per trial |
| --- | ---: | ---: | ---: |
| JSON totals · 64 KiB / C# | 7.0–7.1 | 133.1–140.3 | 640–688 |
| JSON totals · 64 KiB / C++ | 303.1–320.5 | 121.9–122.1 | 752–768 |
| Retain records · reuse / C# | 7.0–7.1 | 164.4–166.4 | 528–560 |
| Retain records · reuse / C++ | 359.4–380.9 | 150.0–151.0 | 624–640 |
| Retain records · allocate / C# | 7.1–7.9 | 166.4–170.0 | 528–560 |
| Retain records · allocate / C++ | 376.8–385.0 | 155.6–156.7 | 608–624 |

Receive elapsed includes waiting from the header-receive issue and is not CPU time spent copying bytes. Processing includes validation, categorization and, in retained modes, materialization and eviction verification. All stage p99 values remain gated off for insufficient samples. The 1 MiB case produced only two stage samples per trial and is omitted from this stage comparison.

## Scheduled-load limits

The three 64 KiB workloads were each offered 50%, 90% and 120% of the slower primary median frame rate, identically for both servers. All 18 trials reconciled complete offered demand and had no failed/unresolved requests, but every trial had at least one scheduled arrival more than 1 ms late. The strict predeclared rule therefore excludes all 18 from comparative SLO/latency evidence. Even the lightest affected run had one such event; the gate was not weakened after observing results. Rejections, timing misses and raw latency remain in the downloadable data. No sustainable-rate or real-time winner is established by this phase.

## Reproduction and evidence

- Native Windows 11 build 26200; Ryzen 7 9800X3D, 8 physical cores / 16 logical CPUs, 61.65 GiB RAM, 96 MiB L3; High Performance power plan.
- Identical server affinity: logical CPUs 0, 2, 4, 6 (four separate physical cores). Client: 8, 10, 12 (three separate physical cores). One physical core remains outside both masks; SMT siblings are not shared between client and server. Affinity does not isolate background Windows activity, shared cache, DRAM or power.
- C# Release: SDK `11.0.100-preview.7.26381.103`, runtime `11.0.0-preview.7.26381.103`, server/concurrent GC, pinned roll-forward disabled. C++23 Release: MSVC 19.51.36260, simdjson 3.12.3 (`icelake` implementation), Windows SDK 10.0.26100.0, no sanitizer.
- Thirty measured seconds plus five seconds of warmup per trial. Three randomized server-order pairs per primary workload, fixed C++ client, global window 64, global payload-in-flight limit 64 MiB, requested socket buffers 256 KiB, TCP_NODELAY. Four server CPU cores and three client CPU cores; the native server uses four IOCP workers and the managed server uses the runtime thread pool.
- Two deterministic seed-42 corpora, each approximately 256 MiB of unique payload (larger than L3). Corpus generation and loading preceded timing. Exact hashes and actual sizes are recorded in the trial data. Strict schema, digest/category results and retained ownership are common to both implementations.
- All reviews, builds, sanitizer/protocol tests and stress probes finished before this campaign. Ordinary desktop/background activity was not instrumented or isolated. These are loopback results for this PC and configuration.

```powershell
python bench/run.py --suite first --output results/my-first-pass
python bench/report.py results/my-first-pass
```

Artifacts: [all 54 trial rows](first-pass-trials.csv), [complete results JSON, gzip](first-pass-results.json.gz), [environment and source hashes](first-pass-environment.json), [structured summary](first-pass-summary.json), [review and fix evidence](../../reviews/round2/README.md). The gzip contains the unchanged campaign `results.json`, including raw histograms and server/client results. Local manifests, commands and logs remain under `results/first-benchmark-20260929/`.

Frozen C# DLL SHA-256: `cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd`.

Frozen C++ Release EXE SHA-256: `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329`.

Long confirmation/soak runs, CPU-budget and socket-buffer sensitivity, complete allocation tracing and GC-suspension attribution remain further experiments. This first pass makes no universal language or hard-real-time claim.
