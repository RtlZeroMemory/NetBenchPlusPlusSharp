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

## FastJson, the schema-specific parser (30 September 2026)

These are processing-only runs: one pinned core, fresh interleaved processes, 5 s warmup and 10 s measured, in millions of records per second. Every run checks every frame against the Python oracle. `bin/fast1` to `bin/fast3` hold the builds.

| Step | Change | HARD 64 KiB aggregate | EASY aggregate |
| --- | --- | --- | --- |
| C++ simdjson (reference) | | 1.005-1.024 | 4.24-4.41 |
| C# System.Text.Json | | 0.667-0.669 | 4.19-4.23 |
| fast1 | strict pointer parser: SIMD string scan, integer key match, SWAR integers, lookup-table hex, whole-frame `Utf8.IsValid` | 1.111-1.143 | 9.32-9.71 |
| fast2 | AVX2 Keiser-Lemire UTF-8 validation instead of `Utf8.IsValid` | 1.253-1.288 | 9.49-9.59 |
| fast3 | speculative 32-byte copies when decoding escaped strings | 1.297-1.307 | |

fast1 against C++ on the other cells: HARD mixed aggregate 1.134-1.139 vs 0.994-1.005, retain-reuse 0.783-0.802 vs 0.73-0.745, retain-allocate 0.793-0.794 vs 0.728-0.73; EASY retain-reuse 5.469-5.589 vs 3.245-3.252.

Correctness checks:

- The selftest runs every check against both parsers.
- It differentially fuzzes FastJson against the `Utf8JsonReader` path on 60,000 documents (half clean, half invalid or mutated), with each input placed right before a no-access page. It also fuzzes `Utf8Check` against `Utf8.IsValid` on random texts and a deterministic block-boundary sweep, and checks canary bytes around the scratch areas FastJson may write.
- Soak: `BENCH_FUZZ=777,20` and `BENCH_FUZZ=424242,20` each passed 2,563,484 checks (`../audit-checks/fuzz-soak-fastjson.log`).
- Seven planted bugs were all caught: leading zeros, a lone surrogate, the 4,096-byte limit off by one, the value range off by one, a missing check on kind's closing quote, a corrupted UTF-8 table entry, and a one-byte over-read. The over-read was stopped by the guard page as an access violation.
- The socket suites (protocol, differential, server regression, interop, large frames) also run with `BENCH_PARSER=fast`.
- An independent review found no memory-safety or equivalence bug; its hardening suggestions were applied.

## C++ compiler target (30 September 2026)

An independent review found that the C++ build had no `/arch` flag. MSVC then defines none of the macros simdjson uses to pick its compile-time kernel, so the On-Demand front end (string unescaping and iteration) was the generic fallback kernel. Only stage 1 dispatched to AVX-512 at runtime. The .NET JIT compiles for the host CPU.

These are processing-only runs: one pinned core, fresh interleaved processes, in millions of records per second (`ab-cpp-arch.log`). `parser_builtin` in the build info records the compile-time kernel.

| C++ build | simdjson front end | simdjson, HARD | simdjson, EASY | C++ FastJson port, HARD | C++ FastJson port, EASY |
| --- | --- | --- | --- | --- | --- |
| no /arch (every campaign before fair-2x2) | fallback | 1.024-1.029 | 4.391-4.420 | 1.287-1.295 | 9.162-9.176 |
| /arch:AVX2 | haswell | 1.026-1.037 | 4.486-4.488 | 1.274-1.284 | 9.063-9.133 |
| /arch:AVX512 | haswell | 1.036-1.038 | 4.469-4.489 | 1.308-1.319 | 9.195-9.319 |

The flag moves simdjson by only 1-2%, so the earlier library results stand. From the fair-2x2 campaign on, C++ is compiled for the host CPU (`bench/build.ps1` picks AVX512 or AVX2), as the JIT does. The same review led to further parity changes in the port: an out-of-line throw helper (like the managed `Fail`), `__forceinline` exactly where C# has `AggressiveInlining`, reasons passed as `string_view`, a local checksum accumulator, and no simdjson buffers on the fast path.

