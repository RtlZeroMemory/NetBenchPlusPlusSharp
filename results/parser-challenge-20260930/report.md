# Campaign report

64 of 98 trials eligible (both members of a repetition pair must pass). Throughput: median across eligible trials. Latency: lowest–highest per-trial percentile over offered requests from intended arrival; "miss" means the rank falls on rejected, failed or unresolved demand. Percentiles are never averaged. MiB = 1,048,576 bytes.

## Processing only (one core, no sockets): C# FastJson vs C++ simdjson (processing only)

| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |
| --- | --- | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 3/3 | 4.426 | 691 | 4.402–4.427 |
| EASY aggregate | C# | 3/3 | 9.583 | 1,497 | 9.541–9.641 |
| HARD mixed frames, retain-reuse | C++ | 3/3 | 0.743 | 543 | 0.739–0.743 |
| HARD mixed frames, retain-reuse | C# | 3/3 | 0.876 | 641 | 0.868–0.880 |
| HARD content, 64 KiB frames, aggregate | C++ | 3/3 | 1.021 | 746 | 1.021–1.022 |
| HARD content, 64 KiB frames, aggregate | C# | 3/3 | 1.316 | 961 | 1.313–1.316 |
| EASY retain-reuse | C++ | 3/3 | 3.245 | 507 | 3.242–3.253 |
| EASY retain-reuse | C# | 3/3 | 5.568 | 870 | 5.566–5.580 |
| HARD mixed frames, retain-allocate | C++ | 3/3 | 0.735 | 537 | 0.732–0.737 |
| HARD mixed frames, retain-allocate | C# | 3/3 | 0.853 | 623 | 0.852–0.859 |
| HARD mixed frames, aggregate | C++ | 3/3 | 1.024 | 748 | 1.017–1.025 |
| HARD mixed frames, aggregate | C# | 3/3 | 1.314 | 961 | 1.312–1.315 |

## EASY, closed-loop saturation (16 connections x 4 outstanding)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse | C++ | 1/5 | 11.102 | 1,734 | 2.28 | 2.66 | 833,487 | 0 | 3.95 | 142.6 | 22 |
| EASY retain-reuse | C# | 1/5 | 16.792 | 2,623 | 1.39 | 3.43 | 1,260,605 | 0 | 3.76 | 89.8 | 96 |

## EASY saturation with a fourth load-generator core

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate, 4 client cores | C++ | 0/3 | – | – | – | – | – | – | – | – | – |
| EASY aggregate, 4 client cores | C# | 0/3 | – | – | – | – | – | – | – | – | – |
| EASY retain-reuse, 4 client cores | C++ | 3/3 | 11.218 | 1,752 | 2.25–2.27 | 2.53–2.74 | 833,129–843,472 | 0 | 3.97 | 141.9 | 22 |
| EASY retain-reuse, 4 client cores | C# | 3/3 | 16.943 | 2,646 | 1.38–1.41 | 2.43–3.38 | 1,254,939–1,288,103 | 0 | 3.81 | 89.9 | 97 |

## HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD retain-allocate, 64 batches | C++ | 5/5 | 2.488 | 1,819 | 6.08–6.31 | 8.91–9.31 | 593,507–615,793 | 0 | 3.97 | 388.2 | 199–201 |
| HARD retain-allocate, 64 batches | C# | 5/5 | 2.820 | 2,061 | 4.85–5.39 | 12.45–14.42 | 661,366–702,052 | 0 | 3.88 | 333.0 | 1017–1025 |
| HARD retain-reuse, 64 batches | C++ | 5/5 | 2.544 | 1,859 | 5.93–5.96 | 8.65–8.72 | 628,364–631,058 | 0 | 3.97 | 379.4 | 702–703 |
| HARD retain-reuse, 64 batches | C# | 5/5 | 2.898 | 2,118 | 5.00–5.05 | 8.81–9.18 | 713,374–723,008 | 0 | 3.90 | 326.8 | 876–883 |

## Memory, allocation and GC (server measured interval)

Allocated per record: C# managed bytes (GC.GetTotalAllocatedBytes); C++ global operator new bytes. Owned capacity: retained plus scratch row/text storage per connection. GC counts are collections in the interval, not independent categories.

| Workload | Server | Allocated B/record | Allocations/frame | GC gen0 / gen1 / gen2 | GC pause ms | Owned capacity MiB |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| **EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | | | |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| **EASY saturation with a fourth load-generator core** | | | | | | |
| EASY retain-reuse, 4 client cores | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse, 4 client cores | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| **HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | |
| HARD retain-allocate, 64 batches | C++ | 828.8–828.9 | 14.0 | – | – | 12.6 |
| HARD retain-allocate, 64 batches | C# | 829.5–829.6 | – | 161–171 / 161–171 / 0 | 1217.3–1241.4 | 12.6 |
| HARD retain-reuse, 64 batches | C++ | 0.0 | 0.0 | – | – | 42.7 |
| HARD retain-reuse, 64 batches | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 42.7 |

## Load-generator and environment checks

Client CPU is the measured-phase share of the client's cores. Lateness is generator dispatch minus intended time (scheduled only). Strict v1 is the first-pass rule (no arrival over 1 ms late), reported but not used. Sibling busy is foreign CPU on the idle SMT siblings of the server's cores.

| Workload | Server | Client CPU % | Client delay p99 ms | Lateness p99 µs | Late >1 ms | Strict v1 pass | Sibling busy % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | | | | |
| EASY aggregate | C++ | 73–76 | 0.00 | – | – | – | 7.3–9.0 |
| EASY aggregate | C# | 98–99 | 0.00 | – | – | – | 7.3–12.2 |
| EASY retain-reuse | C++ | 57–59 | 0.00 | – | – | – | 8.6–12.0 |
| EASY retain-reuse | C# | 84–89 | 0.00 | – | – | – | 8.0–9.6 |
| **EASY saturation with a fourth load-generator core** | | | | | | | |
| EASY aggregate, 4 client cores | C++ | 56–57 | 0.00 | – | – | – | 9.2–12.6 |
| EASY aggregate, 4 client cores | C# | 94–96 | 0.00 | – | – | – | 9.3–13.1 |
| EASY retain-reuse, 4 client cores | C++ | 43–44 | 0.00 | – | – | – | 6.0–7.9 |
| EASY retain-reuse, 4 client cores | C# | 66–67 | 0.00 | – | – | – | 8.8–13.1 |
| **HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | | | | |
| HARD aggregate | C++ | 88–89 | 0.00 | – | – | – | 6.5–8.7 |
| HARD aggregate | C# | 99 | 0.00 | – | – | – | 6.9–8.7 |
| HARD retain-allocate, 64 batches | C++ | 66–69 | 0.00 | – | – | – | 6.2–14.1 |
| HARD retain-allocate, 64 batches | C# | 74–75 | 0.00 | – | – | – | 7.2–17.2 |
| HARD retain-reuse, 64 batches | C++ | 68–70 | 0.00 | – | – | – | 6.4–7.7 |
| HARD retain-reuse, 64 batches | C# | 75–77 | 0.00 | – | – | – | 6.5–9.3 |

## Paired C# / C++ ratios

Per-repetition ratios, median across eligible pairs; bootstrap 95% interval over pairs from five pairs up (rough). Throughput ratios are shown only where throughput is not fixed by the offered rate; paced cells compare p99 latency instead (above 1 means C# was slower).

| Workload | Metric | Pairs | Median ratio | Bootstrap interval |
| --- | --- | ---: | ---: | --- |
| **Processing only (one core, no sockets): C# FastJson vs C++ simdjson** | | | | |
| EASY aggregate | records/s | 3 | 2.177 | – |
| HARD mixed frames, retain-reuse | records/s | 3 | 1.180 | – |
| HARD content, 64 KiB frames, aggregate | records/s | 3 | 1.288 | – |
| EASY retain-reuse | records/s | 3 | 1.716 | – |
| HARD mixed frames, retain-allocate | records/s | 3 | 1.166 | – |
| HARD mixed frames, aggregate | records/s | 3 | 1.285 | – |
| **EASY, closed-loop saturation (16 connections x 4 outstanding)** | | | | |
| EASY retain-reuse | records/s | 1 | 1.513 | – |
| **EASY saturation with a fourth load-generator core** | | | | |
| EASY retain-reuse, 4 client cores | records/s | 3 | 1.527 | – |
| **HARD, closed-loop saturation (16 x 4, 4 KiB-1 MiB frames)** | | | | |
| HARD retain-allocate, 64 batches | records/s | 5 | 1.133 | 1.114–1.143 |
| HARD retain-reuse, 64 batches | records/s | 5 | 1.141 | 1.131–1.149 |

## Excluded or failed trials

| Trial | Reasons | Runner error |
| --- | --- | --- |
| F:\SocketServerClient\results\parser-challenge-20260930\0037-easy-aggregate-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0038-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0039-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0040-easy-reuse-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0041-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0042-easy-reuse-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0043-easy-aggregate-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0044-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0045-easy-reuse-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0046-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0047-easy-aggregate-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0048-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0049-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0050-easy-aggregate-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0053-easy-reuse-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0054-easy-reuse-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0055-easy-aggregate-sat-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0056-easy-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0057-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0058-easy-aggregate-sat-4c-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0063-easy-aggregate-sat-4c-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0064-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0065-easy-aggregate-sat-4c-cpp | partner_excluded |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0066-easy-aggregate-sat-4c-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0069-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0070-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0077-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0078-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0081-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0082-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0089-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0090-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0097-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\parser-challenge-20260930\0098-hard-aggregate-sat-csharp | client_cpu_over_85pct |  |
