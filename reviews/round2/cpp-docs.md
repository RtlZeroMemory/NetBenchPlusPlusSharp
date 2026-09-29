# Round 2: C++ documentation review

Reviewed the five maintained C++ application files, CMake configuration, native check scripts, shared protocol and measurement scripts, corpus generator, implementation contract, previous review evidence, and available round 2 reviews. Scope: `src/cpp/README.md` and this report. No application, build, or benchmark artifacts were changed by this documentation pass.

## Findings and documentation changes

| Priority | Finding in the previous README | Correction |
| --- | --- | --- |
| P2 | “Exactly one pending operation per connection” incorrectly includes processing intervals, when no I/O is pending. | Describe **at most one** pending IOCP operation, with connection/buffer ownership retained until its cancellation completion is observed. |
| P2 | “CPU, memory peaks, and syscall counts are process-lifetime” obscures current memory snapshots and implies complete syscall instrumentation. | Distinguish lifetime CPU/peaks and socket operation counters from current memory snapshots, measured client counters, and final-epoch stage histograms. Send/receive counts do not include every system call. |
| P2 | Deadline wording can be read as prompt cancellation of all CPU work. Round 2 identified noninterruptible processing and diagnostic pauses; the existing response guard only prevents a late ACK. | Keep the shared deadline contract linked and avoid claiming immediate or hard real-time cancellation. Parent owns the processing-budget fix and final implementation wording. |
| P2 | Retention text does not clearly separate canonical payload accounting, actual row/text vector capacity, per-connection peaks, and total process memory. | Name the reported scopes and explain that parser/input buffers and allocator overhead are outside row/text capacity. Parent owns the newly reviewed capacity-peak correction. |
| P3 | Build, diagnostics, implementation details, and metric interpretation appear as one long sequence; no native run example or module map is provided. | Add short build/check, run, architecture, measurement, diagnostic, and formatting sections. Link back to the root guide for campaign execution and results. |
| P3 | Contract and vendored license paths are inline code, not navigable GitHub links. Exact host compiler output and duplicated source hashes add maintenance burden. | Use relative Markdown links. Keep parser version/provenance and link the authoritative CMake hash pins; use emitted build metadata for the actual compiler/SDK/standard values. |
| P3 | Native checks depend on `tests/fixtures/golden.bin`, but the command block does not show fixture creation. | Put `python tests/protocol.py --export-fixtures` before native/shared checks, making the sequence reproducible from source. |
| P3 | No formatting command identifies maintained code or excludes vendored/generated trees. | Use the repository `.clang-format` and an explicit five-file list for `clang-format -i` and `--dry-run --Werror`. Parent supplies the style file and application formatting. |

## Verification and scope

- Command names, options, defaults, mode restrictions, and paths were checked against the local source and script argument parsers. The local CMake help lists the documented `Visual Studio 18 2026` generator; `cmake`, `python`, and `clang-format` are on PATH.
- The manual examples use a generated small corpus, matching server/client modes, the manual all-zero manifest default, and output paths whose directory is created explicitly.
- CMake rejects non-Windows/non-MSVC configurations and requires C++23; the application checks 64-bit pointers, which alone does not prove x64 architecture. The documented generator command explicitly selects the supported x64 target. AddressSanitizer is diagnostic only; no ThreadSanitizer support or new sanitizer pass is claimed here.
- No numerical performance result, general language ranking, allocation count, license for the application, owner URL, or publication status was invented. The first benchmark suite is parent-owned and its completed evidence must be linked only after validation.
- Source formatting and correctness changes are occurring concurrently. Historical review line references describe their reviewed snapshots; the module map uses file responsibilities and must be reconciled with the final source layout before release.

The native check suites and sanitizer validation belong to the implementation/integration pass. This reviewer executed the existing Release executable's `selftest` successfully (`ok=true`, canonical digest `3660725836179230917`, executable SHA-256 `62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79`), checked the four Python check scripts' CLI help, and confirmed `clang-format 21.1.8` is callable. All 12 README links resolve locally, and its seven fenced command blocks are balanced and parse without errors through the PowerShell language parser. This bounded documentation validation does not substitute for rebuilding or rerunning the integration/sanitizer suites after concurrent source changes.
