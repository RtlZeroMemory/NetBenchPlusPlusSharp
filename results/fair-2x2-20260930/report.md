# Campaign report

96 of 264 trials eligible (both members of a repetition pair must pass). Throughput: median across eligible trials. Latency: lowest–highest per-trial percentile over offered requests from intended arrival; "miss" means the rank falls on rejected, failed or unresolved demand. Percentiles are never averaged. MiB = 1,048,576 bytes.

## Library parsers (System.Text.Json vs simdjson): Processing only (one core, no sockets) (processing only)

| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |
| --- | --- | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 3/3 | 4.359 | 681 | 4.354–4.450 |
| EASY aggregate | C# | 3/3 | 4.224 | 660 | 4.219–4.314 |
| HARD mixed frames, retain-allocate | C++ | 1/3 | 0.731 | 534 | 0.731 |
| HARD mixed frames, retain-allocate | C# | 1/3 | 0.513 | 375 | 0.513 |
| HARD mixed frames, aggregate | C++ | 1/3 | 1.004 | 733 | 1.004 |
| HARD mixed frames, aggregate | C# | 1/3 | 0.658 | 481 | 0.658 |
| HARD mixed frames, retain-reuse | C++ | 1/3 | 0.739 | 540 | 0.739 |
| HARD mixed frames, retain-reuse | C# | 1/3 | 0.518 | 379 | 0.518 |
| HARD content, 64 KiB frames, aggregate | C++ | 2/3 | 1.014 | 741 | 1.012–1.017 |
| HARD content, 64 KiB frames, aggregate | C# | 2/3 | 0.676 | 494 | 0.676–0.677 |
| EASY retain-reuse | C++ | 2/3 | 3.236 | 505 | 3.226–3.245 |
| EASY retain-reuse | C# | 2/3 | 3.200 | 500 | 3.144–3.256 |

## Specialized parsers (FastJson vs its C++ port): Processing only (one core, no sockets) (processing only)

| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |
| --- | --- | ---: | ---: | ---: | ---: |
| EASY retain-reuse | C++ | 1/3 | 5.605 | 875 | 5.605 |
| EASY retain-reuse | C# | 1/3 | 5.508 | 860 | 5.508 |
| HARD content, 64 KiB frames, aggregate | C++ | 1/3 | 1.237 | 904 | 1.237 |
| HARD content, 64 KiB frames, aggregate | C# | 1/3 | 1.302 | 951 | 1.302 |
| EASY aggregate | C++ | 2/3 | 9.102 | 1,422 | 9.012–9.192 |
| EASY aggregate | C# | 2/3 | 8.970 | 1,401 | 8.551–9.388 |
| HARD mixed frames, retain-reuse | C++ | 1/3 | 0.869 | 635 | 0.869 |
| HARD mixed frames, retain-reuse | C# | 1/3 | 0.865 | 632 | 0.865 |
| HARD mixed frames, retain-allocate | C++ | 2/3 | 0.846 | 618 | 0.830–0.862 |
| HARD mixed frames, retain-allocate | C# | 2/3 | 0.843 | 616 | 0.826–0.860 |
| HARD mixed frames, aggregate | C++ | 1/3 | 1.284 | 938 | 1.284 |
| HARD mixed frames, aggregate | C# | 1/3 | 1.299 | 950 | 1.299 |

## Library parsers (System.Text.Json vs simdjson): EASY, closed-loop saturation (16 connections x 4 outstanding)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 2/5 | 14.482 | 2,262 | 1.72–1.73 | 2.15–2.20 | 1,086,898–1,087,384 | 0 | 3.95 | 109.5 | 16 |
| EASY aggregate | C# | 2/5 | 13.209 | 2,063 | 1.75 | 3.92–5.31 | 986,912–996,277 | 0 | 3.75 | 113.9 | 76–79 |
| EASY retain-reuse | C++ | 4/5 | 11.039 | 1,724 | 2.24–2.29 | 2.78–3.06 | 822,276–841,156 | 0 | 3.92 | 142.3 | 22 |
| EASY retain-reuse | C# | 4/5 | 10.848 | 1,694 | 2.16–2.20 | 4.26–5.11 | 802,725–843,428 | 0 | 3.83 | 141.6 | 81–85 |

## Specialized parsers (FastJson vs its C++ port): EASY, closed-loop saturation (16 connections x 4 outstanding)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY retain-reuse | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Specialized parsers (FastJson vs its C++ port): EASY saturation with a fourth load-generator core

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate, 4 client cores | C++ | 0/3 | – | – | – | – | – | – | – | – | – |
| EASY aggregate, 4 client cores | C# | 0/3 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse, 4 client cores | C++ | 3/3 | 17.216 | 2,689 | 1.41–1.45 | 1.90–1.95 | 1,285,675–1,312,545 | 0 | 3.94 | 91.8 | 21 |
| EASY retain-reuse, 4 client cores | C# | 3/3 | 16.684 | 2,606 | 1.38–1.41 | 3.40–3.53 | 1,249,135–1,278,875 | 0 | 3.79 | 91.4 | 97–100 |

## Library parsers (System.Text.Json vs simdjson): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-reuse, 64 batches | C++ | 5/5 | 2.526 | 1,846 | 5.90–6.09 | 8.62–9.18 | 610,842–634,929 | 0 | 3.96 | 380.8 | 702–704 |
| HARD retain-reuse, 64 batches | C# | 5/5 | 1.859 | 1,359 | 8.00–8.49 | 13.93–15.60 | 433,420–463,887 | 0 | 3.87 | 505.0 | 825–868 |
| HARD retain-allocate, 64 batches | C++ | 5/5 | 2.458 | 1,796 | 6.05–6.59 | 8.88–10.26 | 565,342–620,128 | 0 | 3.94 | 390.8 | 199–204 |
| HARD retain-allocate, 64 batches | C# | 5/5 | 1.819 | 1,330 | 7.96–8.21 | 16.15–16.91 | 443,684–455,840 | 0 | 3.86 | 516.6 | 1016–1023 |
| HARD aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Specialized parsers (FastJson vs its C++ port): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate, 64 batches | C++ | 5/5 | 2.858 | 2,089 | 5.14–5.46 | 7.55–9.27 | 655,976–717,801 | 0 | 3.94 | 335.6 | 190–193 |
| HARD retain-allocate, 64 batches | C# | 5/5 | 2.721 | 1,988 | 4.77–5.32 | 12.65–14.78 | 652,496–700,119 | 0 | 3.87 | 342.0 | 1015–1021 |
| HARD retain-reuse, 64 batches | C++ | 5/5 | 2.891 | 2,113 | 4.96–5.13 | 7.19–8.39 | 698,749–743,231 | 0 | 3.89 | 328.2 | 691–692 |
| HARD retain-reuse, 64 batches | C# | 5/5 | 2.910 | 2,127 | 4.96–5.23 | 8.88–10.03 | 674,119–725,766 | 0 | 3.88 | 325.8 | 874–883 |

## Library parsers (System.Text.Json vs simdjson): EASY, steady arrivals at 50% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY retain-reuse | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY aggregate | C++ | 1/5 | 6.240 | 975 | 0.16 | miss | 468,416 | 7,132 | 1.89 | 121.7 | 16 |
| EASY aggregate | C# | 1/5 | 6.335 | 989 | 0.16 | 0.34 | 475,548 | 0 | 2.17 | 137.2 | 112 |

## Specialized parsers (FastJson vs its C++ port): EASY, steady arrivals at 50% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Library parsers (System.Text.Json vs simdjson): HARD, Poisson arrivals at 70% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-reuse | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-reuse | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Specialized parsers (FastJson vs its C++ port): HARD, Poisson arrivals at 70% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-reuse | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-reuse | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Memory, allocation and GC (server measured interval)

Allocated per record: C# managed bytes (GC.GetTotalAllocatedBytes); C++ global operator new bytes. Owned capacity: retained plus scratch row/text storage per connection. GC counts are collections in the interval, not independent categories.

| Workload | Server | Allocated B/record | Allocations/frame | GC gen0 / gen1 / gen2 | GC pause ms | Owned capacity MiB |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| **Library parsers (System.Text.Json vs simdjson): EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | | | |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.0 |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| **Specialized parsers (FastJson vs its C++ port): EASY saturation with a fourth load-generator core** | | | | | | |
| EASY retain-reuse, 4 client cores | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse, 4 client cores | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| **Library parsers (System.Text.Json vs simdjson): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | |
| HARD retain-reuse, 64 batches | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, 64 batches | C# | 0.2 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| HARD retain-allocate, 64 batches | C++ | 828.8–828.9 | 14.0 | – | – | 12.6 |
| HARD retain-allocate, 64 batches | C# | 829.5–829.6 | – | 108–111 / 108–111 / 0 | 784.0–808.6 | 12.6 |
| **Specialized parsers (FastJson vs its C++ port): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | |
| HARD retain-allocate, 64 batches | C++ | 828.8–828.9 | 14.0 | – | – | 12.6 |
| HARD retain-allocate, 64 batches | C# | 829.6 | – | 159–171 / 159–171 / 0 | 1208.2–1265.4 | 12.6 |
| HARD retain-reuse, 64 batches | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, 64 batches | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 42.7 |
| **Library parsers (System.Text.Json vs simdjson): EASY, steady arrivals at 50% of the slower server's capacity** | | | | | | |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.3 | – | 0 / 0 / 0 | 0.0 | 0.0 |

## Load-generator and environment checks

Client CPU is the measured-phase share of the client's cores. Lateness is generator dispatch minus intended time (scheduled only). Strict v1 is the first-pass rule (no arrival over 1 ms late), reported but not used. Sibling busy is foreign CPU on the idle SMT siblings of the server's cores.

| Workload | Server | Client CPU % | Client delay p99 ms | Lateness p99 µs | Late >1 ms | Strict v1 pass | Sibling busy % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Library parsers (System.Text.Json vs simdjson): EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | | | | |
| EASY aggregate | C++ | 55–75 | 0.00 | – | – | – | 13.1–49.9 |
| EASY aggregate | C# | 66–70 | 0.00 | – | – | – | 12.5–29.0 |
| EASY retain-reuse | C++ | 58–61 | 0.00 | – | – | – | 13.9–38.6 |
| EASY retain-reuse | C# | 56–58 | 0.00 | – | – | – | 8.2–29.6 |
| **Specialized parsers (FastJson vs its C++ port): EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | | | | |
| EASY retain-reuse | C++ | 90–91 | 0.00 | – | – | – | 6.3–12.2 |
| EASY retain-reuse | C# | 85–88 | 0.00 | – | – | – | 5.3–11.8 |
| EASY aggregate | C++ | 98–99 | 0.00 | – | – | – | 6.3–9.4 |
| EASY aggregate | C# | 99 | 0.00 | – | – | – | 6.6–15.1 |
| **Specialized parsers (FastJson vs its C++ port): EASY saturation with a fourth load-generator core** | | | | | | | |
| EASY aggregate, 4 client cores | C++ | 96–98 | 0.00 | – | – | – | 9.7–13.6 |
| EASY aggregate, 4 client cores | C# | 93–95 | 0.00 | – | – | – | 8.8–18.9 |
| EASY retain-reuse, 4 client cores | C++ | 69–70 | 0.00 | – | – | – | 11.1–16.5 |
| EASY retain-reuse, 4 client cores | C# | 66–67 | 0.00 | – | – | – | 6.5–16.4 |
| **Library parsers (System.Text.Json vs simdjson): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | | |
| HARD retain-reuse, 64 batches | C++ | 68–71 | 0.00 | – | – | – | 7.7–14.7 |
| HARD retain-reuse, 64 batches | C# | 50–53 | 0.00 | – | – | – | 7.0–15.7 |
| HARD retain-allocate, 64 batches | C++ | 67–68 | 0.00 | – | – | – | 9.0–23.4 |
| HARD retain-allocate, 64 batches | C# | 50–52 | 0.00 | – | – | – | 8.3–13.4 |
| HARD aggregate | C++ | 88–90 | 0.00 | – | – | – | 8.6–17.0 |
| HARD aggregate | C# | 61–63 | 0.00 | – | – | – | 6.3–11.5 |
| **Specialized parsers (FastJson vs its C++ port): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | | |
| HARD aggregate | C++ | 98–99 | 0.00 | – | – | – | 8.7–17.1 |
| HARD aggregate | C# | 97–98 | 0.00 | – | – | – | 7.5–20.7 |
| HARD retain-allocate, 64 batches | C++ | 73–78 | 0.00 | – | – | – | 8.2–19.8 |
| HARD retain-allocate, 64 batches | C# | 69–76 | 0.00 | – | – | – | 8.6–16.5 |
| HARD retain-reuse, 64 batches | C++ | 75–79 | 0.00 | – | – | – | 7.7–18.7 |
| HARD retain-reuse, 64 batches | C# | 73–78 | 0.00 | – | – | – | 8.0–13.3 |
| **Library parsers (System.Text.Json vs simdjson): EASY, steady arrivals at 50% of the slower server's capacity** | | | | | | | |
| EASY retain-reuse | C++ | 40–69 | 0.14–364.90 | 0–6242 | 0–14,215 | 1/5 | 24.7–100.0 |
| EASY retain-reuse | C# | 42–68 | 0.06–294.65 | 0–1196 | 0–4,701 | 1/5 | 16.0–96.3 |
| EASY aggregate | C++ | 39–72 | 0.94–387.97 | 0–2449 | 84–9,605 | 0/5 | 18.6–100.0 |
| EASY aggregate | C# | 39–73 | 0.04–356.52 | 0–1565 | 0–6,247 | 2/5 | 10.4–99.5 |
| **Specialized parsers (FastJson vs its C++ port): EASY, steady arrivals at 50% of the slower server's capacity** | | | | | | | |
| EASY aggregate | C++ | 35–92 | 28.11–750.78 | 8–633 | 13–5,384 | 0/5 | 93.2–99.9 |
| EASY aggregate | C# | 36–86 | 21.10–721.42 | 7–434 | 232–4,439 | 0/5 | 97.2–100.0 |
| EASY retain-reuse | C++ | 37–88 | 27.59–624.95 | 4–15 | 65–1,233 | 0/5 | 87.5–100.0 |
| EASY retain-reuse | C# | 42–87 | 13.43–369.10 | 4–20 | 26–2,098 | 0/5 | 96.3–99.7 |
| **Library parsers (System.Text.Json vs simdjson): HARD, Poisson arrivals at 70% of the slower server's capacity** | | | | | | | |
| HARD retain-reuse | C++ | 40–78 | 41.42–968.88 | 10–684 | 157–2,742 | 0/5 | 97.7–99.1 |
| HARD retain-reuse | C# | 63–75 | 20.45–98.30 | 3–359 | 35–1,514 | 0/5 | 97.1–98.5 |
| HARD retain-allocate | C++ | 57–76 | 51.25–344.98 | 11–981 | 104–3,110 | 0/5 | 95.4–99.5 |
| HARD retain-allocate | C# | 40–66 | 28.84–771.75 | 2–1180 | 42–3,735 | 0/5 | 81.3–99.4 |
| **Specialized parsers (FastJson vs its C++ port): HARD, Poisson arrivals at 70% of the slower server's capacity** | | | | | | | |
| HARD retain-reuse | C++ | 36–68 | 138.94–2122.32 | 79–2982 | 3,412–9,664 | 0/5 | 99.3–100.0 |
| HARD retain-reuse | C# | 36–78 | 66.58–2164.26 | 12–1339 | 700–6,084 | 0/5 | 99.2–100.0 |
| HARD retain-allocate | C++ | 36–59 | 179.83–1862.27 | 58–1241 | 2,646–5,360 | 0/5 | 99.7–100.0 |
| HARD retain-allocate | C# | 36–78 | 48.89–2583.69 | 6–666 | 587–4,110 | 0/5 | 99.0–100.0 |

## Paired C# / C++ ratios

Per-repetition ratios, median across eligible pairs; bootstrap 95% interval over pairs from five pairs up (rough). Throughput ratios are shown only where throughput is not fixed by the offered rate; paced cells compare p99 latency instead (above 1 means C# was slower).

| Workload | Metric | Pairs | Median ratio | Bootstrap interval |
| --- | --- | ---: | ---: | --- |
| **Library parsers (System.Text.Json vs simdjson): Processing only (one core, no sockets)** | | | | |
| EASY aggregate | records/s | 3 | 0.968 | – |
| HARD mixed frames, retain-allocate | records/s | 1 | 0.702 | – |
| HARD mixed frames, aggregate | records/s | 1 | 0.656 | – |
| HARD mixed frames, retain-reuse | records/s | 1 | 0.701 | – |
| HARD content, 64 KiB frames, aggregate | records/s | 2 | 0.667 | – |
| EASY retain-reuse | records/s | 2 | 0.989 | – |
| **Specialized parsers (FastJson vs its C++ port): Processing only (one core, no sockets)** | | | | |
| EASY retain-reuse | records/s | 1 | 0.983 | – |
| HARD content, 64 KiB frames, aggregate | records/s | 1 | 1.052 | – |
| EASY aggregate | records/s | 2 | 0.986 | – |
| HARD mixed frames, retain-reuse | records/s | 1 | 0.995 | – |
| HARD mixed frames, retain-allocate | records/s | 2 | 0.996 | – |
| HARD mixed frames, aggregate | records/s | 1 | 1.012 | – |
| **Library parsers (System.Text.Json vs simdjson): EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | |
| EASY aggregate | records/s | 2 | 0.912 | – |
| EASY retain-reuse | records/s | 4 | 0.984 | – |
| **Specialized parsers (FastJson vs its C++ port): EASY saturation with a fourth load-generator core** | | | | |
| EASY retain-reuse, 4 client cores | records/s | 3 | 0.969 | – |
| **Library parsers (System.Text.Json vs simdjson): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | |
| HARD retain-reuse, 64 batches | records/s | 5 | 0.733 | 0.693–0.742 |
| HARD retain-allocate, 64 batches | records/s | 5 | 0.735 | 0.729–0.798 |
| **Specialized parsers (FastJson vs its C++ port): HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | |
| HARD retain-allocate, 64 batches | records/s | 5 | 0.965 | 0.944–1.000 |
| HARD retain-reuse, 64 batches | records/s | 5 | 0.971 | 0.965–1.028 |
| **Library parsers (System.Text.Json vs simdjson): EASY, steady arrivals at 50% of the slower server's capacity** | | | | |
| EASY aggregate | p99 latency | 1 | 0.000 | – |

## Excluded or failed trials

| Trial | Reasons | Runner error |
| --- | --- | --- |
| F:\SocketServerClient\results\fair-2x2-20260930\0003-library-proc-hardmix-allocate-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0004-library-proc-hardmix-allocate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0005-library-proc-hardmix-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0006-library-proc-hardmix-aggregate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0007-library-proc-hardmix-reuse-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0008-library-proc-hardmix-reuse-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0009-library-proc-hard64k-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0010-library-proc-hard64k-aggregate-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0019-library-proc-hardmix-reuse-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0020-library-proc-hardmix-reuse-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0025-library-proc-hardmix-allocate-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0026-library-proc-hardmix-allocate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0027-library-proc-hardmix-aggregate-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0028-library-proc-hardmix-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0033-library-proc-easy-reuse-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0034-library-proc-easy-reuse-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0037-fast-proc-easy-reuse-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0038-fast-proc-easy-reuse-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0039-fast-proc-hard64k-aggregate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0040-fast-proc-hard64k-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0049-fast-proc-easy-reuse-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0050-fast-proc-easy-reuse-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0053-fast-proc-hardmix-allocate-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0054-fast-proc-hardmix-allocate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0055-fast-proc-hardmix-reuse-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0056-fast-proc-hardmix-reuse-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0057-fast-proc-hardmix-aggregate-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0058-fast-proc-hardmix-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0059-fast-proc-easy-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0060-fast-proc-easy-aggregate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0061-fast-proc-hardmix-aggregate-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0062-fast-proc-hardmix-aggregate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0063-fast-proc-hard64k-aggregate-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0064-fast-proc-hard64k-aggregate-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0065-fast-proc-hardmix-reuse-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0066-fast-proc-hardmix-reuse-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0073-library-easy-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0074-library-easy-aggregate-sat-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0075-library-easy-reuse-sat-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0076-library-easy-reuse-sat-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0077-library-easy-aggregate-sat-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0078-library-easy-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0081-library-easy-aggregate-sat-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0082-library-easy-aggregate-sat-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0093-fast-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0094-fast-easy-reuse-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0095-fast-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0096-fast-easy-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0097-fast-easy-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0098-fast-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0099-fast-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0100-fast-easy-reuse-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0101-fast-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0102-fast-easy-reuse-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0103-fast-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0104-fast-easy-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0105-fast-easy-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0106-fast-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0107-fast-easy-reuse-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0108-fast-easy-reuse-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0109-fast-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0110-fast-easy-reuse-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0111-fast-easy-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0112-fast-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0113-fast-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0114-fast-easy-aggregate-sat-4c-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0117-fast-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0118-fast-easy-aggregate-sat-4c-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0121-fast-easy-aggregate-sat-4c-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0122-fast-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0129-library-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0130-library-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0133-library-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0134-library-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0137-library-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0138-library-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0143-library-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0144-library-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0149-library-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0150-library-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0155-fast-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0156-fast-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0163-fast-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0164-fast-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0169-fast-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0170-fast-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0173-fast-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0174-fast-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0179-fast-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0180-fast-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0185-library-easy-reuse-paced-csharp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0186-library-easy-reuse-paced-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0187-library-easy-aggregate-paced-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0188-library-easy-aggregate-paced-cpp | partner_excluded |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0191-library-easy-reuse-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0192-library-easy-reuse-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0193-library-easy-reuse-paced-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0194-library-easy-reuse-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0195-library-easy-aggregate-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0196-library-easy-aggregate-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0197-library-easy-aggregate-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0198-library-easy-aggregate-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0199-library-easy-reuse-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0200-library-easy-reuse-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0201-library-easy-reuse-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0202-library-easy-reuse-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0203-library-easy-aggregate-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0204-library-easy-aggregate-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0205-fast-easy-aggregate-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0206-fast-easy-aggregate-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0207-fast-easy-reuse-paced-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0208-fast-easy-reuse-paced-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0209-fast-easy-reuse-paced-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0210-fast-easy-reuse-paced-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0211-fast-easy-aggregate-paced-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0212-fast-easy-aggregate-paced-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0213-fast-easy-reuse-paced-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0214-fast-easy-reuse-paced-cpp | client_cpu_over_85pct;background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0215-fast-easy-aggregate-paced-csharp | client_cpu_over_85pct;background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0216-fast-easy-aggregate-paced-cpp | client_cpu_over_85pct;background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0217-fast-easy-reuse-paced-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0218-fast-easy-reuse-paced-csharp | client_cpu_over_85pct;background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0219-fast-easy-aggregate-paced-cpp | client_cpu_over_85pct;background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0220-fast-easy-aggregate-paced-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0221-fast-easy-aggregate-paced-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0222-fast-easy-aggregate-paced-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0223-fast-easy-reuse-paced-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0224-fast-easy-reuse-paced-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0225-library-hard-reuse-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0226-library-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0227-library-hard-allocate-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0228-library-hard-allocate-poisson-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0229-library-hard-allocate-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0230-library-hard-allocate-poisson-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0231-library-hard-reuse-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0232-library-hard-reuse-poisson-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0233-library-hard-allocate-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0234-library-hard-allocate-poisson-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0235-library-hard-reuse-poisson-cpp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0236-library-hard-reuse-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0237-library-hard-reuse-poisson-csharp | background_cpu_over_15pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0238-library-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0239-library-hard-allocate-poisson-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0240-library-hard-allocate-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0241-library-hard-allocate-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0242-library-hard-allocate-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0243-library-hard-reuse-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0244-library-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0245-fast-hard-reuse-poisson-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0246-fast-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0247-fast-hard-allocate-poisson-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0248-fast-hard-allocate-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0249-fast-hard-allocate-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0250-fast-hard-allocate-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0251-fast-hard-reuse-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0252-fast-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0253-fast-hard-reuse-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0254-fast-hard-reuse-poisson-cpp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0255-fast-hard-allocate-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0256-fast-hard-allocate-poisson-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0257-fast-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0258-fast-hard-reuse-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0259-fast-hard-allocate-poisson-csharp | background_cpu_over_15pct;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0260-fast-hard-allocate-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0261-fast-hard-reuse-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0262-fast-hard-reuse-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0263-fast-hard-allocate-poisson-cpp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
| F:\SocketServerClient\results\fair-2x2-20260930\0264-fast-hard-allocate-poisson-csharp | background_cpu_over_15pct;lateness_p99_over_100us;late_over_1ms_above_0.1pct |  |
