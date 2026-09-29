# Round 2: C# correctness and lifecycle review

Reviewed the frozen C# transport, processing, load generation, CLI/resources, contract, prior review evidence, runner, report gates, and the corresponding native transport deadline path. No production source or benchmark build artifact was changed. Tests used ephemeral loopback ports only.

Frozen DLL SHA-256: `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`.

## Finding: [P2] Frame deadlines stop applying during processing

Locations: `src/dotnet/Transport.cs:59`–`61`, `153`–`183`; processing loop in `src/dotnet/Processing.cs:159` onward.

After the complete body arrives, `Wire.ReadExact` disables its timer. The server's diagnostic processing delay observes only the server shutdown token, and ordinary parsing/digest/retention has no frame-deadline check. `Wire.Send` eventually checks the absolute deadline, so the server correctly withholds a late success ACK. However, an expired request keeps its socket, receive/retention state, and active connection slot until all processing finishes. It can also be processed and counted as completed after the deadline already expired. This violates the stated expiry behavior of closing the connection and cancelling pending work, and makes recovery depend on the full processing/pause duration rather than the configured frame deadline.

### Reproduced evidence

```powershell
python reviews/round2/csharp-bugs/probe.py
python reviews/round2/csharp-bugs/cpu_probe.py cpu-deadline-early 0.13
```

Both use `--max-connections 1 --frame-timeout-ms 100 --idle-timeout-ms 100`.

| Case | Observation | Slot recovery |
| --- | --- | --- |
| Incomplete body control | Connection closes after 103.9 ms; completed=0 | Second Begin accepted |
| Valid `[]`, diagnostic 900 ms processing pause, three fresh server processes | Connection remains open at 357–360 ms; closes after 908–911 ms; no success ACK; completed=1 | Second Begin at the observation point rejected in all three cases |
| Ordinary valid 15,810,001-byte / 170,000-record JSON batch, no diagnostic pause | Body sent in 5.9 ms; connection remains open at 145.7 ms and closes at 214.1 ms; no success ACK; completed=1 | Second Begin at 145.7 ms rejected |

The initial ordinary-processing repetition also closed at 210.3 ms, with its body sent in 3.5 ms. Its later second connection was accepted after the first had already closed. These are lifecycle measurements on a cold local process, not performance comparisons.

Evidence: `reviews/round2/csharp-bugs/evidence.json`, `cpu-evidence.json`, `cpu-deadline-early-evidence.json`, and the corresponding server JSON/logs. The general probe hashes the DLL before/after and confirms it is unchanged. It retains a runnable incomplete-body control as well as the three reproductions.

### Correction and regression scope

Keep the absolute deadline effective through the processing phase. Bound the diagnostic delay by the remaining deadline and avoid entering processing after expiry. Ordinary processing also needs bounded cooperative deadline/cancellation observation, or the public contract must explicitly describe its noninterruptible processing interval. Cancellation must preserve the existing staged-commit and retained-lifetime guarantees. Do not merely add another pre-send check: the current code already suppresses the late ACK.

A focused regression should require an over-deadline processing request to release its slot within a reasonable scheduling allowance, admit a fresh healthy connection, and issue no success ACK. Cover both diagnostic delay and an ordinary large valid batch; keep the partial-body timeout control. Repeat for retained modes if deadline checks are added to shared parse/visit/commit paths.

The native implementation shares the broader noninterruptible-processing limitation: `src/cpp/transport.cpp:90` sleeps while its connection mutex is held, and the scanner skips a busy mutex at line 155. Unlike C#, it checks expiry before entering ordinary processing after that sleep. This source comparison is not a native reproduction or a claim of language performance asymmetry. The earlier native fix successfully prevented late success ACKs and scanner-wide blocking; the narrower per-connection recovery issue remains separate.

## Sustained-run constraints, not new defects

- The standard 30-second workload is not blocked by any newly identified arithmetic or epoch-limit issue. This pass did not execute the sustained comparison campaign; the parent is responsible for that after fixes.
- Precomputed Poisson schedules reject `seconds × rate > 9,500,000`; burst preflight rejects `seconds × rate × 1.5 > 10,000,000`. These explicit limits are documented. At 1,800 seconds, they limit configured rate to about 5,277/s for Poisson and 3,703/s for burst. Steady arrivals avoid this schedule-storage bound.
- Network phases use one epoch per connection, capped at one billion records. At 1,800 seconds that permits about 555,556 records/s per connection on average, before considering uneven assignment or warmup's separate epoch. Processing-only mode already rolls its epoch over. Plan a high-throughput soak within the network cap, or add coordinated epoch rollover before claiming an uninterrupted longer trial.
- Resource scopes remain disclosed and unequal: C# client CPU/allocation snapshots cover measured-phase setup, controls and drain; native client resources cover the process lifetime, including warmup. Raw CPU/allocation fields must not be treated as directly comparable measured-record costs. This was already recorded in round 1 and is not a new finding.
- No additional reproduced defect was found in global admission arithmetic, corpus cursor advancement, early-abort demand accounting, full category verification, histogram/stage gates, or retained ownership during this source review. Prior broad suites were not rerun without a new hypothesis. Short probes do not establish memory plateaus or absence of long-run races.
