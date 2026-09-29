# Native round 2 fixes and verification

Frozen after the checks below. No sustained benchmark was run by this agent.

| Artifact | SHA-256 |
| --- | --- |
| Release executable | `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329` |
| ASAN executable | `35b24a0211132dd15d2af752a7a2c5d3b1afb7b1cfe9919e53379ec1b252b820` |

Full source, test, formatter, and binary hashes are in [hashes.json](hashes.json). Pre-edit application source is preserved under [original](original/); this does not include or replace historical binaries or review evidence.

## Changes

- All five application C++ files use the repository clang-format configuration, with ordinary spacing, expanded braces and compound statements, and named phase worker bodies. Vendor/generated source was excluded. No new application module or dependency was introduced.
- `ProcessingBudget` is an optional value object with an absolute deadline, timeout label, and pointer to the server's atomic stop flag. Parsing and retained-row digest loops check every 1024 records; eviction verification shares the budget. The final check precedes all retained-state mutations and epoch commit. End and graceful-EOF verification also check. A quiet connection has no stale frame deadline. IOCP's shared registry ownership and pending-completion rule remain intact.
- Diagnostic processing waits sleep in at most 10 ms slices and observe the same deadline/stop budget. They no longer hold a connection slot for the entire configured pause after expiration or shutdown.
- Native owned-storage high-water reporting records row/text vector capacities while committed batches and incoming scratch are simultaneously alive, before eviction frees old buffers. Failed scratch parsing also records capacities before unwinding. The existing result key is preserved; `owned_storage_scope` states that parser storage, allocator metadata, and transient old/new reallocation overlap are excluded.
- Arrival construction, schedule counts and abandoned-demand cursor arithmetic have named file-local helpers. Sender/ACK worker lambdas and server completion workers are named. The server stdin-control polling block is separate. Existing bounded pools, locks and allocations remain, with short ownership comments.
- Removed redundant initial client socket timeouts and unused no-deadline defaults; each blocking operation still sets its remaining absolute deadline. Effective socket buffer queries share one function. Direct standard includes replace transitive dependencies, and unused includes were deleted.

## Verification

Release and ASAN both passed:

| Check | Result | Evidence |
| --- | --- | --- |
| Existing plus expanded selftest | Canonical/schema/lifetime checks, cancelled-budget atomicity, scratch and failed-scratch peaks, 75,600 cursor combinations, 20 schedule-count cases | [Release](release-selftest.log), [ASAN selftest inside native check](asan-check.log) |
| Native check | Four processing controls and 16 loopback variants, including forced partial I/O and all arrival shapes | [Release](release-check.log), [ASAN](asan-check.log) |
| Native regressions | Eight cases: independent deadline progress, stage sampling, three ordinary large-batch budgets, pause expiration/shutdown, capacity plus quiet EOF | [Release](release-regressions.log), [ASAN](asan-regressions.log) |
| Shared protocol | 236 checks | [Release](release-protocol.log), [ASAN](asan-protocol.log) |
| Shared client measurement | All ten cases, including withheld/corrupt ACKs and an honest ACK inside the full drain deadline | [Release](release-measurement.log), [ASAN](asan-measurement.log) |
| Compiler build | No application warnings/errors reported | [Release](release-build.log), [ASAN](asan-build.log) |
| clang-format dry run | Exit 0, no diagnostics | [Format check](format-check.log) |

The ordinary 170,000-record deadline cases expired in all three processing modes with no ACK and zero completed batches in both builds. Release client-observed closure was about 36–41 ms with a 30 ms server frame budget. ASAN was about 92–98 ms. These observations are diagnostics, not a hard real-time guarantee: cooperative checks cannot preempt a parser library call, memory allocation, vector growth, or OS scheduling. The final retention/epoch mutation deliberately does not check cancellation midway through the commit.

The two equal one-row retained batches now report a 2,096-byte capacity peak in both allocation and reuse modes, while canonical retained high-water remains 1,038 bytes. The test also waits beyond the previous frame deadline before graceful EOF and verifies the connection remains graceful.

Independent post-fix review and cross-language/sustained runs belong to the parent workflow. No full allocator accounting, race-freedom proof, or comparative performance claim is made here.
