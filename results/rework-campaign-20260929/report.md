# Campaign report

148 of 158 trials eligible (both members of a repetition pair must pass). Throughput: median across eligible trials. Latency: lowest–highest per-trial percentile over offered requests from intended arrival; "miss" means the rank falls on rejected, failed or unresolved demand. Percentiles are never averaged. MiB = 1,048,576 bytes.

## Processing-only controls (one core, no sockets) (processing only)

| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |
| --- | --- | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 3/3 | 4.393 | 686 | 4.375–4.417 |
| EASY aggregate | C# | 3/3 | 3.805 | 594 | 3.782–3.817 |
| EASY retain-reuse | C++ | 3/3 | 3.238 | 506 | 3.221–3.248 |
| EASY retain-reuse | C# | 3/3 | 3.032 | 474 | 3.031–3.046 |
| HARD mixed frames, retain-allocate | C++ | 3/3 | 0.734 | 536 | 0.727–0.734 |
| HARD mixed frames, retain-allocate | C# | 3/3 | 0.530 | 387 | 0.529–0.532 |
| HARD content, 64 KiB frames, aggregate | C++ | 3/3 | 1.023 | 747 | 1.021–1.027 |
| HARD content, 64 KiB frames, aggregate | C# | 3/3 | 0.690 | 504 | 0.690–0.697 |
| HARD mixed frames, retain-reuse | C++ | 3/3 | 0.742 | 542 | 0.740–0.742 |
| HARD mixed frames, retain-reuse | C# | 3/3 | 0.553 | 404 | 0.550–0.555 |
| HARD mixed frames, aggregate | C++ | 3/3 | 1.023 | 748 | 1.022–1.024 |
| HARD mixed frames, aggregate | C# | 3/3 | 0.696 | 509 | 0.693–0.699 |

## EASY — closed-loop saturation (16 connections × 4 outstanding)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 7/7 | 14.649 | 2,288 | 1.69–1.72 | 1.94–2.37 | 1,079,231–1,119,806 | 0 | 3.96 | 108.3 | 15 |
| EASY aggregate | C# | 7/7 | 12.245 | 1,913 | 1.88–1.91 | 3.92–5.28 | 909,449–954,951 | 0 | 3.80 | 123.5 | 58–62 |
| EASY retain-reuse | C++ | 7/7 | 11.162 | 1,743 | 2.22–2.35 | 2.49–4.13 | 770,812–852,170 | 0 | 3.96 | 141.4 | 21 |
| EASY retain-reuse | C# | 7/7 | 10.286 | 1,607 | 2.31–2.33 | 4.37–5.83 | 749,308–783,271 | 0 | 3.85 | 150.2 | 77–82 |

## HARD — closed-loop saturation (16 × 4, 4 KiB–1 MiB frames)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-reuse, 64 batches | C++ | 5/5 | 2.566 | 1,875 | 5.83–5.91 | 8.52–8.95 | 631,346–643,332 | 0 | 3.95 | 374.9 | 876–890 |
| HARD retain-reuse, 64 batches | C# | 5/5 | 1.921 | 1,404 | 7.78–7.88 | 13.66–14.42 | 470,172–475,866 | 0 | 3.87 | 492.6 | 1047–1058 |
| HARD retain-allocate, 64 batches | C++ | 5/5 | 2.440 | 1,783 | 6.05–6.19 | 8.95–10.03 | 598,736–620,328 | 0 | 3.93 | 391.3 | 242–245 |
| HARD retain-allocate, 64 batches | C# | 5/5 | 1.742 | 1,273 | 7.96–8.37 | 20.71–31.52 | 419,000–447,430 | 0 | 3.70 | 512.5 | 410–764 |
| HARD aggregate | C++ | 0/5 | – | – | – | – | – | – | – | – | – |
| HARD aggregate | C# | 0/5 | – | – | – | – | – | – | – | – | – |

## Controlled comparisons (one variable changed from EASY or HARD)

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD content at EASY settings: retain-reuse | C++ | 5/5 | 2.539 | 1,855 | 2.06–2.09 | 2.40–2.67 | 898,290–915,854 | 0 | 3.96 | 130.9 | 26–27 |
| HARD content at EASY settings: retain-reuse | C# | 5/5 | 1.871 | 1,367 | 2.63–2.72 | 4.93–6.08 | 651,517–680,409 | 0 | 3.81 | 171.7 | 81–85 |
| HARD content at EASY settings: aggregate | C++ | 5/5 | 3.371 | 2,463 | 1.56–1.58 | 1.78–2.07 | 1,182,880–1,214,953 | 0 | 3.97 | 99.3 | 16 |
| HARD content at EASY settings: aggregate | C# | 5/5 | 2.313 | 1,690 | 2.14–2.20 | 4.13–5.14 | 805,661–854,663 | 0 | 3.82 | 138.8 | 59–62 |
| HARD retain-allocate, 8 batches retained | C++ | 5/5 | 2.503 | 1,829 | 5.98–6.14 | 8.78–9.40 | 607,723–628,337 | 0 | 3.95 | 383.2 | 87–89 |
| HARD retain-allocate, 8 batches retained | C# | 5/5 | 1.738 | 1,270 | 7.80–8.14 | 17.50–30.93 | 412,656–455,022 | 0 | 3.68 | 514.3 | 139–194 |

## EASY — steady arrivals at 50% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY retain-reuse | C++ | 7/7 | 5.143 | 803 | 0.19 | 0.29–0.33 | 385,837–386,052 | 0–215 | 1.94 | 151.1 | 21 |
| EASY retain-reuse | C# | 7/7 | 5.140 | 803 | 0.20 | 0.31–0.39 | 385,670–386,052 | 0–382 | 2.23 | 174.3 | 82 |
| EASY aggregate | C++ | 7/7 | 6.123 | 956 | 0.16 | 0.25–0.39 | 459,497–459,606 | 0–109 | 1.79 | 117.1 | 15 |
| EASY aggregate | C# | 7/7 | 6.118 | 956 | 0.17 | 0.28–0.66 | 458,810–459,606 | 0–796 | 2.28 | 149.4 | 57–58 |

## HARD — Poisson arrivals at 70% of the slower server's capacity

| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD retain-allocate | C++ | 2/2 | 1.219 | 891 | 0.48–0.49 | 2.75–3.17 | 301,468–301,616 | 0–148 | 1.89 | 378.3 | 229–230 |
| HARD retain-allocate | C# | 2/2 | 1.218 | 890 | 0.75 | 10.85–12.91 | 301,104–301,414 | 202–512 | 2.56 | 510.9 | 388–392 |
| HARD retain-reuse | C++ | 1/1 | 1.345 | 983 | 0.49 | 2.78 | 332,539 | 0 | 1.99 | 360.0 | 881 |
| HARD retain-reuse | C# | 1/1 | 1.345 | 983 | 0.67 | 4.02 | 332,539 | 0 | 2.73 | 494.8 | 1075 |

## Memory, allocation and GC (server measured interval)

Allocated per record: C# managed bytes (GC.GetTotalAllocatedBytes); C++ global operator new bytes. Owned capacity: retained plus scratch row/text storage per connection. GC counts are collections in the interval, not independent categories.

| Workload | Server | Allocated B/record | Allocations/frame | GC gen0 / gen1 / gen2 | GC pause ms | Owned capacity MiB |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.1 | – | 2 / 0 / 0 | 0.8–1.0 | 0.0 |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.1 | – | 0 / 0 / 0 | 0.0 | 0.4 |
| HARD retain-reuse, 64 batches | C++ | 0.2 | 0.0 | – | – | 60.2 |
| HARD retain-reuse, 64 batches | C# | 0.6–0.7 | – | 0 / 0 / 0 | 0.0 | 60.2 |
| HARD retain-allocate, 64 batches | C++ | 1326.1 | 10.3 | – | – | 15.6 |
| HARD retain-allocate, 64 batches | C# | 1291.2–1326.7 | – | 1,890–2,330 / 1,000–1,314 / 901–1,218 | 1423.6–1720.2 | 15.6 |
| HARD content at EASY settings: retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.7 |
| HARD content at EASY settings: retain-reuse | C# | 0.3 | – | 1 / 0 / 0 | 0.8–0.9 | 0.7 |
| HARD content at EASY settings: aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| HARD content at EASY settings: aggregate | C# | 0.3 | – | 2 / 0 / 0 | 0.7–0.8 | 0.0 |
| HARD retain-allocate, 8 batches retained | C++ | 1326.1–1326.2 | 10.3 | – | – | 3.8 |
| HARD retain-allocate, 8 batches retained | C# | 939.6–1326.8 | – | 3,866–6,581 / 3,848–6,581 / 2,809–5,375 | 1391.5–2005.9 | 3.8 |
| EASY retain-reuse | C++ | 0.0 | 0.0 | – | – | 0.4 |
| EASY retain-reuse | C# | 0.4–0.5 | – | 2–3 / 0 / 0 | 0.5–0.8 | 0.4 |
| EASY aggregate | C++ | 0.0 | 0.0 | – | – | 0.0 |
| EASY aggregate | C# | 0.4 | – | 4–5 / 0 / 0 | 1.1–1.4 | 0.0 |
| HARD retain-allocate | C++ | 1326.0 | 10.3 | – | – | 15.6–15.8 |
| HARD retain-allocate | C# | 1327.0–1327.1 | – | 1,579–1,585 / 893–896 / 829–832 | 1131.0–1161.4 | 16.1–16.6 |
| HARD retain-reuse | C++ | 0.9 | 0.0 | – | – | 60.2 |
| HARD retain-reuse | C# | 1.7 | – | 1 / 0 / 0 | 0.4 | 60.0 |

## Load-generator and environment checks

Client CPU is the measured-phase share of the client's cores. Lateness is generator dispatch minus intended time (scheduled only). Strict v1 is the first-pass rule (no arrival over 1 ms late), reported but not used. Sibling busy is foreign CPU on the idle SMT siblings of the server's cores.

| Workload | Server | Client CPU % | Client delay p99 ms | Lateness p99 µs | Late >1 ms | Strict v1 pass | Sibling busy % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EASY aggregate | C++ | 75–80 | 0.00 | – | – | – | 6.3–11.2 |
| EASY aggregate | C# | 63–68 | 0.00 | – | – | – | 6.0–12.1 |
| EASY retain-reuse | C++ | 57–62 | 0.00 | – | – | – | 5.7–19.4 |
| EASY retain-reuse | C# | 53–56 | 0.00 | – | – | – | 4.0–10.7 |
| HARD retain-reuse, 64 batches | C++ | 66–67 | 0.00 | – | – | – | 7.3–12.7 |
| HARD retain-reuse, 64 batches | C# | 50–51 | 0.00 | – | – | – | 7.7–10.1 |
| HARD retain-allocate, 64 batches | C++ | 65–66 | 0.00 | – | – | – | 7.9–16.4 |
| HARD retain-allocate, 64 batches | C# | 47–49 | 0.00 | – | – | – | 6.4–14.4 |
| HARD aggregate | C++ | 86–90 | 0.00 | – | – | – | 8.9–16.1 |
| HARD aggregate | C# | 61–62 | 0.00 | – | – | – | 7.0–14.9 |
| HARD content at EASY settings: retain-reuse | C++ | 61–64 | 0.00 | – | – | – | 6.9–10.8 |
| HARD content at EASY settings: retain-reuse | C# | 45–48 | 0.00 | – | – | – | 7.4–10.9 |
| HARD content at EASY settings: aggregate | C++ | 79–82 | 0.00 | – | – | – | 4.4–10.2 |
| HARD content at EASY settings: aggregate | C# | 55–57 | 0.00 | – | – | – | 3.4–9.4 |
| HARD retain-allocate, 8 batches retained | C++ | 65–67 | 0.00 | – | – | – | 6.2–12.7 |
| HARD retain-allocate, 8 batches retained | C# | 43–49 | 0.00 | – | – | – | 4.9–9.2 |
| EASY retain-reuse | C++ | 63–64 | 0.01–0.02 | 0 | 0 | 7/7 | 3.7–6.7 |
| EASY retain-reuse | C# | 63–64 | 0.01–0.02 | 0 | 0–4 | 5/7 | 3.3–7.8 |
| EASY aggregate | C++ | 69–70 | 0.01–0.09 | 0 | 0 | 7/7 | 3.2–8.4 |
| EASY aggregate | C# | 69–70 | 0.01–0.05 | 0 | 0 | 7/7 | 3.6–6.6 |
| HARD retain-allocate | C++ | 64 | 0.95–1.30 | 1 | 0 | 2/2 | 5.3–9.3 |
| HARD retain-allocate | C# | 65 | 1.06–1.17 | 1 | 0–1 | 1/2 | 6.6–8.4 |
| HARD retain-reuse | C++ | 67 | 1.13 | 1 | 0 | 1/1 | 6.0 |
| HARD retain-reuse | C# | 66 | 1.03 | 1 | 0 | 1/1 | 5.0 |

## Paired C# / C++ ratios

Per-repetition ratios, median across eligible pairs; bootstrap 95% interval over pairs from five pairs up (rough). Throughput ratios are shown only where throughput is not fixed by the offered rate; paced cells compare p99 latency instead (above 1 means C# was slower).

| Workload | Metric | Pairs | Median ratio | Bootstrap interval |
| --- | --- | ---: | ---: | --- |
| EASY aggregate | records/s | 3 | 0.864 | – |
| EASY retain-reuse | records/s | 3 | 0.938 | – |
| HARD mixed frames, retain-allocate | records/s | 3 | 0.726 | – |
| HARD content, 64 KiB frames, aggregate | records/s | 3 | 0.676 | – |
| HARD mixed frames, retain-reuse | records/s | 3 | 0.745 | – |
| HARD mixed frames, aggregate | records/s | 3 | 0.681 | – |
| EASY aggregate | records/s | 7 | 0.850 | 0.829–0.858 |
| EASY retain-reuse | records/s | 7 | 0.919 | 0.899–0.933 |
| HARD retain-reuse, 64 batches | records/s | 5 | 0.744 | 0.739–0.752 |
| HARD retain-allocate, 64 batches | records/s | 5 | 0.715 | 0.700–0.734 |
| HARD content at EASY settings: retain-reuse | records/s | 5 | 0.735 | 0.725–0.743 |
| HARD content at EASY settings: aggregate | records/s | 5 | 0.681 | 0.678–0.710 |
| HARD retain-allocate, 8 batches retained | records/s | 5 | 0.684 | 0.670–0.729 |
| EASY retain-reuse | p99 latency | 7 | 1.108 | 1.055–1.178 |
| EASY aggregate | p99 latency | 7 | 1.169 | 1.076–1.542 |
| HARD retain-allocate | p99 latency | 2 | 4.006 | – |
| HARD retain-reuse | p99 latency | 1 | 1.448 | – |

## Excluded or failed trials

| Trial | Reasons | Runner error |
| --- | --- | --- |
| F:\SocketServerClient\results\rework-campaign-20260929\0069-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0070-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0075-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0076-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0081-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0082-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0083-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0084-hard-aggregate-sat-csharp | partner_excluded |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0093-hard-aggregate-sat-cpp | client_cpu_over_85pct |  |
| F:\SocketServerClient\results\rework-campaign-20260929\0094-hard-aggregate-sat-csharp | partner_excluded |  |
