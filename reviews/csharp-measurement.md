# C# measurement and fairness review

Reviewed the frozen contract, complete design plan, C# processing/transport/load/metrics paths, C++ counterparts, runner, and report generator. No production file or frozen executable was modified.

Frozen executable hashes:

- C#: `73489b69f86f49137b14fa8699ebf7929f4d9632e4937492e90deec55f0b4764`
- C++: `fe494c9bf033c15867de263994d3bcf288363285c94818b004775a294784361d`

## Findings

### [P1] Processing-only results skip the category oracle

Location: `src/dotnet/Load.cs:412`–`413`.

`Cycle()` verifies only record count and digest. It never compares computed category counts/sums with the corpus metadata. Those arrays are independently required results; the digest hashes typed input rows rather than the computed aggregates. Consequently, a category bug or corrupt expected metadata can receive `valid:true` and exit zero. C++ `src/cpp/load.cpp:160` compares the complete `Result`, including both category arrays. C# network clients correctly verify the full End summary, so this is specifically the processing-only control.

Proof:

```powershell
python reviews\csharp-measurement\reproduce.py metadata
dotnet src\dotnet\bin\Release\net11.0\Bench.dll process --corpus reviews\csharp-measurement\bad-sum.bin --mode aggregate --warmup 0 --duration 0.01
src\cpp\build\Release\tcpbench.exe process --corpus reviews\csharp-measurement\bad-sum.bin --mode aggregate --warmup 0 --duration 0.01
```

The generated one-record corpus has actual bucket-0 sum `123`, expected sum `124`, and correct record count/digest. Its metadata passes structural limits. All three C# process modes exit `0`, `valid:true`; all three C++ modes exit `1`, `process_oracle_mismatch`. Logs, JSON output, and the persistent regression corpus are in `reviews/csharp-measurement/`. The C# selftest's full-summary check already illustrates the missing independent verification (`src/dotnet/SelfTest.cs:60`–`61`).

Required correction: verify category arrays during each full-corpus processing cycle, including warmup, using the same real processing path. Keep the mismatch as a nonzero exit without a successful result.

### [P1] Reports treat inadequate generators as inferential evidence

Locations: `bench/report.py:21`–`28`, `bench/report.py:39`–`44`, and `bench/report.py:49`–`67`. The runner's `bench/run.py:188`–`190` checks correctness only, so valid runs with inadequate generators can enter its `results.json`.

The report discards validity, generator adequacy, sustainability, timed-out/unresolved demand, histogram sample/overflow counts, and deadline misses when creating `trials.csv`. It then includes every row in completion-rate groups and paired ratios, without checking whether its requested arrival schedule was delivered adequately. A run can be correctly reconciled but explicitly unsuitable as evidence about the server at its offered load. The contract/plan requires that distinction; the C# executable provides it.

Proof:

```powershell
python reviews\csharp-measurement\reproduce.py report
python bench\report.py reviews\csharp-measurement\report-inadequate
```

The fixture contains two otherwise valid paired results with `generator_adequate:false`, `sustainable:false`, `scheduler_late_over_1ms:1000`, and `misses_1ms:1000`. Generated `report.md` still reports ordinary `1000.00` frames/s rows and a C#/C++ paired ratio of `1.0000`. `trials.csv` contains none of the inadequacy or SLO fields. Raw `results.json` preserves the flags, but users reading the generated comparison cannot see why the pair is unsuitable evidence.

Required correction: retain every raw trial with adequacy/validity/miss counts and explicit exclusion reasons. Exclude invalid or generator-inadequate pairs from inferential comparisons. Preserve legitimate server-overload runs as labeled overload evidence; `sustainable:false` alone is not a reason to erase that workload.

### [P2] Timeout failures report zero time spent draining

Locations: `src/dotnet/Load.cs:275`–`280` and `src/dotnet/Load.cs:300`.

`drain_seconds` is computed from the last successful ACK, rather than from drain completion or deadline expiry. With no ACKs, `last` stays at phase start, and even a run that exhausts its declared drain deadline reports `drain_seconds:0`. Partial-success failures similarly stop the recorded drain duration at the earlier successful ACK. The configured `--drain-seconds` value is also absent from result configuration fields, so the failure JSON loses the actual waiting interval. C++ separately records phase finish after worker cleanup (`src/cpp/load.cpp:124` and `146`).

Proof:

```powershell
python reviews\csharp-measurement\reproduce.py client
```

The withholding case uses a `0.1`-second measured window and a `0.5`-second drain deadline. It reports:

```json
{"offered":10,"admitted":8,"acknowledged":0,"timedout":8,"unresolved":8,"cohort_seconds":0.1,"drain_seconds":0}
```

`reviews/csharp-measurement/evidence-all.json` records approximately `0.564` seconds between the peer's first-data timestamp plus the requested window and subprocess completion. Thus the reported zero drain is not an immediate failure. Failure classification and exit code are correctly negative; the defect is lost failure timing telemetry.

Required correction: save an actual drain-finish timestamp and report elapsed drain separately from the successful-ACK cohort denominator. Preserve the declared drain bound as configuration metadata.

## Runnable coverage and passed checks

```powershell
python reviews\csharp-measurement\reproduce.py all
dotnet run --project reviews\csharp-measurement\Probe.csproj --configuration Release -- src\dotnet\bin\Release\net11.0\Bench.dll
```

The reflection probe builds only its own isolated harness and loads the frozen C# DLL; it has no project reference and does not rebuild the benchmark. It passes `100091` histogram samples: exact small values, both sides of powers of two, randomized finite values, the 60-second boundary, `long.MaxValue`, complete bucket counts/upper bounds, rank-based quantiles including null overflow ranks, maximum, merge, and clear. No histogram correctness/overflow defect was found.

Independent transport peers corrupt the final bucket-63 count or signed sum after correct data ACKs. Both cases fail with nonzero client exit and `valid:false`, proving full End array verification rather than only totals/digest verification.

Repeated per-frame `25 ms` and `100 ms` stalls at `1000` offered frames/s for `0.4 s`, global window `8`, expose the pause in scheduled latency and preserve all `400` offered arrivals. One run respectively admitted/ACKed `23` and `11`, rejected `377` and `389`, and counted all `400` as 10-ms misses. Latency sample counts equal ACK counts, outstanding-frame high-water stays at most `8`, and payload high-water stays at most `16` bytes. Successful drain completions use cohort denominators longer than the measured window. These checks did not find coordinated-omission masking or dropped offered demand.

The read path preserves intended timestamps, admission failures advance scheduled corpus cursors, cursors persist across warmup, Begin/End delimit epochs, descriptors/bytes are globally bounded, and high-resolution pacing does not alter global timer resolution. Existing parent selftest/protocol/sabotage results were reported as passing; this review adds the independent cases above rather than rerunning shared result folders.

## Fairness limits and remaining risk

Resource/stage scopes are disclosed but differ: C# client CPU/allocation snapshots cover measured-phase setup, controls, and drain; C++ client resource metrics cover process lifetime including warmup. Server resource metrics cover their whole lifetimes. These raw CPU fields cannot be directly compared as measured-record costs without aligning scopes. C# retained visit timing includes eviction verification; C++ visit timing excludes it, while total processing includes it. Stage sampling offsets also differ (C# frame 1024 first, C++ frame 1 first). Compare total processing cautiously and use sufficient samples rather than equating the differently scoped visit percentiles.

Both retained modes materialize owned typed rows and UTF-8 text and verify eviction digests. C# reuse keeps one spare batch; C++ retains the available slot capacities, so mixed-size memory/allocation behavior is implementation-dependent. This is disclosed behavior, not an observed semantic failure.

No sustained campaign, trace-based CPU/GC attribution, client-headroom calibration, or memory-plateau claim is established by these short tests. Extremely late scheduler wakeups around T1 and long cancellation/churn remain unexhausted timing/race risks; no reproducible defect was observed and they are not findings. Rebuild hashes and invalidate affected results after fixes, then rerun these probes against the new frozen executables.

## Post-fix report-only recheck

The parent changed `bench/report.py` after the initial review. Independently reviewed and exercised report hash `0656d72414dd023912509814a219812b988cd068b05ce93fa9025082434d2c35`. Finding 2 is resolved for the tested result shapes. No additional concrete report defect was found.

```powershell
python reviews\csharp-measurement\recheck_report.py
```

Evidence is stored separately in `reviews/csharp-measurement/report-postfix/evidence.json` and its fixture/report subdirectories. The original failing report remains byte-identical, hash `b5e1728792a1ded9d4baf5656b69f07bac9a9677dc5ae15d609eece272c81f9e`.

The independent matrix covers 13 cases and preserves all 26 input trial rows: adequate but unsustainable overload, inadequate generator, missing adequacy flag, invalid client, unsuccessful client, invalid server, failed demand, timed-out demand, unresolved demand, zero/negative window, duplicate pair, and incomplete pair. Only the fully eligible two-member overload pair enters the ratio table. It produces the expected ratio `2.0000`; native deadline/scheduler aliases and histogram count/overflow values survive CSV normalization. Null p99.9 remains blank and the insufficient-sample gate remains false.

Seven independent pair mutations—corpus hash, global window, duration, warmup, seed, arrival shape, and phase—each prevent unlike members from forming a ratio. The tests reprocess all 24 integration-smoke and 18 large-frame trials in private review folders, checking raw row preservation, native/C# deadline and scheduler aliases, histogram sample/overflow counts, and percentile values. This includes 21 trials generated by the native client.

No C# binary checks ran in this pass; the processing-only and failure-drain fixes still await the parent's new-build readiness message. Production files and original frozen proof artefacts were not edited.

## Post-fix C# binary/source recheck

The parent subsequently froze C# DLL hash `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`. Rechecked that exact DLL and the corresponding source. Findings 1 and 3 are resolved in the exercised cases. Together with the preceding report pass, no concrete finding from this review remains open; this statement covers the checks below, not all possible inputs or races.

```powershell
python reviews\csharp-measurement\recheck_binary.py
dotnet run --project reviews\csharp-measurement\binary-postfix\probe\PostfixProbe.csproj --configuration Release -- src\dotnet\bin\Release\net11.0\Bench.dll
dotnet reviews\csharp-measurement\bin\Release\net11.0\Probe.dll src\dotnet\bin\Release\net11.0\Bench.dll
```

New evidence/logs/corpora live under `reviews/csharp-measurement/binary-postfix/`; `evidence.json` and `probe-evidence.json` record outcomes. The checks hash and preserve the historical evidence, original bad-sum corpus, and original zero-drain result. Only isolated probe projects are compiled; the benchmark is never rebuilt by these commands.

Category verification now runs in the shared `Load.ProcessCycle` (`src/dotnet/Load.cs:408`–`418`), called by warmup and measurement (`425`, `428`). It directly compares the processor's existing per-frame arrays (`src/dotnet/Processing.cs:147`–`149`). Twelve negative cases—bad sum or a legal count/sum moved to another bucket, three processing modes, warmup zero or `0.2 s`—all exit `2` with `process_oracle` and `valid:false`. Eight valid controls cover all four modes with and without warmup, repeatedly process two distinct category buckets, and reconcile frames/records. This checks that validation uses per-frame categories rather than accumulated epoch arrays.

The failure timestamp now follows completion of all data send/ACK workers (`src/dotnet/Load.cs:276`–`279`), and timing derives from that timestamp (`304`). A completely withholding peer reports `8` timeouts/unresolved requests, `0.5156637 s` observed drain, and `0.6156637 s` cohort for a `0.1 s` window/`0.5 s` limit. A partial-success peer returns one ACK, then withholds: it reports one successful sample, eight timeouts, `0.5125375 s` drain, and `0.6125375 s` cohort. Both are invalid and generator-inadequate; both preserve `drain_limit_seconds` and `configured_drain_seconds`. A separate successful peer delays End by `250 ms`; that delay stays outside the data throughput denominator (`cohort_seconds:0.1000005`), matching the declared timing scope.

The new stage export (`src/dotnet/Load.cs:78`–`82`) passes independent boundary checks at sample counts `1`, `9999`, `10000`, `999999`, and `1000000`. Both p99 aliases remain null below `10000`; both p99.9 aliases remain null below `1000000`. At the boundary the expected bucket upper bound appears. Counts, maxima, and raw buckets survive the display gate, while raw client percentiles remain unchanged. The original randomized/boundary histogram probe also passes all `100091` samples against the new DLL.

Real aggregate and retain-reuse servers each process an epoch of `2048` empty-array frames followed by a new epoch of `1024`, with independently verified ACKs and full End summaries. Their final output records `3072` completed frames but only one final-epoch processing/decode/receive sample. Stage p99/p99.9 are null, retention visit sampling matches its mode, and error counters are empty. This checks that warmup samples do not leak into the final epoch.

Five class boundaries now match the native definition (`src/dotnet/Load.cs:133`–`139`): <=4 KiB, <=64 KiB, <=256 KiB, <=1 MiB, and larger through 16 MiB. Reflection verifies ten inclusive/lower boundaries and all five initially empty classes. A real client/peer exchange uses exact payload sizes `4096`, `4097`, `65536`, `65537`, `262144`, `262145`, `1048576`, `1048577`, yielding class sample counts `[1,2,2,2,1]` and eight verified ACKs. Ordered `size_latency` and keyed histograms agree, and raw class p99.9 values remain available. The padded payloads are explicitly diagnostic fixtures, not performance inputs.

These bounded regressions do not establish sustained memory/queue plateaus, performance conclusions, or absence of long-run cancellation races. The previously recorded CPU/stage scope distinctions remain relevant when interpreting campaign results.
