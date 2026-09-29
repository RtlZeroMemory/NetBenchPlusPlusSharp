# C++ adversarial measurement review

Reviewed 2026-09-29 against frozen Release `src/cpp/build/Release/tcpbench.exe`, SHA-256 `fe494c9bf033c15867de263994d3bcf288363285c94818b004775a294784361d`. No production/shared source was edited or rebuilt. Probes use ephemeral loopback ports and bounded subprocess deadlines; all artifacts are in `reviews/cpp-measurement/`.

## Findings

### [P1] Account for scheduled demand after an early run failure

**Location:** `src/cpp/load.cpp:100`, `:106`, `:142`, `:144`.

The native scheduler exits immediately when a sender/reader sets `failed`. Only arrivals already reached increment `offered`; the remaining finite schedule is never added to rejection/failure accounting. Consequently, a steady `.4` second run at `100` frames/s that disconnects or returns a bad digest on its first request reports `offered=1`, `admitted=1`, `rejected=0`, and only one deadline miss, although the declared schedule contains 40 intended arrivals. Both cases also report `generator_adequate=true`. `valid=false` correctly prevents a successful run, but the demand and SLO statistics no longer describe the requested experiment.

**Reproduction:** `python reviews/cpp-measurement/probe.py`; inspect the `drop` and `corrupt` cases in `reviews/cpp-measurement/evidence.json`. The independently constructed peer either closes after receiving the first full request or returns a wrong digest. Both exits were nonzero. The missing 39 arrivals are deterministic, unrelated to performance or pacing accuracy.

**Required semantics:** retain the full planned scheduled demand, classify undispatched arrivals after an integrity/I/O failure explicitly (for example, `aborted_schedule_rejected`), count them as SLO failures, and keep corpus-cursor accounting consistent. Closed-loop demand has no future independent arrivals and should not receive synthetic demand. The managed implementation already had an explicit planned-minus-offered reconciliation during this review.

### [P1] Give admitted data the declared window-plus-drain deadline

**Location:** `src/cpp/load.cpp:134`, data `send_exact`/`receive_exact` calls at `:59`/`:68`, drain deadline at `:119`; `src/cpp/transport.cpp:16` and `:24`.

The native client installs socket receive/send timeouts equal to `drain-seconds`, while its admitted-data deadline is `T1 + drain-seconds`. Data I/O has no absolute phase deadline argument. A receive that starts early in the measurement therefore expires before the allowed drain deadline. Begin control calls also modify the same socket timeout and leave it installed for data I/O.

**Reproduction:** the `ack-inside-drain` case in `reviews/cpp-measurement/drain-evidence.json` uses one honest transport request, `.1` second measurement, `.5` second drain, rate `1`, and a `.55` second ACK delay. The ACK is due before `T1 + drain = T0 + .6s`, but the client exits after about `.512s` from the first request with `error="receive_failed"`, `acknowledged=0`, `unresolved=1`, and `timedout=0`.

Run the bounded reproduction with:

```powershell
python -c "import sys,json,pathlib; sys.path.insert(0,'reviews/cpp-measurement'); import probe; x=probe.case('ack-inside-drain',delay=.55,duration=.1,rate=1,drain=.5); pathlib.Path('reviews/cpp-measurement/drain-evidence.json').write_text(json.dumps(x,indent=2)); print(x['result'])"
```

**Required semantics:** apply the common absolute data-phase deadline through cancellation/remaining-time I/O, permit valid ACKs anywhere before it, and classify an actual data deadline expiry as a timeout. Begin/End controls retain their own bounded exchanges. This is a client issue, independent of the native server timer issue found by the protocol reviewer.

### [P2] Include failed drain waiting in the cohort denominator

**Location:** `src/cpp/load.cpp:124`, `:143`, `:146`, `:148`.

`cohort_seconds` uses only `last_ack`, whereas `drain_seconds` uses actual phase finish. If the peer acknowledges one request and withholds the rest, the cohort clock freezes at that first ACK while the client waits and fails later. A `.1s` measurement with `.5s` drain reported `acknowledged=1`, `unresolved=8`, `cohort_seconds=.1`, `drain_seconds=.4152849`, and `cohort_frames_per_second=10`. The observed lifetime after the first request was about `.520s`. The failed requests and wait remain outside the cohort rate's denominator.

**Reproduction:** `python reviews/cpp-measurement/probe.py`; inspect `withhold-after-one` and `withhold` in `evidence.json`. Unlike the managed frozen result previously reviewed, native `drain_seconds` is nonzero and accurately exposes this waiting; the native defect is the cohort denominator.

**Required semantics:** keep the fixed window rate unchanged. For a failed cohort, include the observed wait through phase failure/drain expiry in its elapsed denominator; report failure/unresolved counts separately and never treat missing requests as finite latency samples. Successful cohort timing may end at the last data ACK; End/control traffic should remain separately scoped.

### [P2] Include the diagnostic processing pause in sampled processing elapsed time

**Location:** `src/cpp/transport.cpp:77` and `:78`–`:81`.

The processing timer starts after the deliberate `Sleep`, even though the complete body is already available. This violates the plan's processing elapsed definition (complete body available to successful processing/retention completion) and makes controlled pause attribution inconsistent with C#, whose timer starts before its diagnostic delay.

**Reproduction:** `python reviews/cpp-measurement/stage_probe.py`. A native aggregate server with `--pause-every 1 --pause-ms 100` processes two `[]` frames correctly, verifies the full End summary, and has ACK round trips of `.104149s` and `.106882s`. Its sampled `processing_elapsed_ns.max_ns` is only `21000` ns. Evidence and logs are in `stage-evidence.json` and `stage-server.json`.

**Required semantics:** time the interval from body completion through processing/retention, including an injected processing gate, or separately expose and label the excluded gate interval. Apply the same interval definition across implementations.

## Verification completed

- Read the frozen contract, relevant load/measurement/run-lifecycle plan sections, all native application files, managed load/processing/server measurement code, and runner/report paths. Huge vendored parser internals were not needed.
- Eleven native client cases checked steady, SplitMix64 Poisson, burst, warmup/new epoch, deliberate overload/drain, full End count and sum corruption, early EOF, bad ACK, missing ACK, and partial success before missing ACK. Every result reconciled its reported offered/admitted/rejected totals; each successful-ACK histogram count equaled acknowledged requests. The probes verified global caps of eight descriptors and sixteen payload bytes for their two-byte corpus.
- Healthy `.4s` steady replay offered/completed 40 frames; Poisson 39; burst 24. Warmup preserved a successful measured epoch. Deliberate overload offered 400, completed 23, rejected 377, counted all 400 as 10ms misses, returned valid protocol results and `sustainable=false`, and used a longer `.5818778s` successful cohort rather than hiding drain successes in the `.4s` window denominator.
- End category count/sum corruption both failed with `end_summary_mismatch`; raw ACK corruption and EOF failed. Native processing-only equality compares full `Result`, including counts/sums, unlike the already reported managed defect.
- Source review found the histogram's finite-bin mapping/upper bounds and >=60s overflow/null-quantile policy consistent with the frozen contract. The existing native selftest covers mapping vectors, but this review did not wait 60 seconds to exercise an actual overflow through the client.
- The supplied existing selftest, protocol, differential/large-frame, and measurement results were accepted as prior evidence rather than rerun or overwritten. These new probes supplement their failure-accounting coverage; they are not throughput evidence.

## Genuine limitations and interpretation

- No sustained 2GiB, seven-pair confirmation, 30-minute soak, allocation/GC/ETW attribution, memory plateau, or instrumentation-tax trial was run. Existing smoke evidence is correctly exploratory; it cannot establish sustainable capacity or a language ranking. The runner randomizes pairs and keeps raw trials, but this review does not validate a completed confirmation campaign.
- Native client/process CPU counters cover process lifetime, including corpus loading/setup/warmup; managed client/process counters have measured-phase scopes. Both server lifecycle counters include warmup. They must not be normalized as interchangeable measured-only CPU costs. Native allocation/free counts are explicitly unavailable in ordinary output; owned vector capacity is distinct from canonical retention bytes.
- Retained rows/text are owned in both implementations, committed after validation, verified on eviction/end, and bounded by the common canonical byte/batch rules. Native recycled slots preserve more old high-water capacity than the managed single spare when byte pressure evicts multiple batches; actual capacity/memory measurements must remain visible rather than infer allocation-only equivalence.
- Native samples frames 1, 1025, ... while the managed frozen server samples 1024, 2048, ... . Retained stage scope also differs: native visit excludes eviction revisit, managed visit includes it. Their output labels disclose that difference, so those sub-stage numbers are not directly comparable. The one-sample native stage reproduction exports a numeric p99.9 (`21055ns`); such an extreme stage-tail estimate is unsupported by the sample count and the plan requests a sufficiency gate.
- The native processing-only phase accumulates into one capped epoch without the managed control's epoch rollover. Its documented 1e9-record cap can bound long processing-only requests; a billion-record reproduction was intentionally not run, so this is a source-review limitation rather than a verified finding.
- The already reported C# process-only category-check defect and report adequacy-flag defect were not duplicated here. Parent fixes to managed/report files began after their frozen reviews; this report's executable reproductions target the native hash above.

## Post-fix independent revalidation — scoped pass

The four findings above are historical and **closed by the focused recheck** against final frozen Release SHA-256 `62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79`. This hash was checked before and after the independent tests. Historical probes/results remain unchanged; new artifacts are in `reviews/cpp-measurement/post-fix/`. No production files were edited or rebuilt during revalidation.

Run the new proof with:

```powershell
python reviews/cpp-measurement/post-fix/verify.py
```

**Result:** 12 client cases, two server-stage cases, and 55,000 independent explicit-arrival assignment checks passed. Full commands, raw client/server results, timings, and the frozen hash are preserved in `post-fix/evidence.json` and individual logs/JSON.

| Historical finding | Independent final-binary evidence | Status |
| --- | --- | --- |
| Aborted scheduled demand disappears | Drop and bad-ACK faults retain the full independent steady/Poisson/burst schedules: 40/39/24 offered. Steady first-request failure now records admitted=1, rejected=39, aborted_schedule_rejected=39, all 40 SLO misses, and generator_adequate=false. | Closed |
| Within-drain ACK rejected early | With `.1s` window, `.5s` drain, and one `.55s` delayed ACK, the run succeeds with acknowledged=1, unresolved=0, window completions=0, cohort=.550768s, drain=.450768s. A `.7s` ACK instead fails with timedout=unresolved=1. | Closed |
| Failed cohort freezes at last ACK | Withheld responses report timedout=unresolved=8. The partial-success run has acknowledged=1, cohort=.6150602s and drain=.5150602s; cohort rate is 1.625857 frames/s while the fixed-window rate remains 10 frames/s. | Closed |
| Processing sample excludes pause | A 50ms pause on frame1024 appears in final sampled processing max=60,433,600ns. The preceding 1023-frame run has zero samples. Two 1024-frame epochs leave exactly one final-epoch sample. | Closed |

Additional checks showed that a healthy single-request `.4s` phase waits through T1 (cohort=.4003088s), while intentional overload stays a valid but unsustainable experiment: offered400, acknowledged23, rejected377, unresolved0, all400 10ms misses, cohort=.5815221s and drain=.1815221s. The global descriptor/payload caps and histogram/ACK-count reconciliation still hold in these probes.

Final source inspection verifies:

- `src/cpp/load.cpp:98`–`:101` derives the finite schedule count, `:132`–`:135` accounts for undispatched arrivals and updates each connection-local cursor, and `:159` prevents failed schedules from claiming generator adequacy. The cursor update was compared with enumerating every remaining assigned arrival across 1–8 connections, 1–11 corpus frames, and 0–24 dispatched/remaining requests. All 55,000 cases match. The post-abort cursor is not exported by the executable, so that portion is explicitly an arithmetic/source check rather than a black-box observation.
- Data sends at `src/cpp/load.cpp:69` and ACK receives at `:78` use absolute `T1 + drain`. `src/cpp/transport.cpp:17`, `:21`, and `:24` reduce remaining I/O time and distinguish socket timeout/deadline reasons; `src/cpp/load.cpp:50`/`:138` retain actual timeout counts. Independent missing-ACK runs exercised drain expiry and correctly reported timedout work instead of generic receive failure.
- `src/cpp/load.cpp:129` waits through T1 on healthy phases, `:141` records finish after data workers join, and `:160`/`:163` use that finish for the cohort while excluding End control. In both failed-drain cases, cohort equals window plus reported drain within output precision and matches observed peer-to-exit elapsed time within the probe tolerance.
- `src/cpp/transport.cpp:89` starts sampling on frame1024 and the timer precedes the pause at `:90`. Begin resets stage histograms. `src/cpp/main.cpp:70` gates stage p99 below10,000 samples and p99.9 below1,000,000 samples; both remain null in the new one-sample server output. `src/cpp/processing.cpp:89`–`:94` now adds retained eviction revisit to the visit interval, and the exported scope agrees. The former processing-only 1e9-epoch limitation also has an explicit rollover at `src/cpp/load.cpp:178` with separate whole-run totals; no billion-record run was needed or performed.

No unresolved concrete defect remained within this focused final-source/final-Release recheck. The earlier limits on sustained performance, CPU/allocation attribution, true 60s histogram overflow exposure, large working-set memory plateau, and confirmation/soak evidence still apply. The implementer's existing Release/ASAN regressions and strengthened measurement suite were supplied as prior evidence; this recheck adds independent Release proofs and makes no new ASAN/performance claim.

