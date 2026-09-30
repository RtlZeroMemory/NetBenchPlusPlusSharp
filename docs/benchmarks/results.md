# Library campaign results (29-30 September 2026)

> This campaign compares the standard JSON libraries: safe C# with `System.Text.Json` against C++ with simdjson. A later round put the same hand-tuned parser in both languages and found a tie; see [library vs hand-tuned parsers](parser-challenge.md).

The final campaign ran **270 trials in 4 h 22 min with 0 failures**. All of them reconciled completely. **254 were eligible**; the other 16 were excluded by the pre-declared rule that the load generator must stay under 85% CPU (listed [below](#excluded-trials)). Every number here comes from [`results/final-campaign-20260929`](../../results/final-campaign-20260929/). That folder holds the generated `report.md`, `trials.csv`, `results.json` and one folder per trial with the exact commands and raw JSON. The rules were fixed in advance in the [methodology](methodology.md).

Frozen binaries (`results/audit-checks/frozen-binaries-final.txt`):

- C++23: MSVC 19.51 `/O2`, simdjson 3.12.3.
- C#: .NET 11 preview 7, Server GC, DATAS off. The runtime default is measured separately.

Hardware: Ryzen 7 9800X3D. The server gets 4 physical cores, the load generator 3 others, and one core is left for Windows. It runs on Windows 11 over loopback.

How to read the numbers:

- **M records/s:** the median across repetitions.
- **Latency:** the lowest–highest value across the per-trial percentiles. Percentiles are never averaged. "miss" means that rank landed on a request the server turned away or failed.
- **Ratios:** the median of per-repetition C#/C++ pairs. Where there are 5 or more pairs, a bootstrap 95% interval is shown.
- **Units:** 1 MiB = 1,048,576 bytes. A record is about 160 B (EASY) or about 766 B (HARD) of JSON.

## Verdict

| Contest | Winner | Margin (paired) | Evidence |
| --- | --- | --- | --- |
| EASY max speed, aggregate | C++ | **+5%** (C#/C++ 0.951, interval 0.934–0.969) | 14.64 vs 13.97 M records/s, 7 pairs |
| EASY max speed, retain (reuse) | **tie** | +1.5% (0.985, interval 0.975–1.000) | 11.22 vs 11.11 M records/s, 7 pairs |
| EASY normal load (50%), aggregate | **tie** | p99 ratio 1.11, interval 0.51–1.97 | both p50 ≈ 0.16 ms |
| EASY normal load (50%), retain | C++ | C# p99 2.3× (interval 1.37–∞) | C++ 0.26–2.59 ms, C# 0.29 ms–miss |
| HARD max speed, retain (reuse) | C++ | **+32%** (0.757, interval 0.738–0.815) | 2.47 vs 1.94 M records/s, 5 pairs |
| HARD max speed, retain (fresh memory) | C++ | **+30%** (0.767, interval 0.756–0.780) | 2.44 vs 1.90 M records/s. p99 8.8–10.9 vs 15.4–17.7 ms (one C# outlier at 37 ms) |
| HARD max speed, aggregate | C++ | **+42%** (0.702, 3 eligible pairs) | 3.39 vs 2.34 M records/s |
| HARD normal load (Poisson 70%), fresh memory | C++ | C# p99 **3.1×** (interval 2.76–3.48) | 2.8–3.1 vs 7.8–10.7 ms |
| HARD normal load (Poisson 70%), reuse | C++ | C# p99 **1.7×** (interval 1.56–3.42) | 2.8–3.4 vs 4.4–11.5 ms |
| HARD bursts (1 s at 150%) | C++ | C# turned away ~3× more arrivals | 14–19 k vs 42–49 k rejected per 60 s. Both "miss" at p99 |
| Transport only, no JSON | **tie** | 0.996 on HARD frames; C# +10% on EASY frames | Client-bound (98–99% client CPU), so these are lower bounds |
| Memory (peak working set) | C++ | 4–5× less | EASY 15–21 vs 76–85 MiB; HARD fresh memory 0.2 vs 1.0 GiB |

## EASY: simple JSON

Short ASCII messages, fixed field order, 64 KiB batches, 16 connections.

**Maximum speed** (each connection keeps 4 batches outstanding; 30 s measured, 15 s warmup):

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Server cores used | Server CPU µs / frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| aggregate | C++ | 7/7 | 14.644 | 2,287 | 1.68–1.72 | 1.92–2.24 | 3.97 | 108.5 | 15–16 |
| aggregate | C# | 7/7 | 13.967 | 2,182 | 1.67–1.77 | 3.66–3.85 | 3.83 | 110.0 | 76–81 |
| retain-reuse | C++ | 7/7 | 11.224 | 1,753 | 2.22–2.28 | 2.48–3.05 | 3.96 | 141.7 | 21 |
| retain-reuse | C# | 7/7 | 11.112 | 1,736 | 2.13–2.16 | 4.16–5.34 | 3.81 | 138.4 | 82–85 |

**Normal load**: steady arrivals at 50% of the slower server's capacity; the same schedule for both servers.

| Workload | Server | p50 ms | p99 ms | Requests per trial | Rejected per trial |
| --- | --- | ---: | ---: | ---: | ---: |
| aggregate | C++ | 0.16 | 0.26–miss | 516,920–524,242 | 0–7,322 |
| aggregate | C# | 0.16–0.19 | 0.32–5.28 | 522,059–524,242 | 0–2,183 |
| retain-reuse | C++ | 0.19–0.21 | 0.26–2.59 | 416,100–417,076 | 0–976 |
| retain-reuse | C# | 0.19–0.29 | 0.29–miss | 393,643–417,076 | 0–23,433 |

This phase was noisy for both servers. In several trials, short stalls filled all 64 in-flight slots, so arrivals were turned away. That happened for both servers, most often when background desktop load was high. The worst C# trial had 46% background CPU and 67% busy on the server's idle SMT siblings. The raw per-trial rows are in `trials.csv`.

## HARD: realistic, messy JSON

Shuffled field order, escapes and Unicode, messages up to 4 KiB, batches from 4 KiB to 1 MiB, 64 batches retained per connection. 60 s measured, 20 s warmup.

**Maximum speed:**

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Server cores used | Server CPU µs / frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| retain-reuse | C++ | 5/5 | 2.467 | 1,803 | 5.87–6.13 | 8.55–11.63 | 3.94 | 373.8 | 702–703 |
| retain-reuse | C# | 5/5 | 1.937 | 1,416 | 7.67–8.18 | 13.27–15.20 | 3.89 | 488.0 | 868–873 |
| retain-allocate | C++ | 5/5 | 2.444 | 1,787 | 6.03–6.21 | 8.81–10.88 | 3.93 | 383.8 | 199–204 |
| retain-allocate | C# | 5/5 | 1.896 | 1,386 | 7.50–7.90 | 15.40–37.22 | 3.85 | 494.8 | 1,016–1,022 |
| aggregate | C++ | 3/5 | 3.387 | 2,476 | 4.01–4.23 | 6.77–7.78 | 3.83 | 274.0 | 41 |
| aggregate | C# | 3/5 | 2.336 | 1,707 | 5.98–6.19 | 12.39–13.01 | 3.65 | 379.8 | 76–77 |

A single C# retain-allocate trial reached p99 37.2 ms. Its GC pause total (0.81 s) and background load (4%) matched the other four trials, which were 15.4–17.7 ms. The cause is unexplained. In the aggregate cell, 2 of 5 pairs were excluded because the load generator hit 88% CPU against the C++ server.

**Normal load:** Poisson arrivals at 70% of the slower (C#) server's capacity.

| Workload | Server | p50 ms | p99 ms | Rejected per trial | GC pause in 60 s |
| --- | --- | ---: | ---: | ---: | ---: |
| retain-allocate | C++ | 0.50–0.56 | 2.80–3.08 | 0–59 | – |
| retain-allocate | C# | 0.74–0.83 | 7.80–10.72 | 98–645 | 0.60–0.63 s |
| retain-reuse | C++ | 0.50–0.54 | 2.83–3.36 | 0–149 | – |
| retain-reuse | C# | 0.73–0.87 | 4.41–11.47 | 4–785 | 0 (no GC) |

**Bursts:** 5 s at 60%, then 1 s at 150% of the slower server's capacity. Both servers overflowed the 64-request window during bursts, so both p99 values are "miss". C++ turned away 14,359–19,440 arrivals per trial and C# 42,073–49,467.

**Overload** (120% steady): all 3 pairs were excluded because the load generator reached 90% CPU. The raw trials are kept.

**Controlled comparisons**, which change one variable from EASY:

| Change from EASY | C#/C++ records/s | Interval |
| --- | ---: | --- |
| HARD content, EASY batch sizes, aggregate | 0.694 | 0.668–0.718 |
| HARD content, EASY batch sizes, retain-reuse | 0.724 | 0.699–0.742 |
| HARD, retain-allocate with 8 instead of 64 batches | 0.754 | 0.726–0.791 |

The HARD-vs-EASY gap comes from the content. Batch size and retention depth barely change it.

## Why: controls and diagnostics

**Processing only**: one core, no sockets, the same corpora; 3 runs each.

| Content / mode | C++ M records/s | C# M records/s | C#/C++ |
| --- | ---: | ---: | ---: |
| EASY aggregate | 4.380 | 4.316 | 0.988 |
| EASY retain-reuse | 3.248 | 3.308 | 1.018 |
| HARD 64 KiB aggregate | 1.015 | 0.684 | 0.674 |
| HARD mixed aggregate | 1.019 | 0.694 | 0.679 |
| HARD mixed retain-reuse | 0.737 | 0.547 | 0.749 |
| HARD mixed retain-allocate | 0.728 | 0.536 | 0.731 |

**Transport only**: frames received and acknowledged, no JSON.

| Frames | C++ MiB/s | C# MiB/s | C#/C++ |
| --- | ---: | ---: | ---: |
| EASY 64 KiB | 3,085 | 3,418 | 1.104 |
| HARD 4 KiB–1 MiB | 3,003 | 2,995 | 0.996 |

Both transport cells are client-bound (98–99% client CPU), so read them as lower bounds. They show no networking disadvantage for C#.

**Load-generator checks:**

| Check | EASY aggregate, C#/C++ | HARD retain-reuse, C#/C++ |
| --- | ---: | ---: |
| Main campaign (C++ client, 3 cores) | 0.951 | 0.757 |
| 4th client core | 0.954 | 0.771 |
| C# client instead of C++ | 0.943 | 0.770 |

The result does not depend on the load generator.

**Where HARD time goes.** These are ablations on HARD content, per 766-byte record, from [the optimization diagnostics](../../results/diagnostics-csharp-parser-20260929/README.md):

- The required FNV-1a digest costs about 0.3 µs in both languages.
- Excluding it, parsing and validation take 1.14 µs in C# and 0.67 µs in C++.
- `Utf8JsonReader.Read()` tokenizing alone takes 0.62 µs, about as much as simdjson's whole parse.

## Memory, allocation and GC

| Workload | Server | Allocated B/record | GC gen0 / gen1 / gen2 in interval | GC pause total | Peak RAM |
| --- | --- | ---: | --- | ---: | ---: |
| EASY aggregate / reuse | C++ | 0.0 | – | – | 15–21 MiB |
| EASY aggregate / reuse | C# | 0.1 | 0 / 0 / 0 | 0 | 76–85 MiB |
| HARD retain-reuse | C++ | 0.0 | – | – | 702–703 MiB |
| HARD retain-reuse | C# | 0.2 | 0 / 0 / 0 | 0 | 868–873 MiB |
| HARD retain-allocate | C++ | 828.8 | – | – | 199–204 MiB |
| HARD retain-allocate (DATAS off) | C# | 829.5 | 110–115 / 110–115 / 0 | 0.81–0.86 s | 1,016–1,022 MiB |
| HARD retain-allocate (runtime default, DATAS on) | C# | 829.6–855.8 | 785–813 / 359–369 / 114–129 | 3.92–4.05 s | 1,074–1,211 MiB |

Allocation volume per record is the same in both languages, because both use the same chunked storage. The measured interval runs from the first Begin to the last End; C++ counts global `operator new`.

C#'s peak working set includes the runtime and the managed heap: 76–85 MiB on EASY against 15–21 MiB for C++. HARD retain-reuse holds about 0.7 GiB of batch storage in both (42.7 MiB per connection × 16); C# adds its heap on top.

**Tuned vs default C# GC**, measured in the same campaign phase against C++:

| HARD retain-allocate | DATAS off (tuned) | DATAS on (runtime default) |
| --- | --- | --- |
| Saturation, C# M records/s | 1.896 (5 runs) | 1.618 (3 runs) |
| Saturation, C#/C++ | 0.767 | 0.694 |
| Saturation, C# p99 | 15.4–37.2 ms | 22.5–23.4 ms |
| Poisson 70%, C# p99 | 7.8–10.7 ms | miss (4,477–4,949 rejected) |
| GC pause in 60 s | 0.81–0.86 s | 3.92–4.05 s |

## Before and after the C# optimization

The same cells ran first with the frozen pre-optimization binaries ([stopped campaign](../../results/rework-campaign-20260929/NOTE.md)); both columns show C#/C++ paired ratios.

| Cell | Before | After |
| --- | ---: | ---: |
| EASY processing only, aggregate | 0.864 | 0.988 |
| EASY aggregate, saturation | 0.850 | 0.951 |
| EASY retain-reuse, saturation | 0.919 | 0.985 |
| HARD retain-reuse, saturation | 0.744 | 0.757 |
| HARD retain-allocate, saturation | 0.715 | 0.767 |
| HARD content at EASY settings, aggregate | 0.681 | 0.694 |

C# retain-allocate p99 went from 20.7–31.5 ms to 15.4–17.7 ms, apart from the one 37 ms outlier. The changes are listed in the [methodology](methodology.md#optimization-before-the-final-campaign-declared-before-its-first-trial).

## Do the first-pass conclusions survive?

- **"C++ is 12–15% faster on 64 KiB workloads."** It survives, and the gap is larger. The same first-pass corpus now gives C#/C++ ratios of 0.779–0.801, meaning C++ is 25–28% faster. The first-pass C++ server had been held back by its own saturated load generator.
- **"C# has lower p99 with the C++ client."** It does not survive. That result measured load-generator queueing and connection starvation. With both fixed, C++ has the lower closed-loop p99 in every cell: 2.5–3.2 ms vs 4.6–6.5 ms on the first-pass corpus.
- **The 1 MiB anomaly** was TCP flow control, not GC. See the [methodology](methodology.md#what-the-diagnostics-established).
- **"Fresh allocation gives variable gen2 counts."** That was true with the default GC: 114–129 gen2 collections per run. With chunked storage and DATAS off it disappears (0 gen2).

## Facts and hypotheses

Facts (measured):

- The throughput gap follows JSON content: C# is at 99–102% of C++ on EASY processing and at 67–75% on HARD, and networking alone is on par.
- On EASY, C# spends the same CPU per frame (110.0 vs 108.5 µs). It uses 3.83 of its 4 cores against C++'s 3.97, and that accounts for the 5% throughput gap.
- `Utf8JsonReader` tokenizing alone costs about as much as simdjson's full parse.
- Fresh allocation costs C# 0.8 s of GC pause per minute (tuned) or about 4 s (runtime default); C++ has no collector.
- At the same offered load, C# is closer to its own capacity. At 70% of C#'s capacity, C++ runs at about 54% of its own, so C#'s queues are longer.

Hypotheses, consistent with the data but not proven:

- The HARD parsing gap comes from simdjson's SIMD structural indexing and bulk UTF-8 validation, against `Utf8JsonReader`'s token-by-token state machine plus a separate string-decoding pass.
- The unused 4% of C# server CPU comes from thread-pool scheduling after each per-frame `Task.Yield`.
- The one 37 ms C# p99 outlier has no identified cause.

No universal claim follows from this. Another JSON library, runtime version, CPU or network could move every margin.

## Excluded trials

All 16 exclusions come from the pre-declared client-CPU gate (at most 85% of the load generator's cores). None were failures.

- HARD aggregate saturation: repetitions 1 and 4. Client at 88% with C++; the C# partners were excluded with them.
- HARD overload at 120%: all 3 pairs. Client at 90% with C++; the C# partners were excluded with them.
- Stress with socket I/O capped at 4 KiB: all 3 pairs. Client at 96–99% for both servers.

## Reproduce

```powershell
./bench/build.ps1
./tests/run_all.ps1
./bench/run.ps1 -Matrix bench/matrices/campaign.json -Output results/my-campaign
```
