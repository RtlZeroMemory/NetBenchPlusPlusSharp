# Round 2: C# documentation and GitHub README review

Scope: root `README.md`, `src/dotnet/README.md`, and consistency with implementation, contract, scripts, and review evidence. No production source or benchmark results were changed by this reviewer.

## Findings and corrections

| Finding | Correction |
| --- | --- |
| Root README mixed the newcomer path with lengthy protocol and measurement details. | Added build/smoke/report quickstart and manual cross-language exchange, with reference links for details. |
| Prerequisites omitted 64-bit Python and the physical-core minimum. | Documented default six-core minimum, three-core alternative, native Windows, and single-processor-group scope. |
| Old claim that all long suites used 2 GiB was inaccurate for pilot. | Added a per-suite defaults table, including updated 256 MiB pilot/first defaults. |
| Manual example omitted server shutdown and final metrics. | Documented Ctrl+C after client completion and added server output path. |
| .NET README's root-relative contract path was not navigable from its directory. | Added working relative GitHub links to shared documentation, settings, and review evidence. |
| Diagnostics, scheduling, storage, and output scopes were hard to scan. | Grouped them by purpose and added concise option/resource tables, preserving correctness and adequacy caveats. |
| Processing-only duration did not explain full-corpus cycles. | Documented that a cycle completes before checking elapsed duration and can overrun the target. |
| Correctness evidence could be mistaken for performance evidence. | Separated results status from checks; no ranking, GC causality, overnight confirmation, or memory plateau is claimed. |

## Verification

Read pinned SDK/runtime files, C# responsibility groups, runner/report arguments and wrappers, corpus generator, protocol/measurement/differential tests, focused managed regression, contract, and historical review log. Invoked Python runner/report/protocol/differential `--help` to check public arguments. Build paths and binary command forms match the build scripts. No sustained benchmark was run during this review.

Rechecked the completed runner and PowerShell wrapper: both expose `first`; first/pilot defaults are 30 seconds measured, 5 seconds warmup, and 256 MiB. Checked all 20 local Markdown links in the two READMEs and balanced their code fences. Comparative numbers remain pending until actual results exist. The final C# module map should be added after the quality refactor lands; proposed names are Corpus.cs, Reporting.cs, and Scheduling.cs alongside existing files.

## Remaining reference cleanup

The shared contract contains implementation-owner instructions and historical future-tense notes about Poisson support. Its short `arrival=steady|poisson` description omits implemented `burst`, described elsewhere. The contract owner should remove internal coordination prose and consolidate current behavior before presenting it as the public reference. These edits are outside this reviewer's file ownership.
