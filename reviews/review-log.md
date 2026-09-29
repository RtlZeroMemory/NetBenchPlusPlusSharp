# Implementation and adversarial review evidence

The [rework audit (round 3)](round3/README.md) supersedes the build and methodology state below. The six-reviewer Astra XHIGH round, formatting, simplification and frozen-build checks are recorded in the [second review round](round2/README.md). The remainder of this page preserves the first round and its historical build hashes.

Round 1 status: complete. Both Astra XHIGH implementations, all four Sol XHIGH adversarial reviews, fixes, and focused independent post-fix checks are finished. No concrete review finding remained unresolved in that tested scope. Initial reports and failing evidence are preserved.

## Team

| Assignment | Model / effort | Agent |
| --- | --- | --- |
| C# implementation | GPT-6 Astra / XHIGH | implement_csharp |
| C++ implementation | GPT-6 Astra / XHIGH | implement_cpp |
| C# framing, parsing, lifetime | GPT-6 Sol / XHIGH | review_csharp_protocol |
| C# measurement and fairness | GPT-6 Sol / XHIGH | review_csharp_measurement |
| C++ framing, parsing, lifetime | GPT-6 Sol / XHIGH | review_cpp_protocol |
| C++ measurement and fairness | GPT-6 Sol / XHIGH | review_cpp_measurement |

Both implementations were complete before the review agents started. The four reviews use two waves because the session permits three concurrent subagents. Reviewers inspect frozen builds and save standalone reproduction evidence without modifying production files.

## Frozen initial builds

- C# DLL SHA-256: `73489b69f86f49137b14fa8699ebf7929f4d9632e4937492e90deec55f0b4764`.
- C++ Release EXE SHA-256: `fe494c9bf033c15867de263994d3bcf288363285c94818b004775a294784361d`.
- C++ AddressSanitizer build: `src/cpp/build-asan/RelWithDebInfo/tcpbench.exe`.

Initial evidence is historical after a production fix; fresh builds and results must be recorded below.

## Initial verification

- C# selftest with smoke corpus: 1,745 checks.
- Each implementation: 236 shared protocol/oracle checks across all three processing modes; nine faulty-peer measurement cases.
- Native AddressSanitizer: selftest, 16 native loopback variants, and 236 shared protocol checks passed.
- Shared runner: 24 complete trials covering all four client/server combinations, three modes, and closed-loop/scheduled load. Every trial reconciled and shut down gracefully. Evidence: `results/integration-smoke/`.
- Held-out differential corpus: 200 batches / 828 records through each server in all three modes, matching the independent Python oracle.
- Large-frame/arrival integration: 18 trials covering 1 MiB, 16 MiB, all processing/transport modes as applicable, Poisson and complete burst cycles. Evidence: `results/large-frames-1790665249566346700/`.
- Harness checks: deterministic corpus, oracle metadata, corpus truncation, physical-core/SMT separation, and affinity inherited before child startup.

These are correctness and integration checks. Their short timings, small/repeated corpora, and simultaneous development activity do not establish comparative language performance. The overnight confirmation and 30-minute soak campaigns have not been run.

## Findings and fixes

| Finding | Correction | Regression evidence |
| --- | --- | --- |
| C# processing-only misses category arrays | Compare the existing per-batch counts/sums directly, without allocation or extra serialization, in warmup and measurement | Both altered-category fixtures rejected in all three JSON modes and both warmup configurations; valid controls pass |
| C++ processing blocks the deadline scanner and can ACK after expiry | Scanner uses a nonblocking connection lock; common deadline checks guard completion, processing and response publication; cancellation keeps pending IOCP ownership intact | Delayed processing gets no success ACK; other connections can connect and expire while it runs; Release and ASAN |
| C# drain and both cohort timings hide failed waiting | Timestamp actual data-worker finish; separate fixed-window and cohort denominators; preserve configured drain bound; End controls excluded | Withheld ACK produces a timeout, unresolved demand, and nonzero observed drain/cohort time |
| Reports include inadequate generators | Preserve raw rows, adequacy/SLO/error fields and explicit exclusion reasons; require both members eligible; separate unlike experimental identities | Independent report adversary: 13 case shapes / 26 rows, seven identity mutations, native field aliases; overload remains visible |
| C++ early failure loses intended arrivals | Reconcile planned scheduled demand, label aborted undispatched arrivals, advance cursors and include deadline misses | Early EOF/corrupt ACK retains all planned arrivals; closed-loop demand is not synthesized |
| C++ data socket timeout precedes allowed drain deadline | Use the common absolute T1 plus drain deadline for data I/O; classify actual deadline expiry | Honest ACK at 1.2 seconds succeeds for a 0.4-second window plus 1-second drain; withholding fails correctly |
| C++ sampled processing excludes diagnostic delay | Begin elapsed timing before the processing gate | Injected delay appears in the sampled processing maximum |

Additional fairness refinements: both stage samplers begin at data frame 1024 and reset at Begin; both retained visit stages include eviction verification; both use the same five payload-size classes. Stage p99/p99.9 are null below 10,000/1,000,000 samples. Native processing-only controls roll over their capped epoch for long runs. Healthy native scheduled phases wait through T1 before End, matching the managed lifecycle.

## Final frozen builds

- C# DLL SHA-256: `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`.
- C++ Release EXE SHA-256: `62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79`.
- C++ ASAN EXE SHA-256: `acc66959c532acb7d7f985f0dff3d4aba2505ee7fcea34006fa06b2bf4a645f0`.

## Verification after fixes

- Final cross-language runner: **24/24** trials, every client result valid, every generator adequate, all server metrics produced after graceful shutdown. All four client/server combinations, three workloads, closed-loop and scheduled arrivals. Evidence: `results/final-smoke/`.
- Final large-frame/arrival integration: **18/18** trials, 1 MiB and 16 MiB targets, both cross-language directions, processing and transport modes, Poisson and a complete burst cycle. Evidence: `results/large-frames-1790666530090959100/`.
- Final held-out differential corpus: 200 batches / 828 records for each server in all three processing modes; all ACKs and full category summaries match the independent Python oracle.
- Managed selftest: **1,781** checks; independent histogram probe: **100,091** samples; 12 corrupted-metadata mode/warmup combinations rejected and four valid processing controls passed.
- Independent managed protocol post-fix review: **30** checks, including 24 corrupt-category cases rejected and six valid retained-data controls across warmup/measurement.
- Independent managed measurement post-fix review: 12 corrupted-metadata cases rejected, eight valid controls passed, complete/partial withholding and delayed End timing verified, stage gates checked at 1/9,999/10,000/999,999/1,000,000 samples, two-epoch server sampling checked, and all five size classes validated through real mixed-size traffic. The 100,091-sample histogram probe still passes.
- Native Release and ASAN: selftest, **236** shared protocol checks, all **10** strengthened faulty-peer measurement cases, and focused deadline/stage regressions passed. Managed client also passed all ten measurement cases.
- Independent native measurement post-fix review: **12** client cases, **2** stage cases and **55,000** cursor-accounting arithmetic cases passed against the final Release build. Aborted steady/Poisson/burst schedules retain all demand; honest within-deadline ACK succeeds and true expiry is classified as timeout.
- Report/corpus/affinity harness passed, including invalid/inadequate pair exclusion, legitimate overload retention, experiment identity separation and affinity inherited before runtime initialization.

Persistent focused commands:

```powershell
python tests/harness.py
python tests/measurement.py --client src/dotnet/bin/Release/net11.0/Bench.dll
python tests/measurement.py --client src/cpp/build/Release/tcpbench.exe
python src/dotnet/regression.py
python src/cpp/regressions.py
```

## Independent review reports

- [C# protocol and lifetime](csharp-protocol.md): initial 473 additional assertions; category finding resolved in independent post-fix checks.
- [C# measurement and fairness](csharp-measurement.md): all three findings resolved; independent histogram/report probes and post-fix checks passed.
- [C++ protocol and ownership](cpp-protocol.md): initial 3,642 semantic exchanges and deadline finding; post-fix transport regression pass. The focused transport check first used intermediate binary `0418ab4d3a0106a260fca892d2e037d5cd509c3bbcbd9c703c271160f2148386`; its transport source is unchanged in the final build.
- [C++ measurement](cpp-measurement.md): all four findings resolved; independent demand/drain/stage checks passed on the final build.

## Equivalence and remaining limits

| Area | Matching contract | Remaining implementation difference |
| --- | --- | --- |
| Input and output | Exact corpus bytes; strict full-schema validation; canonical digest and all 64 bucket counts/sums | Utf8JsonReader versus pinned simdjson On-Demand |
| Transport | Same framing, sequencing, ACKs, TCP_NODELAY, requested buffers, global outstanding frame/byte caps | Managed asynchronous sockets versus native IOCP; managed async client versus native send/ACK threads |
| Retained data | Owned typed rows and UTF-8 bytes; same canonical byte/batch limits and revisit checks | Managed one-spare reuse versus native reusable slots; growth slack and actual memory can differ |
| Load and latency | Common steady/Poisson/burst rules, intended timestamps, accounting, windows, drain semantics and histogram bins | Different scheduling/runtime implementations; generator adequacy remains an explicit evidence gate |
| Resource budgets | Native Windows, identical disjoint physical-core budgets, pinned builds and same client within a comparison | Shared last-level cache, DRAM, power and OS scheduling remain unavoidable on loopback |
| Resources | Both expose process memory and CPU; managed implementation exposes allocation/GC observations | Native client/process CPU covers lifecycle; managed client/process snapshots have narrower scopes. Do not compare them as measured CPU/record. Native full allocation/free tracing remains separate |

No sustained confirmation, 30-minute soak, client-headroom/changed-core diagnostic, instrumentation-tax experiment, full allocation tracing or GC-pause attribution has been completed. The short checks above establish functional evidence, not performance rankings or memory plateaus. The independent native protocol reviewer did not complete new ASAN/churn/allocation-failure probes; ASAN coverage listed above comes from the implementation and shared suites. No ThreadSanitizer or proof of race absence is claimed. The documented ten-million-arrival Poisson/burst storage limit and one-billion-record network epoch limit remain explicit workload ceilings.
