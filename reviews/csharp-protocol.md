# C# protocol, semantic, and lifetime review

Reviewed the frozen .NET binary and `docs/contract.md`, relevant benchmark-plan requirements, and all five C# source files. No production/shared files were edited and no binaries were rebuilt.

Frozen `src/dotnet/bin/Release/net11.0/Bench.dll` SHA-256: `73489b69f86f49137b14fa8699ebf7929f4d9632e4937492e90deec55f0b4764`.

## Findings

### P2 — Processing-only runs omit category-array integrity verification

Verified location: [`src/dotnet/Load.cs:413`](https://github.com/RtlZeroMemory/NetBenchPlusPlusSharp/blob/263e1a6b16ed4ecedcceeace7cceb52d20b72457/src/dotnet/Load.cs#L413), with unconditional successful output at line 422.

`Load.Process` checks each batch's record count and canonical digest against corpus metadata, but never compares the 64 category counts or signed sums. This permits a structurally valid corpus with wrong category expectations to produce a successful processing control result. The socket client and corpus-enabled selftest do verify those arrays, so the processing control has weaker integrity checks than the other modes. The contract requires processing-only mode to use the same functions/checks, and the plan requires full category arrays to be checked independently of digest equality.

Minimal reproduction (from `F:\SocketServerClient`):

```powershell
python reviews/csharp-protocol/probe.py
dotnet src/dotnet/bin/Release/net11.0/Bench.dll process --corpus reviews/csharp-protocol/wrong-bucket.bin --duration .001 --warmup 0
```

The fixture contains one valid row with `kind03`, `flags=7`, `value_milli=-12345`, and `message="x"`. Its payload, record count, and canonical digest are correct. Its category metadata moves count `1` and sum `-12345` from bucket 15 to bucket 0. Both the total count and per-bucket numeric bounds remain structurally valid.

Expected: reject the category mismatch, return nonzero, and never report `valid:true`.

Actual: `process` returns 0 with `valid:true` and `success:true` in `aggregate`, `retain-reuse`, and `retain-allocate`. As a negative control, `selftest --corpus reviews/csharp-protocol/wrong-bucket.bin` returns 2 with `end_summary_mismatch` on the same binary and fixture.

Evidence: `reviews/csharp-protocol/wrong-bucket-all-modes.json`, `wrong-bucket-aggregate.log`, `wrong-bucket-retain-reuse.log`, and `wrong-bucket-retain-allocate.log`.

Smallest correction: reuse the existing expected `Summary.Add`/`Summary.Verify` pattern from the corpus selftest in processing-only cycles, with matching resets when the processor resets an epoch. Keep the wrong-bucket fixture as a regression: all three JSON processing modes must reject it. This is an integrity-check defect; the evidence does not establish that the current category calculation is wrong for valid metadata. Existing performance results are not individually shown incorrect by this fixture, but processing-control runs lack the required category validation gate.

## Executed checks

`python reviews/csharp-protocol/probe.py` passed **473 socket/semantic/lifetime assertions** and reproduced the integrity defect. Ports were allocated independently with ephemeral binds; no port 9000 server was used.

- **363 semantic checks** across aggregate and both retained modes: raw UTF-8 boundaries, overlong encodings, surrogate encodings, values above U+10FFFF, truncated multibyte sequences, invalid bytes mixed with escapes, invalid property-name UTF-8, malformed surrogate-pair escapes, valid U+10000/U+10FFFF/noncharacters/BOM inside strings, escaped property/kind equivalence, decoded UTF-8 message lengths exactly 4096 and above, numeric lexical/range corners, long integer tokens, and non-JSON whitespace. Valid batches were checked against the independent Python oracle and complete End summaries; invalid batches received no success ACK and did not enter server completed totals.
- **44 lifetime checks**: body trickle below the inactivity limit still hits the absolute deadline; 30 body-reset connections release admission; subsequent valid traffic succeeds; shutdown drains 12 simultaneous quiet, partial-header, and partial-body reads without hanging.
- **66 final edge checks**: the independent `tests/protocol.py::run_protocol` suite under server `--io-cap 1` (63 checks), atomic retention-capacity rejection in both retained modes, and blocked response writes with a client that stops reading ACKs. The blocked-write test produced the expected bounded `deadline` rejection.
- All three processing modes accepted the wrong-bucket fixture; corpus-enabled selftest rejected it. Results are saved under `reviews/csharp-protocol/`.

Source tracing found no additional concrete defect in exact-read/send advancement, EOF/half-close behavior, epoch/sequence gates, strict full-document JSON validation, staged aggregate commit, owned retained text/rows, eviction verification, or asynchronous buffer cleanup. Retention bounds are explicitly canonical bytes; owned-array capacity and one spare batch are separately disclosed. This pass did not perform a 30-minute memory soak or claim absolute absence of races. No speculative issues are raised as blockers.

## Independent post-fix recheck — scoped pass

The P2 category-oracle finding is **resolved** in frozen .NET SHA-256 `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`. The original report, fixtures, probes, and logs above were preserved.

Verified current locations: `src/dotnet/Load.cs:408` defines the shared `ProcessCycle`; line 417 checks categories after each count/digest check. The warmup path calls it at line 425 and the measured path at line 428. `src/dotnet/Processing.cs:147` defines `VerifyCategories`, using the existing per-batch `counts` and `sums` arrays with span `SequenceEqual` at line 149. This avoids extra per-frame allocation and summary serialization.

Recheck command:

```powershell
python reviews/csharp-protocol/postfix.py
```

**30 independent executable checks passed** across `aggregate`, `retain-reuse`, and `retain-allocate`, each with warmup 0 (corruption reaches the measurement cycle) and warmup 0.02 seconds (corruption reaches the warmup cycle):

- **24 corrupt-category checks**: the preserved original wrong-bucket fixture, wrong counts alone with zero values, wrong signed sums alone, and a late sum mismatch after a valid materialized batch and an empty batch. Every case returned 2 with `valid:false` and `process_oracle`. All altered metadata passed the existing structural constraints; payloads, canonical digests, and record counts remained valid.
- **6 valid controls**: a 12-frame corpus alternating empty/nonempty batches, different categories, signed values, flags, and owned Unicode messages. Each control returned 0 with `valid:true` and `success:true`, using a two-batch retention limit to exercise repeated eviction/reuse across warmup and measurement.

New fixtures, separate per-case logs, and `results.json` are under `reviews/csharp-protocol/postfix-60899c2c/`; the script verifies the final binary hash before and after execution. No production files were edited and no binary was rebuilt in this recheck.

The category helper only reads existing owned batch arrays synchronously. It does not retain spans, change their lifetime, mutate parser scratch, or alter commit/retention behavior. The parser and owned row/text paths are unchanged by this fix. No unresolved finding or regression was reproduced in the corrected category-validation path. This is a focused recheck; the 473 historical checks are not represented as having been rerun against this hash, and the other post-fix measurement/deadline changes are outside this recheck's scope.
