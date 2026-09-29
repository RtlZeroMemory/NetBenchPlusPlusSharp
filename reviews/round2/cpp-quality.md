# C++23 code quality review, round 2

Reviewer: GPT-6 Astra / XHIGH, code standards and simplicity. Review only; no application source, build output, or frozen binary was changed.

Inspected `bench.hpp`, `main.cpp`, `processing.cpp`, `load.cpp`, `transport.cpp`, CMake, native checks, the native/root READMEs, the shared contract, and the previous review log. Vendored simdjson was excluded. The native executable remains SHA-256 `62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79`; the managed DLL remains `60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7`.

## Actionable findings

### P2 — Expand compound statements so ownership and timing boundaries are visible

Locations: `src/cpp/load.cpp:63-92`, `:104-142`; `src/cpp/transport.cpp:51-123`, `:138-178`; also the CLI, parser, histogram, and report writers.

Multiple state changes, branches, declarations, lock scopes, and returns occupy the same physical line. Examples include ACK completion and descriptor recycling at `load.cpp:82-89`, clearing `pending` before connection expiration at `transport.cpp:101-102`, and the timer's nonblocking lock/cleanup at `:154-159`. These are correctness-sensitive ordering decisions that currently require mentally expanding the source before reviewing it. There are 87 application lines longer than 160 columns, with a maximum of 795 columns.

Required change (confirmed explicitly by the user): apply the available clang-format 21.1.8 to the five application C++ files with a small repository style configuration, preserving statement order and excluding vendor/build trees. Use ordinary spacing, one executable statement per line, and explicit multiline braces for stateful branches. Break report streams by field or a small related field group. Keep short accessors/constant expressions short. Add only a few comments where lock ownership, descriptor reuse, parser view lifetime, or timing scope cannot be inferred from a name. Increased physical line count is useful here and is not additional runtime complexity.

Verification: review a whitespace-insensitive comparison first; compile with the existing `/W4` settings and run the existing native/shared checks after all actual fixes. Do not regenerate the vendored parser or rework the wire representation.

### P2 — Give the phase orchestration a few named, testable steps

Locations: `src/cpp/load.cpp:32-144`, particularly schedule construction at `:36-44`, planned-count adjustment at `:95-102`, and abandoned-demand cursor accounting at `:132-136`.

The `phase` function contains schedule construction, Begin exchanges, pool construction, cancellation, pacing, two thread bodies, admission, reconciliation, drain, and End controls. Its shared state is intentional, but deterministic schedule/cursor arithmetic is buried among synchronization operations. A formatting-only pass will expose a much larger function without resolving that problem.

Minimal change: extract file-local helpers for building the optional arrival offsets, computing the finite scheduled count, and advancing cursors for undispatched arrivals. Name the two worker lambdas before launching them, so the launch loop only resets queues and starts the sender/ACK reader. Keep the mutex, bounded descriptor pool, admission loop, and final drain in the existing phase scope; do not introduce a scheduler framework, queue abstraction, or a general worker/context class solely to pass captures around. Name the shared absolute data deadline once after T1 is fixed and preserve the publication/locking relationship before the first request is visible.

Verification: the measurement suite must still reconcile all intended arrivals after early EOF/corrupt ACK, admit an honest ACK before T1 plus drain, classify actual expiry, and preserve closed-loop behavior. Add one small self-check for any extracted cursor/count arithmetic using the already documented edge cases; do not duplicate the whole measurement suite.

### P3 — Delete redundant initial socket timeouts and the unused no-deadline API

Locations: `src/cpp/load.cpp:151`; `src/cpp/bench.hpp:125-126`; `src/cpp/transport.cpp:16-18`.

`client` installs send/receive timeouts before connecting, but every `send_exact`/`receive_exact` caller supplies an absolute deadline and `remaining_timeout` overwrites the corresponding socket timeout before every operation. The initial options add two system calls and two error paths without supplying any I/O behavior. The default `deadline=0` and `if (!deadline) return` branch advertise an unused unbounded mode; the default cap arguments are unused too.

Minimal change: delete the two initial timeout setters, require explicit cap/deadline arguments in the declarations, and remove the no-deadline early return. Preserve per-operation remaining-time calculations, timeout error labels used by accounting, and the distinction between control deadlines and the common T1-plus-drain data deadline. Caller search covers every application call in `load.cpp`.

Verification: run `tests/measurement.py` and the partial-I/O native check; specifically retain the delayed-but-allowed ACK and withheld-ACK cases.

### P3 — Share effective socket buffer queries instead of repeating their setup

Locations: `src/cpp/load.cpp:153`; `src/cpp/transport.cpp:168`; declaration beside `socket_options` in `src/cpp/bench.hpp:123`.

Both paths perform the same two `getsockopt` calls, reset the same option-length variable, and use the same failure labels. This is the only substantial repeated socket setup that benefits from a shared helper.

Minimal change: one plain function `socket_buffers(SOCKET, int& send, int& receive)` in `transport.cpp`, called by client and server after their existing socket setup. Keep buffer configuration in `socket_options`; no socket factory or options class is needed. Do not combine blocking send/receive loops into a callable-template transport abstraction: their EOF/error/counter behavior is different and the remaining duplication is small.

Verification: native loopback checks must still report requested/effective buffers and pass fragmented I/O. This is outside the measured data path.

### P3 — Make standard-library dependencies explicit and delete unused includes

Locations: `src/cpp/bench.hpp:15`, `:23`, `:48`; `src/cpp/main.cpp:14`; `src/cpp/processing.cpp:108`; `src/cpp/transport.cpp:21`, `:71`.

`<deque>` and `<numeric>` have no application users. The application calls `snprintf`, `std::memcmp`, uses `std::remove_reference_t`, and uses `INT_MAX` without direct `<cstdio>`, `<cstring>`, `<type_traits>`, and `<climits>` includes, relying on transitive Windows/parser/library headers.

Minimal change: delete the two unused includes and add direct headers where their names are used (or in the existing shared header if retaining its current include convention). Use `std::snprintf`. A wholesale include-layer redesign or many new headers would not help this small program.

Verification: rebuild all four application translation units with the existing compiler settings; run the selftest and confirm ordinary diagnostic/report output remains valid JSON.

## Minimal module outline

Keep the existing five application files. The current boundary between processing, transport, and load is useful; no new dependency or framework is warranted.

| File | Responsibility after the small cleanup |
| --- | --- |
| `bench.hpp` | Shared options/results, processor declaration, bounded histogram, socket owner, and cross-file function declarations. Group these sections and name function parameters. |
| `main.cpp` | CLI parsing/validation, dispatch, common report fragments and histogram serialization. Keep the two small CLI readers; avoid a metadata-driven option framework. |
| `processing.cpp` | Strict parsing, canonical aggregation, retained lifetime/commit behavior, corpus validation, existing selftest. Separate these visually with a short section comment. |
| `transport.cpp` | Common framing/socket operations, per-connection IOCP state machine, server lifecycle. Keep `Connection` because it is the real I/O owner. Name the server worker loop and the stdin-control polling block if extraction materially shortens orchestration; do not hide pending-completion cleanup in an opaque destructor. |
| `load.cpp` | Client connection/phase orchestration, small deterministic schedule helpers, metrics output, processing-only control. Keep report formatting adjacent to the data it reports and outside timed phases. |

Comments should identify the lock protecting pool/queue links, the rule that only the ACK reader releases a request slot, the single pending `OVERLAPPED`, why a closed connection remains registered until its completion is observed, and the boundary between T1/drain and End controls. Existing comments already explain several of these; move or expand them only where needed. Prefer names over repeating code in prose.

## Explicitly retained

- Heap ownership of `Metrics`/`StageMetrics`: each embeds several large fixed histograms, so replacing the pointer with a stack local is not a simplification compatible with the stated stack budget.
- Two request links: send and ACK queues have different progress, and only ACK completion can recycle a descriptor.
- `Epoch::check` before retention changes plus checking in public `Epoch::add`: the early check protects atomic retention mutation, while `add` has a separate client caller. Removing either without changing the API contract is not a safe duplicate-check cleanup.
- The extra retained scratch slot and `Row.begin`/`length`: these own decoded text and prevent parser/input-buffer lifetime escape. No dead application data field was established.
- Manual endian and JSON string escaping helpers: neither `std::quoted` nor a generic native-memory cast implements this wire/JSON contract.
- The documented bounded global client mutex and the existing standard containers. No new lock-free queue, polymorphic interface, C++ module system migration, or testing framework is indicated.

## Verification scope

This pass is source inspection and hash verification only. Existing frozen checks were read, not claimed as newly executed. Fix follow-up should run the Release selftest, `src/cpp/check.py`, `src/cpp/regressions.py`, shared protocol checks, and shared client measurement checks. Run the focused ASAN suite after any ownership/control-flow changes; parent owns cross-language integration and sustained benchmarking. Preserve allocation/timing semantics and all output fields used by the runner/report tools.

Deletion accounting: two unused includes, two redundant socket-option operations, and one unused deadline branch can be removed. No honest net physical-line reduction is expected after making the dense source readable; the target is less behavior to maintain and clearly visible invariants.
