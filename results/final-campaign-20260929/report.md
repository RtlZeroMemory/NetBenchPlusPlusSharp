# Campaign report

254 of 270 trials eligible (both members of a repetition pair must pass). Throughput: median across eligible trials. Latency: lowest–highest per-trial percentile over offered requests from intended arrival; "miss" means the rank falls on rejected, failed or unresolved demand. Percentiles are never averaged. MiB = 1,048,576 bytes.

## Processing-only controls (one core, no sockets) (processing only)

| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |
| --- | --- | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 3/3 | 4.380 | 684 | 4.062–4.417 |
| EASY aggregate | C# | 3/3 | 4.316 | 674 | 4.308–4.365 |
| EASY retain-reuse | C++ | 3/3 | 3.248 | 507 | 3.221–3.250 |
| EASY retain-reuse | C# | 3/3 | 3.308 | 517 | 3.260–3.332 |
| HARD mixed frames, retain-allocate | C++ | 3/3 | 0.728 | 532 | 0.726–0.738 |
| HARD mixed frames, retain-allocate | C# | 3/3 | 0.536 | 392 | 0.532–0.543 |
| HARD content, 64 KiB frames, aggregate | C++ | 3/3 | 1.015 | 741 | 1.012–1.021 |
| HARD content, 64 KiB frames, aggregate | C# | 3/3 | 0.684 | 500 | 0.682–0.684 |
| HARD mixed frames, retain-reuse | C++ | 3/3 | 0.737 | 539 | 0.728–0.740 |
| HARD mixed frames, retain-reuse | C# | 3/3 | 0.547 | 400 | 0.539–0.554 |
| HARD mixed frames, aggregate | C++ | 3/3 | 1.019 | 745 | 1.011–1.022 |
| HARD mixed frames, aggregate | C# | 3/3 | 0.694 | 507 | 0.680–0.694 |

## EASY — closed-loop saturation (16 connections × 4 outstanding)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 7/7 | 14.644 | 2,287 | 1.68–1.72 | 1.92–2.24 | 1,082,008–1,124,048 | 0 | 3.97 | 108.5 | 15–16 |
| EASY aggregate | C# | 7/7 | 13.967 | 2,182 | 1.67–1.77 | 3.66–3.85 | 1,001,087–1,081,618 | 0 | 3.83 | 110.0 | 76–81 |
| EASY retain-reuse | C++ | 7/7 | 11.224 | 1,753 | 2.22–2.28 | 2.48–3.05 | 823,274–852,354 | 0 | 3.96 | 141.7 | 21 |
| EASY retain-reuse | C# | 7/7 | 11.112 | 1,736 | 2.13–2.16 | 4.16–5.34 | 814,419–839,314 | 0 | 3.81 | 138.4 | 82–85 |

## HARD — closed-loop saturation (16 × 4, 4 KiB–1 MiB frames)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-reuse, 64 batches | C++ | 5/5 | 2.467 | 1,803 | 5.87–6.13 | 8.55–11.63 | 593,643–639,129 | 0 | 3.94 | 373.8 | 702–703 |
| HARD retain-reuse, 64 batches | C# | 5/5 | 1.937 | 1,416 | 7.67–8.18 | 13.27–15.20 | 449,719–483,890 | 0 | 3.89 | 488.0 | 868–873 |
| HARD retain-allocate, 64 batches | C++ | 5/5 | 2.444 | 1,787 | 6.03–6.21 | 8.81–10.88 | 593,613–622,904 | 0 | 3.93 | 383.8 | 199–204 |
| HARD retain-allocate, 64 batches | C# | 5/5 | 1.896 | 1,386 | 7.50–7.90 | 15.40–37.22 | 455,956–472,793 | 0 | 3.85 | 494.8 | 1016–1022 |
| HARD aggregate | C++ | 3/5 | 3.387 | 2,476 | 4.01–4.23 | 6.77–7.78 | 822,535–839,612 | 0 | 3.83 | 274.0 | 41 |
| HARD aggregate | C# | 3/5 | 2.336 | 1,707 | 5.98–6.19 | 12.39–13.01 | 577,051–596,672 | 0 | 3.65 | 379.8 | 76–77 |

## Controlled comparisons (one variable changed from EASY or HARD)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD content at EASY settings: retain-reuse | C++ | 5/5 | 2.539 | 1,855 | 2.06–2.12 | 2.33–2.83 | 881,618–918,455 | 0 | 3.95 | 131.3 | 26–27 |
| HARD content at EASY settings: retain-reuse | C# | 5/5 | 1.861 | 1,360 | 2.65–2.79 | 4.90–6.42 | 632,318–678,753 | 0 | 3.81 | 174.7 | 83–87 |
| HARD content at EASY settings: aggregate | C++ | 5/5 | 3.299 | 2,410 | 1.56–1.62 | 1.81–2.38 | 1,137,923–1,207,335 | 0 | 3.96 | 101.1 | 16 |
| HARD content at EASY settings: aggregate | C# | 5/5 | 2.330 | 1,702 | 2.13–2.24 | 4.28–5.21 | 786,607–838,766 | 0 | 3.82 | 138.2 | 73–74 |
| HARD retain-allocate, 8 batches retained | C++ | 5/5 | 2.478 | 1,811 | 6.06–6.36 | 8.95–10.39 | 582,848–618,768 | 0 | 3.96 | 388.6 | 73–74 |
| HARD retain-allocate, 8 batches retained | C# | 5/5 | 1.822 | 1,332 | 7.82–8.22 | 13.70–16.84 | 447,767–474,430 | 0 | 3.87 | 513.9 | 811–851 |

## EASY — steady arrivals at 50% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY retain-reuse | C++ | 7/7 | 5.555 | 868 | 0.19–0.21 | 0.26–2.59 | 416,100–417,076 | 0–976 | 2.14 | 154.8 | 21 |
| EASY retain-reuse | C# | 7/7 | 5.548 | 867 | 0.19–0.29 | 0.29–miss | 393,643–417,076 | 0–23,433 | 2.39 | 172.5 | 103–157 |
| EASY aggregate | C++ | 7/7 | 6.981 | 1,090 | 0.16 | 0.26–miss | 516,920–524,242 | 0–7,322 | 2.11 | 121.9 | 15 |
| EASY aggregate | C# | 7/7 | 6.975 | 1,089 | 0.16–0.19 | 0.32–5.28 | 522,059–524,242 | 0–2,183 | 2.40 | 138.3 | 138–167 |

## HARD — Poisson arrivals at 70% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-allocate | C++ | 5/5 | 1.328 | 971 | 0.50–0.56 | 2.80–3.08 | 328,276–328,335 | 0–59 | 2.05 | 376.5 | 191–192 |
| HARD retain-allocate | C# | 5/5 | 1.327 | 970 | 0.74–0.83 | 7.80–10.72 | 327,690–328,237 | 98–645 | 2.79 | 511.3 | 1013–1016 |
| HARD retain-reuse | C++ | 5/5 | 1.357 | 992 | 0.50–0.54 | 2.83–3.36 | 335,393–335,542 | 0–149 | 2.07 | 372.3 | 695–697 |
| HARD retain-reuse | C# | 5/5 | 1.356 | 991 | 0.73–0.87 | 4.41–11.47 | 334,757–335,538 | 4–785 | 2.80 | 503.0 | 903–910 |

## HARD — bursts: 5 s at 60%, 1 s at 150% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-allocate | C++ | 5/5 | 1.355 | 991 | 0.79–0.86 | miss | 332,308–337,239 | 14,509–19,440 | 2.13 | 383.0 | 194–195 |
| HARD retain-allocate | C# | 5/5 | 1.245 | 910 | 1.14–1.27 | miss | 306,772–309,675 | 42,073–44,976 | 2.68 | 523.9 | 1011–1012 |
| HARD retain-reuse | C++ | 5/5 | 1.389 | 1,015 | 0.78–0.80 | miss | 341,348–344,949 | 14,359–17,960 | 2.12 | 371.5 | 696–697 |
| HARD retain-reuse | C# | 5/5 | 1.275 | 932 | 1.01–1.31 | miss | 309,841–316,706 | 42,602–49,467 | 2.67 | 510.0 | 894–902 |

## HARD — steady overload at 120% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-allocate | C++ | 0/3 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate | C# | 0/3 | – | – | – | – | – | – | – | – | – |

## C# server with the runtime-default GC (DATAS on); C++ unchanged

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-allocate, saturation (C# DATAS on) | C++ | 3/3 | 2.329 | 1,702 | 6.31–6.32 | 11.21–11.50 | 574,192–576,504 | 0 | 3.77 | 393.4 | 200–205 |
| HARD retain-allocate, saturation (C# DATAS on) | C# | 3/3 | 1.618 | 1,183 | 8.55–8.91 | 22.54–23.40 | 392,125–405,881 | 0 | 3.65 | 548.9 | 1074–1211 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C++ | 3/3 | 1.328 | 970 | 0.63 | 4.11–4.31 | 328,224–328,321 | 14–111 | 2.06 | 377.2 | 191–192 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C# | 3/3 | 1.309 | 957 | 1.50–1.67 | miss | 323,386–323,858 | 4,477–4,949 | 2.86 | 531.8 | 952–965 |

## Transport-only controls (no JSON)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD frames, 4 KiB–1 MiB | C++ | 3/3 | – | 3,003 | 0.29–0.30 | 2.20–2.26 | 507,699–509,961 | 0 | 0.56 | 33.1 | 29 |
| HARD frames, 4 KiB–1 MiB | C# | 3/3 | – | 2,995 | 0.31–0.32 | 2.83–2.88 | 504,365–508,059 | 0 | 1.03 | 61.4 | 151–152 |
| EASY frames, 64 KiB | C++ | 3/3 | – | 3,085 | 0.09 | 0.71–0.74 | 1,479,361–1,493,883 | 0 | 1.47 | 29.9 | 14 |
| EASY frames, 64 KiB | C# | 3/3 | – | 3,418 | 0.11 | 0.77–0.81 | 1,636,519–1,644,025 | 0 | 2.36 | 43.3 | 265–279 |

## Client headroom: same cells with a fourth client core

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate, 4 client cores | C++ | 3/3 | 13.036 | 2,036 | 1.82 | 2.75–2.83 | 978,341–981,122 | 0 | 3.55 | 109.4 | 15 |
| EASY aggregate, 4 client cores | C# | 3/3 | 12.430 | 1,941 | 1.89–1.93 | 4.24–4.42 | 929,458–935,580 | 0 | 3.43 | 111.1 | 74–77 |
| HARD retain-reuse, 4 client cores | C++ | 3/3 | 2.239 | 1,636 | 6.65–6.91 | 14.58–14.97 | 533,782–553,820 | 0 | 3.58 | 389.5 | 700–702 |
| HARD retain-reuse, 4 client cores | C# | 3/3 | 1.727 | 1,262 | 8.52–9.40 | 17.37–18.68 | 392,980–432,182 | 0 | 3.62 | 510.2 | 870 |

## Alternate (C#) client sensitivity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate, C# client | C++ | 3/3 | 13.965 | 2,181 | 1.75–1.76 | 2.42–2.50 | 1,047,074–1,052,875 | 0 | 3.80 | 109.1 | 15 |
| EASY aggregate, C# client | C# | 3/3 | 13.225 | 2,066 | 1.75–1.78 | 3.89–4.10 | 973,629–1,011,106 | 0 | 3.65 | 110.7 | 75–76 |
| HARD retain-reuse, C# client | C++ | 3/3 | 2.377 | 1,737 | 6.34–6.41 | 11.21–12.29 | 581,687–590,496 | 0 | 3.76 | 387.1 | 702–703 |
| HARD retain-reuse, C# client | C# | 3/3 | 1.817 | 1,328 | 8.16–8.24 | 15.70–16.15 | 447,129–453,034 | 0 | 3.72 | 498.6 | 869–870 |

## First-pass bridge: first-pass corpus and retention, rework binaries

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mixed 64 KiB aggregate | C++ | 3/3 | 12.004 | 2,109 | 1.77–1.79 | 2.47–2.57 | 1,007,102–1,019,158 | 0 | 3.80 | 113.0 | 16 |
| Mixed 64 KiB aggregate | C# | 3/3 | 9.353 | 1,643 | 2.23–2.25 | 4.60–5.87 | 781,888–791,522 | 0 | 3.75 | 142.7 | 72 |
| Mixed 64 KiB retain-allocate | C++ | 3/3 | 9.350 | 1,643 | 2.33 | 3.16–3.24 | 787,756–791,251 | 0 | 3.84 | 146.1 | 25 |
| Mixed 64 KiB retain-allocate | C# | 3/3 | 7.445 | 1,308 | 2.79–2.81 | 6.47–6.54 | 628,614–640,760 | 0 | 3.72 | 178.5 | 342–502 |
| Mixed 64 KiB retain-reuse | C++ | 3/3 | 9.546 | 1,677 | 2.28 | 3.13–3.20 | 803,850–806,800 | 0 | 3.81 | 142.2 | 23 |
| Mixed 64 KiB retain-reuse | C# | 3/3 | 7.620 | 1,339 | 2.73–2.78 | 5.19–5.77 | 635,047–646,971 | 0 | 3.72 | 174.9 | 82 |

## Stress (not ordinary valid-traffic performance)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate, every socket operation capped at 4 KiB | C++ | 0/3 | – | – | – | – | – | – | – | – | – |
| EASY aggregate, every socket operation capped at 4 KiB | C# | 0/3 | – | – | – | – | – | – | – | – | – |

## Memory, allocation and GC (server measured interval)

Allocated per record: C# managed bytes (GC.GetTotalAllocatedBytes); C++ global operator new bytes. Owned capacity: retained plus scratch row/text storage per connection. GC counts are collections in the interval, not independent categories.

| Workload | Server | Allocated B/record | Allocations/frame | GC gen0 / gen1 / gen2 | GC pause ms | Owned capacity MiB |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| HARD retain-reuse, 64 batches | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, 64 batches | C# | 0.2 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| HARD retain-allocate, 64 batches | C++ | 828.8–828.9 | 14.0 | – | – | 12.6 |
| HARD retain-allocate, 64 batches | C# | 829.5–829.6 | – | 110–115 / 110–115 / 0 | 809.6–861.2 | 12.6 |
| HARD aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| HARD aggregate | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| HARD content at EASY settings: retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.6 |
| HARD content at EASY settings: retain-reuse | C# | 0.3 | – | 0 / 0 / 0 | 0.0 | 0.6 |
| HARD content at EASY settings: aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| HARD content at EASY settings: aggregate | C# | 0.3 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| HARD retain-allocate, 8 batches retained | C++ | 828.8–828.9 | 14.0 | – | – | 3.0 |
| HARD retain-allocate, 8 batches retained | C# | 829.5–829.6 | – | 131–139 / 131–139 / 0 | 157.4–163.7 | 3.0 |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.3–0.5 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.3–0.4 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| HARD retain-allocate | C++ | 828.8 | 14.0 | – | – | 12.6 |
| HARD retain-allocate | C# | 830.0–830.1 | – | 79–80 / 79–80 / 0 | 598.4–628.7 | 12.8–13.9 |
| HARD retain-reuse | C++ | 0.2 | 0.0 | – | – | 42.7 |
| HARD retain-reuse | C# | 0.8–0.9 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| HARD retain-allocate | C++ | 828.5–828.8 | 14.0 | – | – | 13.5–14.4 |
| HARD retain-allocate | C# | 829.5–830.0 | – | 74–75 / 74–75 / 0 | 565.8–645.6 | 13.7–14.8 |
| HARD retain-reuse | C++ | 0.2 | 0.0 | – | – | 42.7 |
| HARD retain-reuse | C# | 0.7–0.8 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| HARD retain-allocate, saturation (C# DATAS on) | C++ | 828.8–828.9 | 14.0 | – | – | 12.6 |
| HARD retain-allocate, saturation (C# DATAS on) | C# | 829.6–855.8 | – | 785–813 / 359–369 / 114–129 | 3916.9–4053.5 | 12.6 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C++ | 828.8 | 14.0 | – | – | 12.6–13.1 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C# | 829.9–830.0 | – | 1,077–1,095 / 197–202 / 70–74 | 2996.5–3058.0 | 13.3–13.6 |
| HARD frames, 4 KiB–1 MiB | C++ | – | 0.0 | – | – | 0.0 |
| HARD frames, 4 KiB–1 MiB | C# | – | – | 0 / 0 / 0 | 0.0 | 0.0 |
| EASY frames, 64 KiB | C++ | – | 0.0 | – | – | 0.0 |
| EASY frames, 64 KiB | C# | – | – | 1 / 0 / 0 | 0.7–6.7 | 0.0 |
| EASY aggregate, 4 client cores | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate, 4 client cores | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| HARD retain-reuse, 4 client cores | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, 4 client cores | C# | 0.2–0.3 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| EASY aggregate, C# client | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate, C# client | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| HARD retain-reuse, C# client | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, C# client | C# | 0.2 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| Mixed 64 KiB aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| Mixed 64 KiB aggregate | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| Mixed 64 KiB retain-allocate | C++ | 308.1 | 15.9 | – | – | 0.5 |
| Mixed 64 KiB retain-allocate | C# | 308.9 | – | 282–288 / 282–288 / 0 | 286.5–301.9 | 0.5 |
| Mixed 64 KiB retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.5 |
| Mixed 64 KiB retain-reuse | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.5 |

## Load-generator and environment checks

Client CPU is the measured-phase share of the client's cores. Lateness is generator dispatch minus intended time (scheduled only). Strict v1 is the first-pass rule (no arrival over 1 ms late), reported but not used. Sibling busy is foreign CPU on the idle SMT siblings of the server's cores.

| Workload | Server | Client CPU % | Client delay p99 ms | Lateness p99 µs | Late >1 ms | Strict v1 pass | Sibling busy % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 74–80 | 0.00 | – | – | – | 4.0–11.6 |
| EASY aggregate | C# | 70–74 | 0.00 | – | – | – | 4.9–11.6 |
| EASY retain-reuse | C++ | 57–61 | 0.00 | – | – | – | 6.0–12.8 |
| EASY retain-reuse | C# | 57–59 | 0.00 | – | – | – | 7.6–9.8 |
| HARD retain-reuse, 64 batches | C++ | 64–69 | 0.00 | – | – | – | 4.3–17.4 |
| HARD retain-reuse, 64 batches | C# | 48–52 | 0.00 | – | – | – | 4.6–13.3 |
| HARD retain-allocate, 64 batches | C++ | 61–67 | 0.00 | – | – | – | 4.7–12.3 |
| HARD retain-allocate, 64 batches | C# | 46–51 | 0.00 | – | – | – | 4.7–6.3 |
| HARD aggregate | C++ | 83–88 | 0.00 | – | – | – | 7.1–18.7 |
| HARD aggregate | C# | 56–63 | 0.00 | – | – | – | 5.5–15.7 |
| HARD content at EASY settings: retain-reuse | C++ | 60–62 | 0.00 | – | – | – | 6.6–14.7 |
| HARD content at EASY settings: retain-reuse | C# | 44–46 | 0.00 | – | – | – | 6.5–16.2 |
| HARD content at EASY settings: aggregate | C++ | 79–80 | 0.00 | – | – | – | 6.4–15.6 |
| HARD content at EASY settings: aggregate | C# | 53–56 | 0.00 | – | – | – | 6.3–13.1 |
| HARD retain-allocate, 8 batches retained | C++ | 66–68 | 0.00 | – | – | – | 9.8–19.1 |
| HARD retain-allocate, 8 batches retained | C# | 50–52 | 0.00 | – | – | – | 8.3–15.6 |
| EASY retain-reuse | C++ | 65–70 | 0.01–0.66 | 0 | 0–15 | 5/7 | 5.4–38.5 |
| EASY retain-reuse | C# | 65–70 | 0.01–4.41 | 0–1 | 0–164 | 5/7 | 5.6–66.9 |
| EASY aggregate | C++ | 75–77 | 0.03–2.56 | 0 | 0–174 | 5/7 | 6.0–19.7 |
| EASY aggregate | C# | 74–78 | 0.03–0.63 | 0 | 0–32 | 6/7 | 7.0–35.4 |
| HARD retain-allocate | C++ | 67–69 | 1.10–1.40 | 1–2 | 0 | 5/5 | 5.6–10.8 |
| HARD retain-allocate | C# | 67–68 | 1.27–1.63 | 1 | 0–26 | 2/5 | 5.7–11.6 |
| HARD retain-reuse | C++ | 68–69 | 1.23–1.58 | 1–2 | 0–5 | 4/5 | 6.2–12.1 |
| HARD retain-reuse | C# | 67–69 | 1.16–1.65 | 1 | 0–6 | 4/5 | 5.9–16.3 |
| HARD retain-allocate | C++ | 68–69 | 4.92–7.18 | 0 | 0–5 | 2/5 | 8.7–15.2 |
| HARD retain-allocate | C# | 66–67 | 1.33–1.80 | 0 | 0–1 | 4/5 | 10.1–13.0 |
| HARD retain-reuse | C++ | 69–70 | 5.67–7.93 | 0 | 0–97 | 2/5 | 8.9–11.7 |
| HARD retain-reuse | C# | 66–67 | 1.20–1.66 | 0 | 0–24 | 2/5 | 9.5–21.3 |
| HARD retain-allocate | C++ | 90 | 4.83–9.80 | 0 | 0 | 3/3 | 12.7–19.2 |
| HARD retain-allocate | C# | 79–82 | 2.38–6.55 | 0 | 0 | 3/3 | 12.7–14.0 |
| HARD retain-allocate, saturation (C# DATAS on) | C++ | 62–63 | 0.00 | – | – | – | 10.8–12.7 |
| HARD retain-allocate, saturation (C# DATAS on) | C# | 44–46 | 0.00 | – | – | – | 10.3–12.9 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C++ | 67 | 2.28–2.43 | 1 | 0 | 3/3 | 17.3–18.3 |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | C# | 67–68 | 3.24–3.38 | 0 | 0–23 | 2/3 | 15.3–16.0 |
| HARD frames, 4 KiB–1 MiB | C++ | 99 | 0.00 | – | – | – | 18.2–19.1 |
| HARD frames, 4 KiB–1 MiB | C# | 99 | 0.00 | – | – | – | 19.3–20.1 |
| EASY frames, 64 KiB | C++ | 98 | 0.00 | – | – | – | 18.3–21.0 |
| EASY frames, 64 KiB | C# | 98 | 0.00 | – | – | – | 17.1–17.8 |
| EASY aggregate, 4 client cores | C++ | 50–51 | 0.00 | – | – | – | 10.2–10.8 |
| EASY aggregate, 4 client cores | C# | 48–49 | 0.00 | – | – | – | 11.1–15.2 |
| HARD retain-reuse, 4 client cores | C++ | 47–49 | 0.00 | – | – | – | 10.7–18.1 |
| HARD retain-reuse, 4 client cores | C# | 34–37 | 0.00 | – | – | – | 10.6–21.6 |
| EASY aggregate, C# client | C++ | 76–77 | 0.00 | – | – | – | 9.1–10.7 |
| EASY aggregate, C# client | C# | 72–74 | 0.00 | – | – | – | 10.9–12.6 |
| HARD retain-reuse, C# client | C++ | 65–66 | 0.00 | – | – | – | 12.1–13.8 |
| HARD retain-reuse, C# client | C# | 50–51 | 0.00 | – | – | – | 9.7–11.4 |
| Mixed 64 KiB aggregate | C++ | 67–69 | 0.00 | – | – | – | 8.4–11.7 |
| Mixed 64 KiB aggregate | C# | 52–54 | 0.00 | – | – | – | 8.3–10.5 |
| Mixed 64 KiB retain-allocate | C++ | 53 | 0.00 | – | – | – | 8.1–9.3 |
| Mixed 64 KiB retain-allocate | C# | 43–44 | 0.00 | – | – | – | 8.7–10.7 |
| Mixed 64 KiB retain-reuse | C++ | 54–56 | 0.00 | – | – | – | 8.4–9.4 |
| Mixed 64 KiB retain-reuse | C# | 43 | 0.00 | – | – | – | 9.2–10.4 |
| EASY aggregate, every socket operation capped at 4 KiB | C++ | 96–97 | 0.00 | – | – | – | 17.8–18.5 |
| EASY aggregate, every socket operation capped at 4 KiB | C# | 98–99 | 0.00 | – | – | – | 16.5–17.4 |

## Paired C# / C++ ratios

Per-repetition ratios, median across eligible pairs; bootstrap 95% interval over pairs from five pairs up (rough). Throughput ratios are shown only where throughput is not fixed by the offered rate; paced cells compare p99 latency instead (above 1 means C# was slower).

| Workload | Metric | Pairs | Median ratio | Bootstrap interval |
| --- | --- | ---: | ---: | --- |
| EASY aggregate | records/s | 3 | 0.988 | – |
| EASY retain-reuse | records/s | 3 | 1.018 | – |
| HARD mixed frames, retain-allocate | records/s | 3 | 0.731 | – |
| HARD content, 64 KiB frames, aggregate | records/s | 3 | 0.674 | – |
| HARD mixed frames, retain-reuse | records/s | 3 | 0.749 | – |
| HARD mixed frames, aggregate | records/s | 3 | 0.679 | – |
| EASY aggregate | records/s | 7 | 0.951 | 0.934–0.969 |
| EASY retain-reuse | records/s | 7 | 0.985 | 0.975–1.000 |
| HARD retain-reuse, 64 batches | records/s | 5 | 0.757 | 0.738–0.815 |
| HARD retain-allocate, 64 batches | records/s | 5 | 0.767 | 0.756–0.780 |
| HARD aggregate | records/s | 3 | 0.702 | – |
| HARD content at EASY settings: retain-reuse | records/s | 5 | 0.724 | 0.699–0.742 |
| HARD content at EASY settings: aggregate | records/s | 5 | 0.694 | 0.668–0.718 |
| HARD retain-allocate, 8 batches retained | records/s | 5 | 0.754 | 0.726–0.791 |
| EASY retain-reuse | p99 latency | 7 | 2.267 | 1.372–∞ (C# miss) |
| EASY aggregate | p99 latency | 7 | 1.113 | 0.510–1.972 |
| HARD retain-allocate | p99 latency | 5 | 3.102 | 2.759–3.479 |
| HARD retain-reuse | p99 latency | 5 | 1.682 | 1.555–3.415 |
| HARD retain-allocate, saturation (C# DATAS on) | records/s | 3 | 0.694 | – |
| HARD retain-allocate, Poisson at 70% (C# DATAS on) | p99 latency | 3 | ∞ (C# miss) | – |
| HARD frames, 4 KiB–1 MiB | MiB/s (client-bound) | 3 | 0.996 | – |
| EASY frames, 64 KiB | MiB/s (client-bound) | 3 | 1.104 | – |
| EASY aggregate, 4 client cores | records/s | 3 | 0.954 | – |
| HARD retain-reuse, 4 client cores | records/s | 3 | 0.771 | – |
| EASY aggregate, C# client | records/s | 3 | 0.943 | – |
| HARD retain-reuse, C# client | records/s | 3 | 0.770 | – |
| Mixed 64 KiB aggregate | records/s | 3 | 0.779 | – |
| Mixed 64 KiB retain-allocate | records/s | 3 | 0.796 | – |
| Mixed 64 KiB retain-reuse | records/s | 3 | 0.801 | – |

## Excluded or failed trials

| Trial | Reasons | Runner error |
| --- | --- | --- |
| F:\SocketServerClient\results\final-campaign-20260929\0075-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0076-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\final-campaign-20260929\0093-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0094-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\final-campaign-20260929\0193-hard-allocate-overload-csharp | partner_excluded |  |
| F:\SocketServerClient\results\final-campaign-20260929\0194-hard-allocate-overload-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0195-hard-allocate-overload-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0196-hard-allocate-overload-csharp | partner_excluded |  |
| F:\SocketServerClient\results\final-campaign-20260929\0197-hard-allocate-overload-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0198-hard-allocate-overload-csharp | partner_excluded |  |
| F:\SocketServerClient\results\final-campaign-20260929\0265-stress-fragmented-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0266-stress-fragmented-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0267-stress-fragmented-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0268-stress-fragmented-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0269-stress-fragmented-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\final-campaign-20260929\0270-stress-fragmented-csharp | client_cpu_over_85pct |  |
