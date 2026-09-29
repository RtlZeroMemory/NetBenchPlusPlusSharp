# C++ protocol and resource ownership review

Initial adversarial review, 2026-09-29. Production source and frozen binaries were not modified or rebuilt. Reviewed `docs/contract.md`, the design requirements, and `src/cpp/bench.hpp`, `main.cpp`, `processing.cpp`, `transport.cpp`, and `load.cpp`.

One verified timing/correctness finding remains. No additional parser-result or resource ownership defect was demonstrated by the completed checks. This is not a clean sanitizer or concurrency-stress sign-off.

## P1 — Processing holds the lock needed to enforce application deadlines

**Verified locations:** `src/cpp/transport.cpp:127` holds the connection mutex through `completed()` and synchronous frame processing. `src/cpp/transport.cpp:140` waits for that same mutex before the sole deadline check at `src/cpp/transport.cpp:143`. Processing at `src/cpp/transport.cpp:79` can then publish a success response at `src/cpp/transport.cpp:84` without checking elapsed time. When the response completes, `src/cpp/transport.cpp:98` resets the deadline timestamps.

The contract starts the absolute frame deadline with the first header byte and also requires pending response writes to be bounded. Blocking the scanner on processing allows a frame to finish after its configured deadline and receive an ordinary success ACK.

**Existing reproduction command, from the workspace root:**

```powershell
python reviews/cpp-protocol/probe.py deadlines
```

The probe starts the frozen `src/cpp/build/Release/tcpbench.exe` on its own ephemeral port with `--frame-timeout-ms 100 --idle-timeout-ms 100 --pause-every 1 --pause-ms 500`, performs Begin, and sends a valid `[]` JsonBatch. It stops the server through its stdin control pipe after collecting the result.

**Expected:** the 100 ms frame deadline prevents a successful acknowledgement of the request after the 500 ms processing delay; the connection closes with a deadline rejection. Enforcement need not be exact to the millisecond, but it must not silently disappear for the entire processing interval.

**Actual:** the client received the correct success ACK `(0, 14695981039346656037)` after **0.5085901000056765 seconds**. Server output reported `completed_frames=1`, `graceful_connections=1`, `valid=true`, and `rejection_reasons={}`. Thus this is a completed request after its application deadline, not a malformed JSON classification or bad digest.

**Evidence:** `reviews/cpp-protocol/deadline-Release.json` and `reviews/cpp-protocol/deadline-Release-aggregate-56654.log`.

The diagnostic pause at `src/cpp/transport.cpp:77` makes the defect reproducible. Ordinary decoding/retention at line 79 runs under exactly the same mutex, so a slow processing interval or scheduling pause can expose the same enforcement gap. That ordinary slow-parse case was not independently timed in this review.

There is also a static liveness implication: the scanner visits connections serially, and its blocking lock at line 140 prevents it from reaching subsequent connections, accepting further sockets, or checking shutdown input until the busy connection releases the lock. This follows from the source path; an unrelated-connection socket experiment was not completed and is not presented as additional runtime evidence.

The fix should enforce expiry before publishing a successful response and let the scanner continue past a connection that is processing. No data race, use-after-free, or ASAN failure is claimed by this finding.

## Completed executable coverage

```powershell
python reviews/cpp-protocol/probe.py semantics --mode aggregate
python reviews/cpp-protocol/probe.py semantics --mode retain-reuse
python reviews/cpp-protocol/probe.py semantics --mode retain-allocate
```

Each command used a distinct ephemeral port and the frozen Release executable. The fixture classifications and results were checked against the independent Python oracle in `tools/corpus.py`.

| Mode | Valid fixtures | Invalid fixtures | Mismatches |
| --- | ---: | ---: | ---: |
| aggregate | 143 | 1071 | 0 |
| retain-reuse | 143 | 1071 | 0 |
| retain-allocate | 143 | 1071 | 0 |

Total: **3642** fixture exchanges. Valid requests were checked for record count, canonical digest, and the complete End summary including every bucket count and sum. Invalid requests were required to close without a success response.

Cases covered separator deletion/replacement, control bytes outside JSON strings, trailing roots/content, integer syntax and range boundaries across all numeric fields, unsigned negative zero, valid signed negative zero, invalid UTF-8, surrogate escapes, decoded message-length limits, escaped names, invalid property names, and randomized property ordering. Each fixture used a fresh connection; these new probes do not independently establish retained eviction/lifetime behavior across repeated batches.

Evidence is saved in `reviews/cpp-protocol/semantic-Release-aggregate.json`, `semantic-Release-retain-reuse.json`, and `semantic-Release-retain-allocate.json`, with their corresponding server logs. Each server completed 143 data frames and closed 143 connections gracefully; all result files show an empty mismatch list and `valid=true`.

## Static resource ownership review

The ordinary IOCP path keeps one outstanding operation per connection, sends immediate and pending successes through the completion port, and keeps `pending` true after cancellation until the corresponding completion is observed (`transport.cpp:53`, `transport.cpp:62`, `transport.cpp:66`, `transport.cpp:88`). Registry removal requires both closed state and no pending operation (`transport.cpp:144`); workers and the scanner retain shared ownership while accessing the object. No concrete lifetime defect was established in that path.

The processor consumes required fields, checks unescaped duplicate names, checks integer token spelling before conversion, and checks the end of the document (`processing.cpp:47`, `processing.cpp:51`, `processing.cpp:58`, `processing.cpp:71`). Retained text is copied into owned storage (`processing.cpp:68`), validation uses a scratch slot distinct from committed live slots, and epoch checks precede retained eviction/commit (`processing.cpp:85`). Those source paths are consistent with the tested parser results; this review did not inject allocation failures or corrupt retained state.

## Not completed

- ASAN reruns of these new probes, reset/cancellation churn, repeated start/stop stress, and slow-reader response-backpressure experiments.
- A timed unrelated-connection demonstration of the scanner stall, or a slow-processing demonstration without the diagnostic pause.
- Worker-creation and allocation-failure injection. `reviews/cpp-protocol/startup-memory.py` is an unexecuted preparatory script and supplies no test evidence.
- New cross-batch invalid-frame atomicity, retained eviction, or capacity-churn experiments. The parent reported existing selftest/protocol/loopback coverage; those checks were not independently rerun here.

Active testing stopped at the parent's instruction. The initial review is complete with the verified deadline finding above; the parent will implement and validate the fix.

## Focused post-fix review — 2026-09-29

**Result:** the initial P1 deadline/scanner finding is resolved by the reviewed transport changes. No unresolved concrete deadline or IOCP resource ownership defect was found in this focused pass. The initial observations and evidence above describe the pre-fix source and remain preserved.

Reviewed the corrected `src/cpp/transport.cpp` and ran only the existing bounded loopback regression suite:

```powershell
python src/cpp/regressions.py --exe src/cpp/build/Release/tcpbench.exe
```

The command exited **0** and printed:

```json
{"regression": "processing-deadline-and-independent-progress", "passed": true}
{"regression": "stage-pause-offset-reset-and-tail-gates", "passed": true}
```

The existing regressions select separate ephemeral loopback ports. Coverage now independently establishes:

- A 500 ms diagnostic processing pause with a 100 ms frame deadline produces no successful ACK, no completed data frame, and a frame-timeout rejection.
- While that connection processes, an unrelated connection can be accepted and complete Begin within the suite's 300 ms bound; its partial-header timeout also completes within 300 ms. At least two frame-timeout rejections are required.
- Two epochs of 1024 acknowledged retained-mode batches each produce one sample in the final epoch, reset stage state correctly at Begin, include the 50 ms pause in processing elapsed time, and suppress stage p99/p99.9 below their sample gates. End verifies the per-epoch batch total.

Run output is preserved separately in `reviews/cpp-protocol/post-fix-0418ab4d-regressions.log`. Historical probe scripts, JSON results, and server logs were not overwritten. No production source was edited by this reviewer.

### Reviewed hashes and scope

These SHA-256 hashes were identical immediately before and after the regression run:

| Artifact | SHA-256 |
| --- | --- |
| `src/cpp/build/Release/tcpbench.exe` | `0418AB4D3A0106A260FCA892D2E037D5CD509C3BBCBD9C703C271160F2148386` |
| `src/cpp/transport.cpp` | `DCD6ADFF0AAF72212ACDFCD496EEB149EF8260236A77C5367ED01C30FD6E8771` |
| `src/cpp/regressions.py` | `121225343353D097267D64E7C90927799F4C57072B37AFDFDFF874BBCB54093C` |

The executable hash identifies the **intermediate build actually tested**, not the final project freeze. After the run, the native owner and parent disclosed a pending `load.cpp` client lifecycle alignment and confirmed that the reviewed transport component remains unchanged. The parent will record the final executable freeze in `reviews/review-log.md`; an independent measurement review covers that client change. The parent explicitly requested no repeat of identical server regressions solely for the client-only rebuild.

After the native owner announced the final rebuild, a read-only hash check confirmed Release SHA-256 `62773A9936616BC8D0A0B3ABFADE96BB904EF70FBA447B0FB8F0697FDA265F79`. The transport source and regression script hashes still match the tested hashes above. This is a provenance check, not a claim that this reviewer reran the suite against the final executable.

### Corrected timeout/cancellation path

`Connection::expired()` centralizes deadline/shutdown closure at `src/cpp/transport.cpp:56`. The processing path checks it before work (`transport.cpp:84`) and after the diagnostic pause (`transport.cpp:91`); the response path checks it before publishing output (`transport.cpp:79`), and issue/completion boundaries check it as well (`transport.cpp:65`, `transport.cpp:102`). Ordinary decoding or retention therefore reaches the same response expiry guard even when its own processing interval runs long.

The scanner uses a nonblocking connection lock at `src/cpp/transport.cpp:155`, allowing unrelated connections and shutdown input to progress. The atomic stopping flag (`transport.cpp:36`, `transport.cpp:152`) lets a busy worker observe shutdown without the scanner acquiring its processing lock. Fatal cleanup also snapshots registry ownership and skips busy locks (`transport.cpp:175`, `transport.cpp:176`).

Cancellation still preserves `pending` until the completion is dequeued (`transport.cpp:54`, `transport.cpp:101`), and registry removal still requires `closed && !pending` (`transport.cpp:158`). Expiry is evaluated after observing a completion and before reusing buffers or issuing subsequent work. Worker and snapshot shared references continue to own the connection through their accesses. These changes do not create an observed early-reclamation path.

This post-fix pass did not run ASAN, new attack probes, allocation-failure injection, or sustained churn. It signs off the focused timeout/scanner correction and the stated Release regression coverage, not a complete project or sanitizer freeze.
