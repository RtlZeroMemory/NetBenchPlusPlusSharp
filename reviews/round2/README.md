# Second review and fix round

Six GPT-6 Astra reviewers, each at XHIGH reasoning effort, reviewed the two implementations. Each language received separate code-quality, correctness and documentation passes. Source edits followed the reviews; the correctness reviewers then independently rechecked the frozen fixes. Historical failing evidence is preserved with its original build hashes.

| Language | Quality and simplicity | Bugs and measurement | Documentation |
| --- | --- | --- | --- |
| C# | [Review](csharp-quality.md), [fixes](csharp-fixed.md) | [Review](csharp-bugs.md), [independent recheck](csharp-bugs-postfix.md) | [README audit](csharp-docs.md) |
| C++ | [Review](cpp-quality.md), [fixes](cpp-fixed/README.md) | [Review and recheck](cpp-bugs.md) | [README audit](cpp-docs.md) |

## Changes

- Formatted every maintained C++ file with the checked-in clang-format rules. Expanded packed statements, branches and declarations in both languages. C# result initializers are readable multiline objects. Vendor/generated files were excluded.
- Kept the existing five native modules. Split the managed client's unrelated corpus, reporting and pacing responsibilities into three small files; the managed implementation now has eight source modules. No runtime dependency, interface hierarchy or framework was added.
- Simplified fixed size-class storage to arrays and reused exported histogram objects in C#. Named native scheduling helpers and worker bodies; removed redundant socket timeouts and unused includes/default branches. Preserved queue ownership and native IOCP completion lifetimes.
- Extended frame/idle/shutdown budgets through JSON processing and retained verification. Checks occur before work, every 1024 records, and before commit. Diagnostic pauses observe the same budget. Quiet EOF never inherits an old frame deadline.
- Verified every planned eviction before mutating retained state. The short retention/epoch commit has no midway cancellation point. Expired work no longer commits an epoch or contributes to completed-frame counts.
- Corrected owned-storage high-water accounting to include incoming scratch alongside retained data, including failed-parse scratch growth. These fields measure owned row/text capacities, not total allocations or resident memory.
- Preserved failed runner trials with explicit errors and raw output. Reports exclude failed/unverified trials and separate different binaries and CPU allocations as well as different workloads. Added the sustained `first` suite and per-trial records/s, payload MiB/s and stage summaries.
- Rewrote the GitHub-facing README and both implementation guides with build/run instructions, source maps, metric scopes and experimental limits.

## Frozen builds

| Artifact | SHA-256 |
| --- | --- |
| C# Release DLL | `cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd` |
| C++ Release EXE | `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329` |
| C++ ASAN EXE | `35b24a0211132dd15d2af752a7a2c5d3b1afb7b1cfe9919e53379ec1b252b820` |

Both application formatter checks passed. The managed Release build has zero warnings/errors; native builds reported no application warnings/errors.

## Verification

| Check | Result |
| --- | --- |
| Shared strict protocol/oracle suite | 236 cases each for C#, native Release and native ASAN |
| Faulty-peer client measurement suite | All 10 cases on all three builds |
| Managed selftests | 289 golden-corpus checks and 1,819 smoke-corpus checks |
| Managed histogram reflection probe | 100,091 samples against the frozen DLL |
| Managed server regressions | 11 cases: deadlines, interruption, slot release, memory capacity and quiet EOF |
| Native checks | Selftest, four processing controls, 16 loopback variants and eight focused regressions in Release and ASAN |
| Independent post-fix bug reviews | See the linked reports for full-body CPU-expiry evidence, capacity checks, cancellation/commit scope, EOF/End checks and exact limitations |
| Parent cross-language smoke | 24/24 valid and generator-adequate trials; all four client/server combinations, three JSON workloads and both load models |
| Parent large-frame/arrival integration | 18/18 trials, including 1 MiB/16 MiB frames, both cross-language directions, Poisson and a full burst cycle |
| Parent held-out differential corpus | 200 batches / 828 records through each server in all three processing modes, matching the Python oracle |
| Runner/report harness | Deterministic corpus, truncation, inherited affinity, SMT separation, failed-trial retention, eligibility/identity gates and all 54 first-suite dispatches |

Local integration evidence: `results/round2-smoke/` and `results/large-frames-1790668663568871800/`. Per-language build/test logs and probe evidence are linked from the fix and bug reports. The original managed histogram probe project encountered stale generated assembly attributes when rebuilding; its existing independent executable successfully checked the final DLL. After the campaign, the auxiliary project was corrected to compile only its one source file, preventing generated evidence/output files from entering the source glob. The freshly rebuilt probe also passed all 100,091 samples against the unchanged benchmark DLL. The earlier failed tool-build log is retained.

These checks establish functional evidence. They do not establish hard real-time deadlines, absence of races or allocation-free operation.

## First sustained benchmark

The [completed first-pass report](../../docs/benchmarks/first-pass.md) contains 54 trials with 30-second measured windows and five-second warmups, using two approximately 256 MiB corpora. All trials reconciled, with zero failed/unresolved requests. The 24 primary trials and 12 alternate-client/transport controls passed comparison gates. All 18 scheduled-load trials failed the predeclared generator-lateness gate and remain excluded diagnostics. The report includes readable throughput/latency/memory tables, allocation/GC ranges, stage samples, controls and downloadable complete raw results.

The campaign used the frozen Release hashes above, after all builds, functional stress tests and reviewer probes finished. Overnight confirmation, long memory-plateau tests, instrumentation-cost checks and causal GC-suspension tracing remain separate experiments.

The [independent report audit](benchmark-report.md) verified the final tables, paired ratios, metric scopes, trial eligibility, binary identities and byte-exact compressed raw results. Wording corrections about managed worker policy and alternate-client causality were applied before publication.
