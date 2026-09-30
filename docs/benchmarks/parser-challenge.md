# Library parsers vs hand-tuned parsers (30 September 2026)

The [main results](results.md) compare each language with its standard JSON library: safe C# with `System.Text.Json` against C++ with simdjson. C++ won on messy JSON, and the evidence pointed at the parser, not the language. This page tests that directly by taking the library out of the picture.

**FastJson** is a strict parser written for this benchmark's one schema. It exists twice, once in C# ([FastJson.cs](../../src/dotnet/FastJson.cs)) and once as a line-by-line C++ port ([fastjson.cpp](../../src/cpp/fastjson.cpp)), and it is selected with `--parser fast`. Both languages then run the same algorithm, which makes two fair comparisons possible:

| Comparison | C# | C++ | What it shows |
| --- | --- | --- | --- |
| **Library** | `System.Text.Json` (safe code) | simdjson | What you get by writing normal code |
| **Hand-tuned** | FastJson | FastJson port | What each language can do at its limit |

## Results

Every number is a median over paired runs, with C# and C++ measured back to back in random order. "C# / C++" is the median of the per-pair ratios. Only trials that passed every gate count, including the new [environment gate](#what-went-wrong-with-the-latency-phases). Raw data: [`results/fair-2x2-20260930`](../../results/fair-2x2-20260930/).

### Full server over TCP (4 server cores)

| Workload | Comparison | Pairs | C++ M records/s | C# M records/s | C# / C++ | C++ p99 ms | C# p99 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| HARD, keep records | Library | 5 | 2.526 | 1.859 | 0.733 (0.693–0.742) | 8.6–9.2 | 13.9–15.6 |
| HARD, keep records | Hand-tuned | 5 | 2.891 | 2.910 | 0.971 (0.965–1.028) | 7.2–8.4 | 8.9–10.0 |
| HARD, fresh memory | Library | 5 | 2.458 | 1.819 | 0.735 (0.729–0.798) | 8.9–10.3 | 16.2–16.9 |
| HARD, fresh memory | Hand-tuned | 5 | 2.858 | 2.721 | 0.965 (0.944–1.000) | 7.6–9.3 | 12.7–14.8 |
| EASY, keep records | Library | 4 | 11.039 | 10.848 | 0.984 | 2.8–3.1 | 4.3–5.1 |
| EASY, keep records (4 client cores) | Hand-tuned | 3 | 17.216 | 16.684 | 0.969 | 1.9–2.0 | 3.4–3.5 |
| EASY, aggregate | Library | 2 | 14.482 | 13.209 | 0.912 | 2.2 | 3.9–5.3 |

The p99 values are saturation latencies with 4 requests outstanding per connection, not response times at normal load.

Two cells have no hand-tuned TCP result:

- **EASY aggregate.** Both hand-tuned servers outran the load generator, which sat at 94–99% CPU even with a fourth core. Those runs are excluded.
- **HARD aggregate.** Excluded for the same reason, in both comparisons.

### Parsing only (one core, no sockets)

Most processing-only pairs of this campaign ran while the machine was busy and are excluded; the pair count shows what is left.

| Workload | Library C# / C++ | Pairs | Hand-tuned C# / C++ | Pairs |
| --- | ---: | ---: | ---: | ---: |
| EASY aggregate | 0.968 | 3 | 0.986 | 2 |
| EASY keep records | 0.989 | 2 | 0.983 | 1 |
| HARD 64 KiB aggregate | 0.667 | 2 | 1.052 | 1 |
| HARD mixed aggregate | 0.656 | 1 | 1.012 | 1 |
| HARD mixed keep records | 0.701 | 1 | 0.995 | 1 |
| HARD mixed fresh memory | 0.702 | 1 | 0.996 | 2 |

The pinned A/B runs during development agree with these (see the [diagnostics](../../results/diagnostics-csharp-parser-20260929/README.md)): hand-tuned HARD aggregate 1.31–1.32 M records/s in C++ against 1.30–1.32 in C#.

### What hand-tuning bought each language

Full server, HARD, keep records:

| | Library | Hand-tuned | Gain |
| --- | ---: | ---: | ---: |
| C# | 1.859 | 2.910 | +57% |
| C++ | 2.526 | 2.891 | +14% |

### Memory and GC (hand-tuned, HARD)

| Workload | Server | Peak working set | Allocated B/record | GC gen0 / gen1 / gen2 | GC pause in 60 s |
| --- | --- | ---: | ---: | --- | ---: |
| Keep records | C++ | 691–692 MiB | 0.0 | – | – |
| Keep records | C# | 874–883 MiB | 0.1 | 0 / 0 / 0 | 0 |
| Fresh memory | C++ | 190–193 MiB | 828.8 | – | – |
| Fresh memory | C# | 1,015–1,021 MiB | 829.6 | 159–171 / 159–171 / 0 | 1.21–1.27 s |

A faster parser does not change memory use. It raises the allocation rate in the fresh-memory mode, so the GC pauses more: 1.2 s per minute against 0.8 s with the library parser.

### Cross check: hand-tuned C# against simdjson

An earlier campaign ([`results/parser-challenge-20260930`](../../results/parser-challenge-20260930/), a quiet machine, 98 trials, 0 failures) ran FastJson in C# against the C++ library path. It is not a fair language comparison, but it shows how far a specialized parser gets past a general one:

| Workload | C# FastJson / C++ simdjson |
| --- | ---: |
| Parsing only, EASY aggregate | 2.18 |
| Parsing only, EASY keep records | 1.72 |
| Parsing only, HARD aggregate | 1.29 |
| Parsing only, HARD keep records | 1.18 |
| Parsing only, HARD fresh memory | 1.17 |
| TCP, HARD keep records (5 pairs) | 1.14 (1.13–1.15) |
| TCP, HARD fresh memory (5 pairs) | 1.13 (1.11–1.14) |
| TCP, EASY keep records, 4 client cores (3 pairs) | 1.53 |

## What the results say

Facts:

- With library parsers, C# reaches 97–99% of C++ on EASY in three of four cells and 66–74% on HARD. The fourth EASY cell, aggregate over TCP, shows 91% from 2 pairs; the [main campaign](results.md) measured 95% there from 7 pairs.
- With the same hand-tuned parser, C# reaches 96–105% of C++ on every workload that could be measured. On HARD over TCP the paired ratios are 0.971 and 0.965.
- The hand-tuned parser is worth +57% to C# and +14% to C++ on HARD. simdjson was already close to what a specialized parser achieves; `Utf8JsonReader` was not.
- C# keeps its memory and GC costs either way: 1.0 GiB against 0.2 GiB in the fresh-memory mode, and a higher saturation p99 in every cell.

Hypotheses:

- The remaining 3% on HARD is within what bounds checks, the GC write barrier and thread-pool scheduling could explain. It was not isolated.
- About half of each HARD record's time is the FNV-1a checksum, which is byte-serial and identical in both languages. It caps how far apart the two can be.

Not measured:

- Response time at normal load for the hand-tuned pair. See the next section.

## What went wrong with the latency phases

The campaign ran 264 trials with 0 failures, but other work started on the PC partway through: four compiler processes and a Python job, each using a full core. The runner records the CPU used by everything except the trial:

| Time (UTC) | Phases | Background CPU |
| --- | --- | ---: |
| 02:55–03:37 | processing controls, library EASY saturation | 7–40% (one trial 73%) |
| 03:38–05:24 | hand-tuned EASY saturation, all HARD saturation | 3–11% |
| 05:25–06:53 | every paced and sustained phase | 55–92% |

Under that load the paced phases rejected up to 97% of arrivals for both servers while the servers sat nearly idle. A control experiment with five load-generator builds, including one from before this work, failed the same way, so the cause was the machine and not the code.

Two changes came out of this:

- **An environment gate.** A trial is excluded when background CPU exceeds 15% of the machine's logical CPUs. Quiet campaigns have a median of 4–7% and a 90th percentile of about 11%. The gate excluded 104 of the 264 trials; with pairing and the other gates, 96 remain. This tightens the rules; nothing that was excluded before is included now.
- **`--quiet-wait`.** The runner can wait before each trial until other CPU use drops below 10%, and stops with a clear message if it does not. `--resume` then continues later.

The paced and sustained phases for both comparisons, and the excluded saturation and processing pairs, still need to be measured on a quiet machine:

```powershell
python bench/run.py --matrix bench/matrices/fair-2x2.json --output results/fair-2x2-quiet --quiet-wait 3600
```

Until then, response time at normal load is known only for the library pair, from the [main campaign](results.md).

## How FastJson works

Both versions do the same five things:

1. **Validate UTF-8 once per frame**, with the lookup algorithm of Keiser and Lemire that simdjson uses (AVX2). Outside strings only ASCII is legal, so one pass covers everything.
2. **Scan strings 64 bytes at a time** (AVX-512 when the CPU has it, else 32 bytes with AVX2) for a quote, a backslash or a control byte.
3. **Match the seven field names with integer compares.** `"timestamp_ns"` is one 8-byte and one 4-byte compare.
4. **Parse integers eight digits at a time** (SWAR), then finish digit by digit.
5. **Decode escaped strings with speculative 32-byte copies**, as simdjson does.

It accepts and rejects exactly what the library path does. Only some failure reasons differ.

## How it was checked

- **Differential fuzzing.** Each fast parser is compared with its language's library path on 60,000 documents per run (half valid, half invalid or byte-mutated), in two retention modes. Soak runs covered about 5 million cases in C#.
- **Guard pages.** Every fuzz input ends right before a no-access memory page, so reading one byte too far crashes the test. Canary bytes detect stray writes into unused scratch space.
- **Planted bugs.** Seven deliberate bugs were inserted one at a time in each language: leading zeros, a lone surrogate, two off-by-one limits, a missing quote check, a corrupted UTF-8 table entry and a one-byte over-read. All seven are caught in both languages. One was first missed in C++, which exposed a gap in both fuzz generators; the gap was closed.
- **UTF-8 validator.** Compared with `Utf8.IsValid` in C#, and with an independent scalar validator and simdjson's in C++, on random texts and a deterministic sweep across block boundaries.
- **AddressSanitizer** for the C++ port, including the fuzzers.
- **The socket suites** (protocol, differential, server regression, interop, large frames) run with both parsers in both languages.
- **The Python oracle** checks every frame of all five campaign corpora in all three modes, for both languages and both parsers.
- **Two independent reviews**, one per language. Neither found a memory-safety or equivalence bug.

## Fairness notes

- **C++ is compiled for the host CPU** (`/arch:AVX512` here), as the .NET JIT does. Earlier campaigns had no `/arch` flag, which left simdjson's On-Demand front end on its generic fallback kernel. Measured cost: 1–2%, so the earlier library results stand.
- **Same inlining and error paths.** The C++ port force-inlines exactly the functions C# marks `AggressiveInlining`, and its throw helper is out of line, like the managed one.
- **Same checksum loop.** Both accumulate into a local variable.
- **Same data structures.** Chunked batch storage is identical in both languages.
- **The C# fast path is unsafe code.** It uses raw pointers. The library path is fully safe C#.
