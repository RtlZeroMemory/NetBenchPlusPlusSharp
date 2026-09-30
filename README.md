# NetBench++#

**C# vs C++, measured fairly.** Two TCP servers do exactly the same job: receive batches of JSON, validate every field, crunch the numbers and reply. One is written in C# on .NET 11, the other in C++23. They run on the same machine with the same data and must return bit-identical answers. Then we see which one is faster, and why.

![.NET 11 preview 7](https://img.shields.io/badge/.NET-11_preview_7-512BD4)
![C++23](https://img.shields.io/badge/C%2B%2B-23-00599C)
![Windows x64](https://img.shields.io/badge/platform-Windows_x64-0078D4)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

<p align="center">
  <img src="docs/images/results.svg" alt="C# throughput as a percentage of C++. With standard library parsers: 98% on simple JSON, 73-74% on messy JSON. With the same hand-tuned parser in both languages: 97% everywhere." width="800">
</p>

## The result in three lines

1. **Normal C# is fast.** Safe code with the standard JSON library matches C++ on simple JSON and reaches about 73% of it on messy JSON.
2. **The gap is the library, not the language.** Give both languages the same hand-tuned parser and C# runs at 97% of C++. That's a tie.
3. **C++ still wins on memory and steadiness.** It uses up to 5x less memory and its slowest replies are quicker.

## Two ways to compare

We measured both, because they answer different questions.

### 1. The code most people write

C# uses `System.Text.Json` with fully safe code. C++ uses [simdjson](https://github.com/simdjson/simdjson), the fastest general JSON library there is.

| Contest | C++ | C# | C# vs C++ |
| --- | ---: | ---: | ---: |
| Simple JSON, check and total | 14.6 M records/s | 14.0 | 95% |
| Simple JSON, keep records in memory | 11.0 | 10.8 | 98% |
| Messy JSON, check and total | 3.39 | 2.34 | 70% |
| Messy JSON, keep records in memory | 2.53 | 1.86 | **73%** |
| Messy JSON, keep records in fresh memory | 2.46 | 1.82 | **74%** |
| Slowest 1% of replies at normal load, messy JSON | 2.8-3.4 ms | 4.4-11.5 ms | C++ is 1.7-3x quicker |
| Peak memory, messy JSON in fresh memory | 0.2 GiB | 1.0 GiB | C++ uses 5x less |

Rows 1, 3 and 6 come from the earlier [library campaign](docs/benchmarks/results.md). Its C++ build did not yet target the host CPU, which is worth 1-2%.

### 2. The same hand-tuned parser in both languages

We wrote one parser for exactly this data (SIMD scanning, unsafe pointers, about 600 lines) and ported it line by line, so both languages run the same algorithm.

| Contest | C++ | C# | C# vs C++ |
| --- | ---: | ---: | ---: |
| Simple JSON, keep records in memory | 17.2 M records/s | 16.7 | 97% |
| Messy JSON, keep records in memory | 2.89 | 2.91 | **97%** (tie) |
| Messy JSON, keep records in fresh memory | 2.86 | 2.72 | **97%** |
| Parsing only on one core, simple JSON | 9.10 | 8.97 | 99% |
| Parsing only on one core, messy JSON | 1.24-1.28 | 1.30 | 101-105% |
| Slowest 1% of replies at full speed, messy JSON | 7.2-8.4 ms | 8.9-10.0 ms | C++ is a bit quicker |
| Peak memory, messy JSON in fresh memory | 0.19 GiB | 1.0 GiB | C++ uses 5x less |

Hand-tuning made C# 57% faster on messy JSON and C++ 14% faster. simdjson was already close to the limit. `System.Text.Json` was not.

The percentages are medians of paired runs (C++ and C# measured back to back), so they can differ a little from dividing the two medians. Most rows have 3 to 7 pairs; the one-core messy row has only 1, and the [detailed tables](docs/benchmarks/parser-challenge.md) give every count. "Simple" records are about 160 bytes of ASCII JSON. "Messy" records are about 770 bytes with shuffled fields, escapes, Unicode and long text. Everything ran on a Ryzen 7 9800X3D under Windows 11.

## What each choice costs

| | Safe C# | Hand-tuned C# | C++ with simdjson | Hand-tuned C++ |
| --- | --- | --- | --- | --- |
| Speed on messy JSON | 1.0x | 1.6x | 1.4x | 1.6x |
| Parser code to write and maintain | none | ~600 lines, plus a fuzzer | none | ~600 lines, plus a fuzzer |
| Memory safety | guaranteed | your job (unsafe pointers) | your job | your job |
| Memory use | up to 5x more | up to 5x more | lowest | lowest |
| GC pauses when allocating a lot | 0.8 s per minute | 1.2 s per minute | none | none |
| Works for any JSON | yes | no, one schema | yes | no, one schema |

In practice:

- **Writing a normal service?** Safe C# costs you nothing on simple payloads and about a quarter of your throughput on messy ones. For most systems that's a fine trade for memory safety.
- **Is JSON parsing your bottleneck?** A specialized parser closes the gap completely in C#. You pay with unsafe code and the testing it needs.
- **Need tight memory or steady response times?** That's where C++ keeps a real lead, whichever parser you use.

## Why the library matters so much

- On messy input, `System.Text.Json` spends about as long splitting the text into tokens as simdjson spends on its whole parse. simdjson checks 64 bytes at a time with SIMD instructions.
- The hand-tuned parser does the same in both languages: it scans strings 64 bytes at a time, matches field names with single integer compares and parses numbers eight digits at once.
- About half the time per messy record goes into the checksum both servers must compute. It is the same code in both languages, so it caps how far apart they can be.
- Networking is a tie. Both servers move raw bytes equally fast.

## How the benchmark works

1. **The load generator** opens 16 TCP connections to the server over loopback and sends batches of JSON records. Each record has an id, a timestamp, a source, a kind, a value, some flags and a text message.
2. **The server** checks every field against a strict schema. That means exact field names, number ranges, valid UTF-8 and no duplicates, and one bad record rejects the whole batch. Then it does one of three things:
   - **check and total:** add the record to running totals and a checksum;
   - **keep records:** also keep typed copies of the last 64 batches, reusing buffers;
   - **fresh memory:** the same, but allocate new memory for every batch.
3. **The server replies** with an acknowledgement. At the end, client and server compare checksums over everything processed, so skipped or mangled work fails the run.

It measures two things:

- **Max speed:** every connection keeps 4 batches in flight, and we count records per second.
- **Normal load:** batches arrive on a schedule, and we measure how long each one waits from when it *should* have arrived until it's answered. A stalled server can't hide its delays.

### Is it fair?

- Both servers get the same 4 CPU cores. The load generator gets 3 other cores, and one more is left free for Windows.
- C++ is compiled for this exact CPU, as the .NET JIT does.
- The load generator is the same program for both servers.
- Every run checks that the load generator kept up and that nothing else was using the machine. Runs that fail those checks are excluded and listed, never hidden. The rules were written down before collecting results.
- Every parser accepts and rejects exactly the same inputs. Both fast parsers are fuzzed against their library counterparts, with every input placed against a memory guard page, and all of them are checked against an independent Python reference on every record of the test data.

The results come from one machine over loopback. A different CPU, .NET version, JSON library or real network could move the numbers.

### What's still open

Response time at normal load for the hand-tuned pair is not measured yet. Other work started on the test PC during that part of the run, and the benchmark excluded those trials instead of reporting them. The normal-load numbers above come from the earlier library campaign on a quiet machine. The [details](docs/benchmarks/parser-challenge.md#what-went-wrong-with-the-latency-phases) explain what happened and how to finish the run.

## Run it yourself

You'll need Windows x64 with at least 6 physical cores, the .NET SDK pinned in [global.json](global.json), Visual Studio 2026 C++ tools with CMake, and Python 3.11+ (no packages).

```powershell
./bench/build.ps1                                                     # build both servers
./tests/run_all.ps1                                                   # every correctness check (add -Asan for AddressSanitizer)
./bench/run.ps1 -Matrix bench/matrices/smoke.json -Output results/smoke        # 2-minute sanity run
./bench/run.ps1 -Matrix bench/matrices/fair-2x2.json -Output results/fair      # both comparisons, about 4 hours
./bench/run.ps1 -Matrix bench/matrices/campaign.json -Output results/campaign  # the full library campaign, about 4.5 hours
```

Each run writes `report.md`, `trials.csv` and the raw JSON of every trial into its output folder. Close other programs first: a busy machine gets its trials excluded.

## What's in here

| Folder | What it holds |
| --- | --- |
| [src/cpp](src/cpp) | C++23 server, load generator and self-tests (IOCP, simdjson, FastJson port) |
| [src/dotnet](src/dotnet) | C# server, load generator and self-tests (async sockets, System.Text.Json, FastJson) |
| [bench](bench) | Build script, campaign runner, report generator and [test plans](bench/matrices) |
| [tests](tests) | Protocol, fault-injection, timing and cross-implementation tests |
| [tools](tools) | Test-data generator with an independent Python reference |
| [docs](docs) | Results, methodology, protocol and history |
| [results](results) | Published raw data from the campaigns and diagnostics |
| [reviews](reviews) | Code review and audit records |

## Going deeper

- [Library vs hand-tuned parsers](docs/benchmarks/parser-challenge.md): the full tables for both comparisons, how the fast parser works and how it was checked
- [Library campaign results](docs/benchmarks/results.md): every table for the standard-library comparison, including latency, bursts, GC and memory
- [Methodology](docs/benchmarks/methodology.md): the rules fixed before measuring, and what the diagnostics found
- [Protocol and result format](docs/contract.md): wire format, validation rules, load models, JSON schema
- [C++ implementation](src/cpp/README.md) and [C# implementation](src/dotnet/README.md): design notes and build details
- [Audit record](reviews/round3/README.md): every bug found and fixed, and what didn't pan out
- [Optimization diagnostics](results/diagnostics-csharp-parser-20260929/README.md): the A/B tests and ablations behind the numbers
- [First benchmark pass](docs/benchmarks/first-pass.md): the original run, kept for history, and why its latency conclusion didn't survive
- [Design proposal](docs/design-proposal.md): the original plan for the experiment

## License

MIT, see [LICENSE](LICENSE).
