# Round 2: independent C# post-fix recheck

**No concrete finding remains open in the scope checked.** The processing deadline and owned-capacity fixes pass against frozen DLL SHA-256 `cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd`. No production source/build artifacts were changed or rebuilt. Historical failing evidence remains intact.

Commands:

```powershell
python reviews/round2/csharp-bugs/postfix/verify.py
dotnet run --project reviews/round2/csharp-bugs/postfix/reflection/Probe.csproj --configuration Release -- src/dotnet/bin/Release/net11.0/Bench.dll
```

The second command builds only an isolated reflection harness. Its generic delegate invokes the processing method in the frozen DLL; it does not copy/recompile production processing source or reference the benchmark project.

## Passed checks

- **Ordinary CPU expiry:** all three JSON modes receive the full valid 15,810,001-byte / 170,000-row batch, reject without ACK or committed completion, and close in 104.6–105.5 ms for a 100 ms deadline. Each immediately accepts a new healthy connection into the sole slot.
- **Diagnostic pause and shutdown:** all three JSON modes close a 900 ms pause at 110.9–118.3 ms for the 100 ms deadline, with completed=0 and a single deadline error; slots recover. Shutdown during a 10-second pause completes in 43–60 ms with no committed batch. Transport mode separately respects the 100 ms idle deadline despite a 1,000 ms frame deadline.
- **Simultaneous owned capacity:** both retained modes report the exact expected 3,536-byte high-water for one live plus one incoming owned batch, not merely post-eviction live storage. A malformed incoming batch that has already grown scratch to 262,144 bytes raises the reported peak to exactly 263,912 bytes, including the 1,768-byte live batch. The failed frame does not increase completed count.
- **Active End versus quiet EOF:** both retained modes accept End after waiting 1.05 seconds beyond the previous frame's 1-second deadline, return a verified summary of two frames/two records, and handle half-close after another 1.05 seconds without a stale-deadline error. Source inspection confirms End receives its current `ProcessingBudget`, while quiet EOF uses deadline zero. These network tests exercise valid End verification; they do not force expiry partway through a large End verification.
- **Cancellation before eviction commit:** an isolated reflection probe in both retained modes retains a small batch followed by a 170,000-row batch, then requests cancellation while validating the two evictions required by a two-row replacement. It verifies the incoming visit completed, cancellation was observed, retained object identity/order and canonical bytes did not change, and the complete epoch state stayed byte-for-byte equivalent under serialization. Retained digests verify afterward; retrying the same sequence without cancellation succeeds, leaving one 78-byte canonical batch. Observed cancellation calls completed in 2.8645 ms and 1.067 ms. Source audit confirms all cancellable visits/checks precede the uninterrupted eviction/epoch commit.

Network evidence: `reviews/round2/csharp-bugs/postfix/evidence-1790668587479207900/evidence.json` plus 14 individual server outputs/logs. Reflection evidence: `reviews/round2/csharp-bugs/postfix/reflection/evidence.json`.

## Additional client ownership/refactor audit

Inspected the final `Load.SendLoop` publication boundary: sequence/header, immutable corpus frame and local `firstSend` are captured before placing the descriptor in the ACK queue. After publication the sender uses only its header buffer, local timestamp and captured frame, and awaits completion of both sends before reusing the header. The ACK owner reads all descriptor fields before `admission.Return`; no sender reread of recycled descriptor fields remains. This audit covers the correction without adding a timing hook.

The fixed histogram arrays each contain five distinct preallocated histograms. `PhaseResult.Merge` preserves all prior scalar/histogram fields and merges every size class by the same index, after `Task.WhenAll` has ended worker ownership. Keyed and ordered JSON aliases use the same exported array in index order. `RejectUndispatched` retains the prior planned-minus-offered calculation, aborted/late split, round-robin offset/count arithmetic and cursor advancement before adding the remaining offered/rejected demand. No new defect was found. Existing final-build histogram, faulty-peer and cross-language smoke checks were reported passing by the parent/quality agent and were not redundantly rerun here.

The first reflection harness attempted a 5 ms timer cancellation, but verification completed before the timer fired; that was an inconclusive probe, not a product defect. The final harness uses one joined thread with a 1 ms local monotonic spin to request cancellation and checks it occurred after the incoming visit. It adds no production hook or instrumentation.

These are bounded correctness/lifecycle checks, not sustained performance measurements. Cooperative checks occur every 1,024 rows; individual JSON tokens, allocations and the final commit are not preemptible. The observed response times are not hard real-time bounds. The existing schedule-storage, one-billion-record network-epoch and resource-scope limitations recorded in the original review remain applicable.
