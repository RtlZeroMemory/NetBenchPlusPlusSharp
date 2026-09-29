# C++ correctness and measurement review, round 2

**Post-fix status:** both findings below are resolved in the independently checked Release build `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329`. The original findings and frozen failing evidence remain unchanged below; see the post-fix section for the exact scope.

Reviewed the frozen Release executable SHA-256 `62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79` and all five native application files, the shared contract, previous reviews, native regressions, and corresponding managed paths. No production source or build output was changed. All new probes use ephemeral loopback ports, bounded waits, fresh server processes, and preserved logs. These are functional probes, not performance trials.

## P1 — CPU processing can outlive its frame budget and commit expired work

Locations in the reviewed source: `src/cpp/transport.cpp:84-98`, `:105`, `:155`; `src/cpp/processing.cpp:43-103`.

The worker owns `Connection::mutex` throughout parsing, materialization, incoming-row visits, retained verification, and the injected pause. The deadline scanner correctly avoids blocking on that mutex, but those operations have no internal deadline or shutdown checks. The post-pause guard fixed the previous late-ACK defect for a diagnostic pause; ordinary `Processor::apply` still finishes, commits its epoch/retention changes, and increments server completion totals before `respond` discovers expiration. The response guard prevents a success ACK, but cannot cancel work or undo that expired commit.

Observed on the frozen executable, without any diagnostic pause, using one valid 15,640,001-byte batch containing 170,000 rows and a 30 ms frame deadline:

| Mode | Client send elapsed | Reply/closure observed | Completed frames / records | Rejection |
| --- | ---: | ---: | ---: | --- |
| retain-reuse | 13.1 ms | 70.5 ms | 1 / 170,000 | frame_timeout |
| retain-allocate | 6.2 ms | 77.4 ms | 1 / 170,000 | frame_timeout |
| aggregate, same probe | 4.2 ms | 37.3 ms | 0 / 0 | frame_timeout |

Both retained cases received no ACK. The aggregate run and earlier 10 ms runs expired before committing and are preserved as non-reproducing controls, not presented as proof of the retained failure. Timing comparisons are diagnostic only; the completed-count-plus-timeout combination and source commit order establish the defect.

Evidence: [normal-processing cases](cpp-bugs/processing-30ms-evidence.json), [earlier receive-expiry cases](cpp-bugs/processing-evidence.json), [probe](cpp-bugs/processing_deadline_probe.py), and individual server JSON/stdout/stderr files in [cpp-bugs](cpp-bugs/).

The diagnostic manifestation is deterministic and longer: with a 100 ms frame/idle deadline, `--pause-ms 900`, one worker and one connection slot, the connection remained open after 359 ms, rejected a replacement connection, and closed after 905 ms. A partial-body control closed in 106 ms and admitted the replacement. Sending `stop` 50 ms into the same pause with a generous frame deadline took another 873 ms to exit. Native completed counts remain zero for this paused case because the existing post-pause check works.

Evidence: [pause/shutdown cases](cpp-bugs/evidence.json), [probe](cpp-bugs/probe.py). This extends the previous successful unrelated-connection regression: that check used spare workers/slots and did not require the busy connection itself to release its resource budget on time.

Minimal correction: pass an optional processing budget into the existing processor, consisting of the absolute earliest applicable frame/idle deadline and the existing server stopping flag. Check it before processing, periodically during record loops (for example every 1024 records), and immediately before committing. Use the same budget to interrupt the diagnostic wait. Retained paths must check both initial materialized-row visits and eviction verification; `verify` invoked by End or graceful EOF must also observe shutdown and the applicable active-frame deadline. Do not make EOF on an otherwise quiet connection inherit an already-finished frame's stale deadline. Processing-only controls can omit the budget.

Keep cancellation checks before any retained-state mutation so a timed-out batch does not partially evict or enter the epoch. Preserve the existing IOCP rule that closed sockets remain owned until pending completion is dequeued. An additional `respond` check alone would leave the CPU/slot hold and expired commit intact. Periodic checks provide a bounded cooperative interruption point; they cannot preempt a parser library's single noninterruptible call, so do not claim a hard real-time limit.

Required focused follow-up: the existing processing-pause/stage regressions plus a normal large-batch budget case in all processing modes; verify zero completed expired batches, no ACK, prompt slot/shutdown release, and unchanged atomic retention semantics. The parent owns implementing/rebuilding and any sustained benchmarking.

## P2 — Owned-storage peak omits simultaneous incoming scratch storage

Locations: `src/cpp/processing.cpp:43-80`, `:84-103`; `src/cpp/transport.cpp:156`, `:182`.

`peak_owned_storage_per_connection` is updated by sampling `Processor::owned_capacity()` from the scanner, only when it can acquire the connection mutex. In allocate mode, the next batch's owned rows/text are built while the old retained batch still exists. `apply` then frees the evicted storage before releasing that mutex. The scanner can never observe the two live allocations together, so its reported peak excludes a real scratch-plus-retained peak. Scanner sampling can additionally miss short post-commit capacity peaks for mixed-size input.

Observed with two equal one-row batches, each containing a 1000-byte decoded message, `retain-batches=1`, and otherwise equal settings:

| Mode | Completed batches | Reported canonical peak | Reported owned-storage peak |
| --- | ---: | ---: | ---: |
| retain-allocate | 2 | 1038 bytes | 1048 bytes |
| retain-reuse | 2 | 1038 bytes | 2096 bytes |

The observed fields are recorded in [capacity evidence](cpp-bugs/capacity-evidence.json); the reproducible check is [owned_capacity_probe.py](cpp-bugs/owned_capacity_probe.py). Source ownership establishes that allocate also has both 1048-byte batch capacities simultaneously before the second commit; this is a source-derived lower bound, not a claim of instrumented allocator accounting. The reported difference can mislead a memory comparison of the two retention modes. OS process-memory counters are separate and are not claimed to be incorrect.

Minimal correction: retain an explicit owned-capacity high-water value inside `Processor` at the existing scratch/commit transition, before old buffers are released, and report that value at cleanup. Include retained and scratch vector capacities; describe the metric's scope and exclude parser/allocator internals explicitly. Alternatively, rename and document the current field as a sampled post-commit capacity rather than a peak, but the intended actual-capacity comparison then remains unavailable. No allocator interception or new abstraction is warranted. The analogous managed metric is also read after processing and merits the same scope audit by its owner.

## Other reviewed boundaries

- The previously fixed data deadline, failure-demand accounting, cohort finish, stage sampling and no-late-ACK guards were traced; their existing regression suites were not blindly rerun.
- No new concrete IOCP use-after-free or socket/handle leak was established. Registry removal still waits for closed state plus no pending completion, with shared references protecting worker/scanner access. This is source review, not a race-freedom proof.
- The ten-million-arrival Poisson/burst storage limit and one-billion-record network epoch limit are already disclosed workload ceilings. They were not relabeled as new findings. The parent must choose supported sustained workloads or explicitly change the contract.
- Native allocation-failure/thread-creation injection, sanitizer churn, and sustained trials were outside this pass. No claim of their completion is made.

All probes rechecked the frozen executable hash after running. No original review evidence was overwritten. The three scripts now accept `--exe` and `--output-dir`; their default recheck destinations are fresh timestamped folders and existing output directories are refused. The ordinary-processing probe defaults to the observed 30 ms case and accepts `--deadline-ms 10` for the earlier control. These output-routing changes were syntax-checked without rerunning the workload.

## Independent post-fix verification — 2026-09-29

**Result:** both established findings are resolved in this focused pass. No new concrete defect was found in the changed processing-budget, cancellation, retention-commit, or owned-capacity paths.

The native owner confirmed its source edits, Release build, and timing-sensitive suites were finished before this reviewer ran any workload. The executable tested independently was SHA-256 `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329`. Every probe checked the hash before and after. This reviewer did not edit application source or build anything. Fresh evidence lives under [postfix-7e195792](cpp-bugs/postfix-7e195792/), with the initial grouped results in [summary.json](cpp-bugs/postfix-7e195792/summary.json). Historical failures were preserved.

### Observed results

| Check | Result |
| --- | --- |
| Diagnostic 900 ms pause, 100 ms frame/idle budget | Socket closes after 102.5 ms; replacement connection accepted; zero completed frames and zero excess-connection rejections |
| Partial-body timeout control | Socket closes after 103.5 ms; replacement accepted; zero completed frames |
| Shutdown 50 ms into the diagnostic pause | Process exits 38.0 ms after stop; zero completed frames |
| Two identical retained batches, one retained slot | Both allocate and reuse now report owned-capacity peak **2096 bytes**, canonical-retention peak **1038 bytes**, and two completed frames |
| Quiet interval of 150 ms after successful data with 100 ms frame/idle limits | EOF and a new End request both succeed in both retained modes: **four cases**, each with one graceful connection and no rejection; End summary checked against the independent oracle |
| Native selftest on the same executable | Passes, including epoch/retention preservation under already-expired and stop-requested budgets, strict-invalid scratch rollback, retained digest revisit, and scratch-capacity high-water checks |

Ordinary large-batch probes use the original valid 15,640,001-byte / 170,000-row input without diagnostic pauses. All three modes have a case where the server's received-byte count equals the entire Begin plus data frame (**15,640,065 bytes**), followed by timeout closure without a committed frame or record:

| Mode | Frame limit | Closure observed from client send start | Completed frames / records | Evidence |
| --- | ---: | ---: | ---: | --- |
| aggregate | 40 ms | 44.5 ms | 0 / 0 | [aggregate full-body case](cpp-bugs/postfix-7e195792/aggregate-40ms/evidence.json) |
| retain-reuse | 30 ms | 31.6 ms | 0 / 0 | [reuse full-body case](cpp-bugs/postfix-7e195792/processing-nodelay/evidence.json) |
| retain-allocate | 30 ms | 31.4 ms | 0 / 0 | [allocate case](cpp-bugs/postfix-7e195792/processing/processing-evidence.json), [server metrics](cpp-bugs/postfix-7e195792/processing/processing-retain-allocate-30ms.json) |

The allocate case also records 1,310,880 bytes of partially built owned scratch, directly establishing processing entry before cancellation. Earlier aggregate/reuse attempts expired during receive; those results remain preserved and are not claimed as evidence of CPU-loop interruption. The targeted follow-ups enabled client `TCP_NODELAY` and recorded received-byte counts explicitly; aggregate used 40 ms so its full body arrived before expiry. No success ACK was observed in the expiring cases. These elapsed times are bounded diagnostic observations, not comparative performance data.

### Source paths checked

- `ProcessingBudget` at `bench.hpp:206` checks the atomic shutdown flag and the optional absolute deadline. `Connection::processing_budget` at `transport.cpp:210` selects the earlier frame/idle deadline and leaves the deadline absent while `started == 0`.
- `parse` checks the budget every 1024 rows (`processing.cpp:112`); `visit` checks every 1024 rows and on completion (`:244`, `:253`). The same visit implementation covers incoming materialized rows and retained batches being verified for eviction.
- `apply` checks on entry (`processing.cpp:265`) and immediately before retained mutation (`:311`) or aggregate/transport epoch commit (`:338`). All potentially cancelled eviction verification precedes actual slot release, live-list mutation, retention counters, and epoch updates. Once commit starts, there is intentionally no cancellation point splitting retention from epoch state. This preserves atomic eviction semantics; it is not a claim that operating-system scheduling cannot delay those final bounded operations.
- `verify` checks entry and exit and passes the budget through retained visits (`processing.cpp:344`). End supplies its new active-frame budget (`transport.cpp:336`); quiet EOF supplies a shutdown-only budget (`:403`). The four quiet-control probes verify that neither path reuses the previous frame's expired deadline.
- The diagnostic pause checks the same budget between waits of at most 10 ms (`transport.cpp:304`), preserving the existing connection mutex and pending-IOCP ownership rules.
- Capacity high-water tracking occurs before evicted storage is released and in the parse exception path (`processing.cpp:280`, `:289`), so failed partially materialized scratch is included. Both normal and fatal cleanup consume `processor.peak_owned` (`transport.cpp:640`, `:735`). The output now states that this is row/text vector capacity including retained and scratch storage, excluding parser storage, allocator metadata, and transient reallocation overlap.

### Scope limits

The successful selftest and source ordering establish the checked atomicity paths, but this reviewer did not inject cancellation at a chosen internal eviction row or force End to time out midway through verification. End/EOF budget propagation was inspected, and their quiet-connection lifecycle was exercised independently. Cooperative checkpoints do not preempt one simdjson call, one bounded record operation, or the final atomic commit; no hard real-time guarantee is claimed. ASAN and the broader native/shared suites were run by the native owner, not repeated by this reviewer. No sustained benchmark was run.
