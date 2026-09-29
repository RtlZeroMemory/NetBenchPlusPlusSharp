# C# optimization diagnostics (29 September 2026)

These are diagnostics, not campaign evidence. The binaries for every variant are in `bin/` and the source snapshots are `Processing-v*.cs`. Ablation builds were patched temporarily and never committed; their oracle check was disabled.

## Processing only (one pinned core, fresh interleaved processes, 5 s warmup, 10 s measured)

Values are millions of records per second, one per round (`ab_process.py`).

| Corpus / mode | base (frozen) | v1 names/kind in place | v2 + messages in place | C++ |
| --- | --- | --- | --- | --- |
| EASY 64 KiB aggregate | 3.818, 3.848, 3.862 | 4.248, 4.235, 4.301 | 4.392, 4.345, 4.517 | 4.393 (campaign) |
| HARD 64 KiB aggregate | 0.590, 0.595, 0.692 | 0.688, 0.708, 0.698 | 0.692, 0.693, 0.694 | 1.023 (campaign) |
| HARD mixed retain-reuse | 0.564, 0.549, 0.550 | 0.555, 0.554, 0.554 | 0.554, 0.560, 0.542 | 0.742 (campaign) |

Ablations on HARD 64 KiB aggregate (`ab-ablation-*.log`):

- C# full 0.698 M/s. Without message hashing: 0.875 M/s.
- `Read()` tokens only: about 1,180 MiB/s. Tokens plus `CopyString` of every string: about 680 MiB/s.
- Escaped messages not decoded (upper bound for a faster unescaper): 0.82 M/s.
- C++ full 1.018 M/s; without message hashing 1.493 M/s.

Per 766-byte record, the digest costs about 0.3 µs in both languages. Excluding it, parsing takes 1.14 µs in C# against 0.67 µs in C++.

Rejected variants:

- Hand-written unescaper v4: 0.597–0.617 vs 0.680–0.690 for `CopyString`.
- Indexed unescaper v5: 0.557–0.648 vs 0.687–0.692.
- `DOTNET_PreferredVectorBitWidth=512`: HARD 0.659–0.696 vs 0.678–0.687; EASY 4.076–4.378 vs 4.315–4.335. No consistent effect.

## Over TCP: HARD retain-allocate saturation (C++ client, 30 s measured, 20 s warmup, interleaved)

`ab_tcp.py`; raw trials in `ab-tcp/`.

| C# server | M records/s | p99 ms | GC gen0/gen1/gen2 | GC pause total | Peak working set |
| --- | --- | --- | --- | --- | --- |
| base | 1.733–1.825 | 19.99–21.76 | ~1,150/650/600 | 0.82–0.86 s | 393–426 MiB |
| v2 | 1.715–1.824 | 20.19–22.94 | ~1,140/645/595 | 0.84–0.87 s | 397–409 MiB |
| v2, DATAS off | 1.659–1.780 | 24.51–25.69 | ~370/370/345 | 0.18 s | 593–610 MiB |
| v3 chunked | 1.674–1.749 | 19.01–20.51 | 425–771/189–203/56–87 | 1.61–1.81 s | 1,182–1,293 MiB |
| v3 chunked, DATAS off | 1.792–1.892 | 15.70–17.24 | ~55/55/0 | 0.40 s | 1,014–1,021 MiB |

C++ server, same cell:

| C++ server | M records/s | p99 ms | Allocated B/record | Peak working set |
| --- | --- | --- | --- | --- |
| pre-chunk | 2.447–2.506 | 8.91–9.34 | 1,326 | 240–242 MiB |
| chunked | 2.436–2.467 | 9.11–9.27 | 829 | 198–202 MiB |

Adopted for the final campaign: v2 parsing, chunked storage in both languages, and C# DATAS off, with a runtime-default phase in the campaign.
