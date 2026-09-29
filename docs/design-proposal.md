# Plan: C# / .NET 11 versus C++23 TCP ingestion benchmark

Date: 2026-09-29. Status: design proposal; no server, client, or benchmark results exist yet.

## 1. Decision and scope

Build two interoperable implementations of one small application protocol over TCP. Compare sustained ingestion on this Windows PC using the same corpus, processing contract, load schedule, resource budget, and correctness oracle. The principal result is **useful completed work versus latency, CPU, and memory**, not a single language speed multiplier.

The user selected both direct JSON aggregation and retained typed records. No latency deadline has been supplied; use throughput/latency curves and report experimental 1 ms, 5 ms, and 10 ms thresholds. These are reporting thresholds, not assumed business requirements.

The headline workloads are:

1. **Aggregate:** validate and decode every record, categorize it, update integer aggregates, acknowledge the batch; do not retain an object graph.
2. **Retain/reuse:** deserialize into owned typed rows and owned decoded text, process those rows, retain a bounded window, and reuse storage after eviction.
3. **Retain/allocate:** the same retained-data semantics with ordinary allocating data structures, to expose allocation and reclamation behavior. Report this separately from reuse.

Add a transport-only socket workload and a full-corpus processing-only workload as explanatory controls. Neither replaces the end-to-end results. A binary encoding is a later controlled experiment using the same records and processing, not a replacement for the requested JSON comparison.

### Important premises

TCP delivers an ordered byte stream. Application writes are not application read boundaries. A short successful read leaves remaining bytes available for subsequent reads; it does not itself lose them. A close, reset, timeout, or application bug can leave a message incomplete. Application framing must handle that explicitly. We implement framing **on TCP**, not TCP itself. [TCP specification](https://www.rfc-editor.org/rfc/rfc9293.html#section-2.2)

Garbage collection can affect latency, but neither a C# loss nor its cause is established in advance. Avoiding application allocations does not prove that a whole process never allocates or pauses. Native code also experiences scheduling delays, allocator costs, faults, and contention. This experiment measures soft latency on desktop Windows; it cannot establish hard real-time guarantees. .NET low-latency GC modes still permit collections and have memory tradeoffs. [GC latency modes](https://learn.microsoft.com/en-us/dotnet/standard/garbage-collection/latency)

Length framing and binary serialization are independent decisions. Both JSON and binary records can use the same length-prefixed envelope.

## 2. Observed local environment

Read-only inspection found:

| Item | Observed value |
| --- | --- |
| Workspace | `F:\SocketServerClient`; initially empty, not a Git repository |
| OS reported by .NET | Windows, build `10.0.26200`, x64 |
| CPU | AMD Ryzen 7 9800X3D, 8 physical cores / 16 logical processors |
| OS-visible physical memory | Approximately 61.65 GiB |
| .NET SDK | `11.0.100-preview.7.26381.103` |
| .NET runtime | `11.0.0-preview.7.26381.103` |
| Native tools | Visual Studio 18 Community, MSVC tools directory `14.51.36231`; LLVM tools also present |
| CMake | `4.2.3` |

These observations are not a verified native build. Phase 1 must record actual compiler versions, compile commands, Windows SDK, and supported C++23 mode. Pin the installed .NET preview explicitly; do not silently roll forward. An SDK change creates a new experiment. Do not infer the newest available preview from what is installed.

## 3. Questions the experiment answers

- At the same offered load, what are completion latency, deadline misses, CPU cost, and memory use?
- What is each implementation's maximum sustainable completed records/s and payload GiB/s?
- How much changes when typed data must survive reuse of the receive buffer?
- How do allocation rate, retained live data, and GC or native reclamation affect tails?
- What happens during overload, bursts, slow readers, fragmentation, disconnects, and recovery?
- Which measured limit belongs to the server, the load generator, the loopback stack, or shared hardware?

These are implementation-stack results: C# + runtime + parser versus C++ + compiler + parser. Different JSON engines have different algorithms. Semantic equivalence makes the application comparison useful, but does not isolate a pure language effect.

## 4. Wire protocol

### Envelope

Use a fixed 16-byte header, followed immediately by `payload_length` bytes. All header integers use big-endian byte order; parse explicit fields rather than casting a native struct onto the wire.

| Offset | Width | Field | Rule |
| --- | --- | --- | --- |
| 0 | 4 | `payload_length` | Unsigned byte count excluding the header |
| 4 | 2 | `version` | Exactly `1` |
| 6 | 2 | `message_type` | One of the supported types below |
| 8 | 8 | `sequence` | Strictly increasing for requests on each connection; echoed by the response |

Request types: `BeginEpoch = 1`, `JsonBatch = 2`, `EndEpoch = 3`; reserve `BinaryBatch = 4` for the later binary experiment. A response has the request type with bit `0x8000` set. Reject unsupported types, including reserved binary requests before binary support exists.

The v1 default hard payload limit is **16 MiB**. Each benchmark scenario has a smaller configured bound where possible, normally 1 MiB. Validate the declared length, type-specific minimum/maximum, sequence, and checked size arithmetic before using the value as an allocation or receive size.

`BeginEpoch` carries the SHA-256 hash of the canonical scenario manifest, exactly 32 bytes. It is accepted only when earlier work on that connection has drained; its response has an empty payload. It resets epoch counters while preserving warmed buffers and the configured retained window. Warmup window contents are tagged as warmup, and their later eviction is accounted for consistently in both implementations.

`JsonBatch` carries one complete UTF-8 JSON array. Its successful response contains exactly 16 payload bytes: `record_count: u64` and `result_digest: u64`. Thus the ordinary acknowledgement is 32 bytes including its header. Acknowledgement means validation, required processing, and insertion into the retention window have completed. It does not mean durable storage.

`EndEpoch` has an empty payload. Its response carries fixed-width totals: completed batch count, payload bytes, record count, and an order-sensitive digest of batch results, followed by the fixed category count/sum arrays. Freeze the exact field offsets and signed encodings in the protocol document during Phase 1. Per-connection summaries avoid dependence on nondeterministic interconnection completion order.

No per-batch initiate/ready handshake is needed. The client already knows the UTF-8 byte length and can send header and body immediately. Length counts serialized bytes, not characters or the size of an in-memory object. Corpus replay uses precomputed lengths. A future live-serialization profile serializes once into owned storage and takes the resulting byte count; its cost belongs in that profile.

### Receive/send state machine

`header -> validate -> body -> validate/decode/process -> acknowledgement -> next header`

- Accumulate exactly 16 header bytes across any number of receives.
- Receive the declared body directly into its final contiguous reusable storage, using an offset and remaining count.
- Never issue a receive larger than the remainder of the current header/body in the baseline. Bytes from following frames stay in the socket buffer. This makes coalesced TCP delivery correct without a second user-space accumulator or compaction copy.
- A receive of zero before a new header is orderly peer shutdown; zero partway through a header/body is truncation. Neither is successful completion of a pending request.
- Loop until every requested send byte completes. For scatter/gather, advance both the current segment and its offset after a partial send.
- Keep one owner for the read/framing state and one serialized send path per socket. Multiple producers must not interleave header/body writes.
- One application frame is processed at a time per connection. Parallelism comes from connections; the client may have multiple requests in flight.
- Count every completed frame once. An invalid frame changes no committed application aggregate or retention state. Stage per-frame state until validation succeeds.
- On malformed framing/JSON, deadline expiry, or reset: close the connection, record a bounded reason code, cancel pending work safely, and do not attempt magic-byte resynchronization.
- Default fault-test deadlines: 30 seconds absolute frame completion and 5 seconds with no receive progress, both configurable. Do not let a trickle of bytes defeat the absolute deadline. Idle-between-frame behavior is separate.
- Treat EOF after a complete request as a half-close: finish valid pending output where possible. A reset can make its delivery impossible; the client must then mark the operation failed or of unknown outcome.
- No automatic retry during a measured run. A reconnect cannot establish exactly-once application processing by itself.

With native overlapped I/O, closing/canceling a socket does not make buffers immediately safe to free. Reclaim only after the corresponding completion has been observed. Handle immediate and pending completions without double dispatch. [Winsock overlapped I/O](https://learn.microsoft.com/en-us/windows/win32/winsock/overlapped-i-o-and-event-objects-2)

The exact-read baseline has extra receive calls for small frames. Report calls/frame. A coalescing accumulator is a paired optimization only if profiling identifies this cost; it must be introduced for both implementations and rerun through the same tests.

## 5. Data and processing contract

### Corpus

Generate deterministic data once with an offline reference generator, then save exact UTF-8 bytes, offsets, lengths, seeds, and expected results. Both clients replay the same files. No JSON generation, file reads, random-number generation, or textual logging belongs in the primary timed send path.

Default input working set: **2 GiB**, loaded and touched before timing, with a deterministic shuffled frame order. This deliberately exceeds a small repeatedly parsed cache-resident document. Add a separately labeled 16 MiB hot-corpus sensitivity test. The measured work can replay the corpus many times; disclose unique bytes and total bytes separately.

Frames contain an array of records with these required fields:

| Field | Type / rule | Purpose |
| --- | --- | --- |
| `id` | Unsigned 64-bit integer | Identity / digest |
| `timestamp_ns` | Unsigned 64-bit integer | Parsed application data; not the benchmark clock |
| `source` | Unsigned 32-bit integer | Identity / digest; optional later keyed aggregation |
| `kind` | One of 16 published string values | Category |
| `value_milli` | Signed integer in `[-1,000,000, +1,000,000]` | Exact category sum |
| `flags` | Unsigned 32-bit integer | Severity is `flags & 3`; all bits enter digest |
| `message` | Unicode string, decoded UTF-8 length at most 4096 bytes | Text decoding / ownership / digest |

Use ordinary integer tokens, not quoted numbers, exponents, or floating-point substitutions. Integer ranges and lexical rules are checked equally. Use a per-record seen-field bitmask to reject duplicate or missing properties. Property order is unrestricted. Unknown properties are rejected in v1; this is a fixed application schema, not a generic JSON document service.

Reject invalid UTF-8, invalid escapes, unpaired surrogates, comments, trailing commas, excess nesting, extra root values, and non-whitespace after the root. Publish the depth limit and its counting convention, then verify both parsers at the boundary. Compare decoded field names and values, so escaped names cannot bypass duplicate detection. No Unicode normalization is applied.

Every required field must be read and type checked. Every string must be decoded/validated according to the contract, including fields that do not affect the category. Merely finding a few keys or accepting a lazy parser document is insufficient.

### Equivalent useful work

For each record:

1. Validate and decode all fields.
2. Select one of 64 buckets: `kind_index * 4 + (flags & 3)`.
3. Increment its count and add `value_milli` to its signed sum.
4. Update a specified 64-bit digest over canonical typed values, including the decoded message bytes, with explicit field boundaries and lengths.

Freeze digest constants, byte order, signed-integer encoding, and wraparound behavior in the specification. A simple specified FNV-1a digest is sufficient for checking benchmark output, with golden vectors and an offline SHA-256 corpus identity; it is not a security checksum. Digest work is timed and may be significant for long strings, so the report must expose its cost in a diagnostic profile rather than silently remove it for one side.

Cap an epoch at at most one billion records. Together with the value bounds this keeps category sums safely within signed 64-bit range. Reject configuration that violates these published bounds. C++ signed overflow must never be used as an implicit equivalent of C# unchecked arithmetic.

The client verifies each result count/digest against precomputed expected values and checks every sequence. Final summaries must agree with the reference oracle. Golden tests compare full category arrays as well as digests, so correctness does not rest on hash collision assumptions.

### Three processing modes

**Aggregate:** parse directly into local scalars and fixed category arrays. Decode escaped strings into reusable scratch when needed; avoid creating a managed string/native string for every field. Commit the small per-frame result only after the entire JSON document passes validation.

**Retain/reuse:** materialize typed rows and decoded UTF-8 text into owned reusable batch storage. Rows refer to offsets/lengths inside that owned text storage, not into the socket buffer or parser scratch. Aggregate by visiting the materialized rows. Retain the last 8 batches per connection, subject to a 64 MiB canonical-data limit per connection. Evict oldest batches before exceeding either bound. Reuse their storage only after their data is no longer needed. Before eviction, revisit retained rows/text and verify their stored digest; this forces retention to have observable semantics and catches dangling views. Include this work in both implementations.

**Retain/allocate:** use the same retention and revisit rules, but ordinary owned arrays/containers and strings, allocating for new batches and releasing evicted batches. C# can use typed records plus managed strings; C++ can use typed records plus owning strings. Their object layouts and string encodings differ, so record that distinction and do not interpret this as an allocator-only comparison. The reuse mode supplies the closer UTF-8 data-layout comparison. Do not force C++ into individually heap-allocating each record just to mimic a managed object graph.

Retention is a deterministic window in batches and canonical bytes, not wall time; otherwise a faster implementation would retain more data. Large-frame scenarios must fit at least one batch in this window. Actual capacity, metadata, and allocator overhead are reported separately from canonical bytes. Warm the selected size classes and live window naturally; do not force GC before measurement to manufacture a clean heap.

## 6. Implementation choices

### Shared architecture

Use separate client and server processes. Each language builds one executable with server, client, and processing-only modes. Use a small PowerShell runner for process setup and result collection; it never handles timed payload traffic.

For the primary async server, use one serial state machine per connection, direct receives into reusable buffers, inline per-frame processing, and a bounded response path. There is no unbounded intermediate work queue. Once a connection is processing or its response is blocked, stop consuming further frames on that connection. TCP then supplies backpressure. All application memory and in-flight descriptors remain bounded.

Receive capacity is fixed from the scenario's maximum before measurement. Keep one buffer per connection throughout its lifetime. This is simpler than renting an array for every frame. Account for parser workspace, retained data, outbound buffers, socket buffers, and measurement storage in addition to payload capacity.

Starting application storage envelope: 1 GiB for transport/parser capacity and 2 GiB for retained data across the server, checked during admission. These are budget targets, not claims that both runtimes have identical total memory. Measure actual peak private bytes, commit, and working set. If parser capacity cannot fit, reject the scenario symmetrically or reduce connections in its named profile; never silently shrink the workload for one implementation.

### C# / .NET 11

- Target `net11.0`, x64 Release; pin SDK/runtime versions and record the executable hash.
- Use the `Socket` memory-based async APIs, reuse connection buffers, and keep parsing synchronous inside the completed-frame handler.
- Use `Utf8JsonReader` over a contiguous span. It provides low-level UTF-8 token access; avoid `JsonDocument`, dictionaries, LINQ, boxing, and per-record delegates on the aggregate path. Keep ref-struct/span lifetimes within synchronous code; do not retain them over an await. [Utf8JsonReader guidance](https://learn.microsoft.com/en-us/dotnet/standard/serialization/system-text-json/use-utf8jsonreader)
- Do not assume that obtaining a raw token span proves decoded Unicode validity. Conformance tests determine which explicit UTF-8/unescape checks are necessary, and their real cost stays inside timing.
- Use small stack storage only for fixed synchronous scratch. Large payloads, retained records, and asynchronous I/O buffers require appropriate owned memory, not large `stackalloc` calls.
- Start with background Server GC for the 4-core primary server. Record effective GC configuration, heap count, processor count, tiering, dynamic PGO, and runtime async configuration; do not assume environment defaults.
- Let the runtime manage its worker pool initially; do not cap it at the physical core count and accidentally starve completions. The process CPU affinity is the common resource boundary.
- Treat source-generated `JsonSerializer` as a separately labeled idiomatic deserialization variant if added. It must satisfy the same schema checks; source generation does not make retained strings or arrays allocation-free. [Source generation](https://learn.microsoft.com/en-us/dotnet/standard/serialization/system-text-json/source-generation)

### C++23

- Start with the installed MSVC toolchain, x64 optimized Release, CMake `CXX_STANDARD 23`, required standard support and no silent downgrade. Record the exact generated standard/optimization/linker flags. Compiler standard switches vary with installed versions. [MSVC standard selection](https://learn.microsoft.com/en-us/cpp/build/reference/std-specify-language-standard-version?view=msvc-170)
- Use Winsock overlapped receive/send with IOCP and a small fixed worker pool. Keep the native transport narrow: one outstanding receive and serialized send per connection, no custom coroutine framework, allocator, or network stack.
- Use a pinned simdjson release for JSON decoding, with one parser per independently active connection. Reuse parser capacity, provide its required readable padding, and keep all input/parser-backed views within their valid lifetime. Padding belongs to allocated capacity, never to the transmitted length. Traverse the complete document and check deferred errors and trailing content. [simdjson documentation](https://github.com/simdjson/simdjson/blob/master/doc/basics.md)
- Use standard owned containers and RAII for retained storage and native handles. Reserve capacity before measurement where the reuse profile allows it; native allocation/free costs remain timed in the allocate profile.
- Keep checks and normal error handling enabled. No undefined behavior, unchecked input, fast-math shortcut, or debug STL configuration hidden in a release result.
- A clang-cl build is a compiler sensitivity check after the primary pair is established, not an opportunity to pick and report only the fastest native result.

The .NET worker pool and native IOCP workers are not identical schedulers. We match serial per-connection semantics, buffering, processing work, CPU resources, and socket behavior. Report thread counts and scheduling effects. A single-connection blocking-I/O diagnostic may be added if scheduler differences need isolation.

### What is deliberately deferred

No TLS, compression, persistence, GUI, distributed clients, custom binary serialization, bespoke allocators, lock-free queues, or zero-copy kernel APIs in the primary experiment. They change the question. No `NoGCRegion`, forced GC suppression, real-time process priority, or manual loopback fast-path tuning in headline runs.

`System.IO.Pipelines` is a valid later implementation variant, not a prerequisite. Whole-frame parsing and a pipe threshold smaller than the unconsumed frame can deadlock unless the design consumes/copies incrementally or adjusts buffering. A pipeline variant needs its own ownership and backpressure tests. [Pipelines guidance](https://learn.microsoft.com/en-us/dotnet/standard/io/pipelines)

## 7. Localhost and machine controls

Use explicit `127.0.0.1` and persistent TCP connections; avoid hostname resolution and accidental IPv4/IPv6 mixing. `::1` is a separate sensitivity run. Loopback exercises the OS socket path but excludes NIC, physical link, and remote-host effects.

For this 8-core machine, start with:

| Role | Initial budget |
| --- | --- |
| Server | 4 physical cores, one selected logical processor per core |
| Client | 3 other physical cores, one selected logical processor per core |
| Runner / OS headroom | Remaining physical core; no claim of OS isolation |

Discover actual core/SMT topology; never assume contiguous processor numbers map to distinct cores. Keep test processes off each other's SMT siblings. Affinity constrains those processes, not all Windows kernel work or background applications. Start the runtime under the intended affinity and verify its effective processor/GC configuration. Both languages receive the same server mask.

Use additional server budgets of 1 and 2 cores for scaling. A shared-SMT or all-core run is a separate deployment-style profile. CPU budget experiments must keep client adequacy visible.

Record OS build, power plan, CPU topology/cache information, memory availability, CPU clock/temperature if accessible, SMT status, socket options, antivirus/background activity, and tool versions. Use normal process priority, no debugger, no sanitizers/profiler for primary performance runs. Keep the same power settings, warmup, socket sizes, and cooldown rules for both implementations. Do not disable security software merely to chase a score.

Set `TCP_NODELAY` identically, initially enabled, on data and acknowledgement paths. Request the same `SO_SNDBUF`/`SO_RCVBUF` sizes, initially 256 KiB, and record effective values. The kernel may buffer more than the application window; disclose that additional queued memory. Compare a buffer-size sensitivity pair if socket buffering shifts the knee of the latency curve.

Client and server still share last-level cache, DRAM bandwidth, power, and thermal limits. That is a property of this experiment. Never translate loopback throughput directly into an Ethernet capacity or claim that affinity eliminates shared-hardware interference.

### Client adequacy and the four combinations

Run every client against every server for interoperability:

| Client | Server |
| --- | --- |
| C# | C# |
| C# | C++ |
| C++ | C# |
| C++ | C++ |

For primary server comparisons, hold the client implementation fixed across the pair. Repeat representative results with the other client. A native driver is not assumed neutral or unlimited; a managed driver can also pause.

Calibrate with transport-only receivers and inspect client CPU, send blocking, schedule lateness, and result-verification cost. Where feasible demonstrate at least 25% delivery headroom over the target load and repeat with a changed client core budget. If additional client resources change the apparent server limit, label the earlier number generator-limited. If neither client can saturate the transport control on this host, report a co-hosted system limit rather than invent a server ceiling. Client GC is measured in managed-client runs.

## 8. Workload matrix without a combinatorial explosion

### Primary comparisons

Use aggregate and both retain modes, with 64 KiB and 1 MiB target frame sizes, at 1 and 16 connections, and a 4-core server budget. These are 12 workload cells per implementation before load points. Frame sizes are targets: pack complete records, publish actual byte distributions, and use identical bytes on both sides. Do not fill most of a nominal JSON workload with ignorable whitespace.

The primary corpus combines balanced categories with realistic varied text: short/long integer tokens, empty and nonempty messages, ASCII plus multibyte UTF-8 and escapes, varying field order, and a documented source distribution. Publish the generator's exact proportions before implementation tuning. Unicode-heavy and skewed-category corpora are separate sensitivities. Include a held-out seed/order for final confirmation.

### Targeted sensitivities

| Dimension | Selected values | Purpose |
| --- | --- | --- |
| Frame size | 1 KiB, 4 KiB, 64 KiB, 96 KiB, 256 KiB, 1 MiB, 16 MiB | Syscall overhead through large-body behavior |
| Connections | 1, 4, 16, 32 | Per-connection and parallel scaling |
| Server cores | 1, 2, 4 | CPU scaling on this shared host |
| In-flight window | 1, 8, 64 frames, also capped by bytes | Latency/throughput/queueing tradeoff |
| Corpus working set | 16 MiB and 2 GiB | Cache-sensitive versus larger working set |
| Categories | Balanced and 90% concentrated in one category | Branch/data distribution |
| JSON | ASCII; escaped/Unicode-heavy; property order shuffled | Parser semantic costs |
| Retention | 1, 8, 32 batches subject to the same byte cap | Live-data pressure |
| Managed GC | Server baseline; Workstation; SustainedLowLatency | Throughput/tail/memory tradeoffs |
| Compiler/runtime | MSVC baseline; clang-cl; optional stable .NET control | Toolchain sensitivity |

Vary one relevant dimension at a time around representative primary cells. Do not run the full Cartesian product. For 16 MiB frames, begin at 1 and 4 connections; expand only when the shared memory envelope admits it.

The 64/96 KiB size pair can help examine large-buffer behavior, but it does not by itself isolate .NET's large-object heap: allocations also depend on object layout, capacity growth, and pooling. Reusable transport buffers are allocated before timing. Record actual allocations rather than inferring them from wire size. [GC configuration and LOH threshold](https://learn.microsoft.com/en-us/dotnet/core/runtime-config/garbage-collector#large-object-heap-threshold)

## 9. Load models

### Saturation / closed loop

The client sends as quickly as it can while respecting a fixed maximum outstanding request count and byte budget. It continues reading acknowledgements concurrently with writes. Default global in-flight payload cap: 64 MiB; publish the resulting effective request window for each frame size.

This measures achievable completed throughput under backpressure. It does not establish independent-arrival latency: a waiting client naturally reduces traffic when the system stalls. Report its latency only as closed-loop latency.

### Scheduled / open loop

Use a deterministic arrival schedule independent of acknowledgements: both steady and seeded Poisson arrivals. Schedule is shared across languages, including connection assignment and frame order. Probe common absolute rates around the slower preliminary capacity, for example 25%, 50%, 70%, 85%, 95%, 105%, and 120%. Also report each implementation's own normalized load curve separately; never compare p99 at unlike absolute rates without labeling it.

Maintain bounded preallocated request descriptors. Each request has intended arrival time, actual first-send-attempt time, and final acknowledgement time. Socket backpressure must not reset its intended time. The scheduler and acknowledgement reader continue accounting while a send is pending.

When a scheduled request cannot enter the bounded outstanding set, count it as admission failure at its scheduled time. Do not quietly skip it or shift later arrivals. Once admitted, queueing delay is included in scheduled latency. Do not use timeout cancellations to make a success-only histogram look healthy; report every timeout/failure separately and count it as an SLO failure.

Report scheduler lateness and occupancy. Windows pacing cannot be assumed precise at arbitrarily high rates. If the generator cannot sustain the requested schedule independently, that run is not valid evidence of server latency at that offered load. Batching that is needed for pacing changes the arrival model and must be labeled.

The risk of missing samples during pauses is commonly called coordinated omission. A measured scheduled-arrival workload avoids hiding the client queue; do not also apply synthetic histogram correction to the same samples. Use histogram correction only for a separately labeled diagnostic based on closed-loop samples. [HdrHistogram explanation](https://github.com/HdrHistogram/HdrHistogram/blob/master/README.md#corrected-vs-raw-value-recording-calls)

### Bursts and overload recovery

Add a deterministic burst profile: 5 seconds at 60% of the common reference capacity, then 1 second at 150%, repeated. Add a sustained 120% offered-load run. Record admission failures, queue maxima, completed work, memory plateau, time to drain, and time until latency returns to its prior range after load falls. The overload cap is an explicit load-shedding policy, not hidden demand suppression.

## 10. Measurement definitions

Use `Stopwatch.GetTimestamp()` in C# and `QueryPerformanceCounter()` in C++. Save clock frequency and keep raw tick arithmetic wide enough to avoid overflow. Prefer same-process intervals. On Windows these clocks share QPC as their timing basis; verify frequency/units before correlating process traces. Do not use wall-clock timestamps, `DateTime.Now`, or raw unsynchronized CPU-cycle counters for latency. [Windows high-resolution timing](https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps)

For request i:

| Metric | Definition |
| --- | --- |
| Scheduled completion latency | `ack_received_i - intended_arrival_i` |
| Submitted round-trip latency | `ack_received_i - first_send_attempt_i` |
| Client delay | `first_send_attempt_i - intended_arrival_i` |
| Receive-stage elapsed time | First header receive issue to final body receive completion |
| Processing elapsed time | Complete body available to successful processing/retention completion |
| Decode/materialize time | Instrumented decode/materialize interval in retained modes |
| Categorize time | Instrumented visit of materialized rows and aggregate update |
| Successful payload throughput | Valid completed JSON payload bytes / explicit measurement interval |
| Record throughput | Successfully processed records / explicit measurement interval |
| CPU cost | Server process user + kernel CPU seconds per million records and per GiB |

The receive interval includes waiting for data, scheduler delay, and delivery under backpressure; it is not pure kernel socket-copy CPU time. In the aggregate mode decoding and categorization are fused, so label the stage as combined instead of inventing a clean split. Use CPU traces to explain internal costs. Do not subtract processing-only and end-to-end rates to estimate networking overhead: the components overlap and contend.

Publish frame completion latency and the frame's record-count distribution. Never divide batch latency by record count and call that per-record latency. Time from an external producer creating each record would require a separate production/serialization workload.

### Required metrics

- p50, p90, p99, p99.9, maximum observed, and deadline miss counts; p99.99 only when sample count supports it.
- Offered, admitted, first-send-started, server-completed, client-acknowledged, failed, and unresolved counts, with exact reconciliation.
- Payload GiB/s, frames/s, records/s, protocol bytes, and ACK traffic separately. Do not call application bytes physical wire bandwidth.
- Server and client CPU time, thread count, receive/send calls, bytes/call, in-flight/queue high-water marks, and generator lateness.
- Peak/steady private bytes, working set, committed memory, buffer capacities, retained canonical bytes, and actual owned-storage capacity.
- Managed allocated bytes/record and bytes/second, Gen0/1/2 counts, heap/LOH observations, and actual suspension durations in diagnostic runs.
- Native allocation/free counts and bytes in a separate instrumented diagnostic, plus private-memory behavior during eviction. An overloaded `operator new` counter alone does not capture all process/native allocations.

Use mature HDR histogram implementations with matching range/precision and test vectors for client latency, recording without per-event allocation. Keep per-connection or per-worker histograms and merge counts after the interval; do not average percentile values. Preserve overflow/underflow counters. Provide per-size histograms for mixed workloads, since a single pooled percentile can conceal small-frame starvation.

Primary runs use minimal common instrumentation. Sample detailed server stage timing deterministically, initially 1 in 1024 frames, rather than putting many timestamps in every hot loop. Record the sample count; omit extreme stage percentiles when the sample is insufficient. Validate the measurement tax with paired instrumentation-on/off trials. GC/EventPipe, ETW, allocation stacks, and full per-stage profiles are diagnostic repetitions, not silently mixed into the primary numbers. [dotnet-trace](https://learn.microsoft.com/en-us/dotnet/core/diagnostics/dotnet-trace)

For GC attribution, align client latency excursions and server suspension events, accounting for queued requests affected after the pause. Report pause duration, pause fraction, and maximum; a background collection's total duration is not its stop-the-world duration. Investigate scheduler, client, allocation, and paging explanations before naming GC as the cause of every tail event.

## 11. Run lifecycle and statistics

1. Save a manifest with scenario, corpus/expectation hashes, source/build hashes, toolchain/runtime settings, resource masks, socket settings, and random seeds.
2. Start fresh client/server processes for each independent trial. Load and touch input, buffers, parser capacity, and histogram storage.
3. Establish connections and warm with representative traffic for at least 30 seconds. Check rolling throughput/JIT/GC behavior; if still changing, extend symmetrically under a published rule, up to 120 seconds, and report failure to stabilize rather than choosing a flattering interval.
4. Drain outstanding warmup requests. Exchange `BeginEpoch`, preserve warmed storage/live retention, then start from a future agreed monotonic T0. No measured request may be sent before T0. Control traffic is outside timed data work.
5. Measure for 120 seconds for confirmation runs. After T1, stop new scheduled arrivals, keep receiving, then send `EndEpoch` once data has drained. Use a declared drain deadline, initially 30 seconds; unresolved requests are failures.
6. Report window completions within `[T0,T1)` separately from whole-cohort completion rate through the last ACK. Latency follows the requests scheduled within the window, including their drain-period completion. Never add drain successes to a 120-second numerator while hiding drain time in the denominator.
7. Validate all counts/digests, snapshot final memory, stop processes, save results. A missing ACK or wrong result invalidates a supposedly successful performance run.
8. Run at least 7 independently restarted paired trials for the selected confirmation cells, randomizing/balancing C#/C++ order. Keep raw per-trial results and histograms.

Use a short 30-second exploratory sweep to locate capacity and interesting regimes, then freeze the configurations before confirmation. This prevents spending days on uninformative matrix cells or selecting the best run after looking at results.

Budget the campaign explicitly. Explore all 12 primary workload cells, then use four preselected anchor cells for the initial full confirmation: aggregate/64 KiB/16 connections, aggregate/1 MiB/1 connection, retain-reuse/64 KiB/16 connections, and retain-allocate/64 KiB/16 connections. Confirm a low, near-knee, and overload rate for each anchor, selected by the same published pilot rule for both servers. Additional curve points remain exploratory unless repeated to the confirmation standard. With 30-second warmups, 120-second measurements, and seven paired trials, one confirmed cell/rate needs about 35 minutes before setup/drain/cooldown. Twelve such points need about seven hours; pilot, diagnostics, and soaks add time. The complete experiment is an overnight-scale campaign, with a much shorter smoke/pilot command for iteration. Do not advertise exploratory cells as confirmed results.

Report medians, per-trial dispersion, paired ratios, and uncertainty intervals computed over independent trial pairs. A bootstrap interval with seven pairs is only a rough indication; add paired trials if the conclusion depends on a small difference. Do not treat millions of correlated requests in one run as millions of independent experiments. Do not silently discard slow trials; exclusions require a predeclared environmental reason and must remain visible.

Require enough samples for tail claims. A practical initial gate is at least one million completed frames for a headline p99.9, with run-to-run tails also shown. For p99.99, target ten million or state that the estimate is weak. With huge frames, the necessary exposure may be impractical; report lower percentiles, maximum observed, sample count, and SLO failures rather than claiming precision. Maximum observed is not a proven worst-case bound.

Run 30-minute soak tests for representative aggregate and retained workloads below the common saturation knee, plus the specified overload/recovery test. Naturally occurring collection/reclamation must be included; do not force collections merely to guarantee a GC event. If a reuse workload has no collections, report that observation and its duration.

Define sustainable capacity as the highest tested rate with no admission failures, timeouts, invalid results, or unexplained errors, no continuing queue or memory growth after warmup, and a window completion rate within 1% of offered rate. Every admitted request must reconcile successfully through the drain deadline. Report deadline-constrained capacity separately for each threshold: at least 99.9% of *all scheduled requests* must complete within that deadline, with admission failures and timeouts counted as misses. Do not infer a percentile over failures as if each were a finite successful latency.

## 12. Correctness and fault campaign

Use the same fixture corpus against both implementations, with an independent slow reference checker. Python's standard JSON parser can support the reference, but configure duplicate detection, integer range/lexical rules, Unicode checks, and rejection of nonstandard numbers; its defaults alone are not the specification.

| Area | Required checks |
| --- | --- |
| Framing | Every split of a short header/body; one-byte chunks; multiple frames together; body followed by a partial next header |
| Partial I/O | Deterministic capped receive/send completion sizes, including segment-boundary partial sends |
| EOF/failure | Close/reset at every offset of short fixtures; clean EOF between frames; half-close after complete body; missing final response |
| Header validation | Wrong version/type/sequence; empty data payload; zero-length legal controls; maximum length; max+1; `0xffffffff`; checked arithmetic |
| JSON | Missing/duplicate/unknown fields; all property orders; malformed UTF-8; escaped property names; Unicode pairs and lone surrogates; numeric boundaries; trailing junk; depth/length limits |
| State | Bad frame cannot partially commit; counters reset across epochs; no duplicate processing; correctly reconcile partial success before disconnect |
| Ownership | Overwrite/reuse receive buffers immediately after permitted lifetime; revisit retained data; no view into recycled parser input |
| Flow control | Slow sender; client stops reading ACKs; full outstanding window; memory budget exhaustion; cancellation during I/O |
| Longevity | Size churn, connect/disconnect churn, retention eviction, repeated start/stop, timer/sequence boundary checks |
| Cross-language | All four client/server combinations and identical fixture classifications/results |

Fragmenting application writes does not guarantee the OS delivers corresponding receive fragments. Test the framing component with deterministic chunk delivery as well as real sockets. Malformed traffic and deliberately tiny writes belong to correctness/fault or labeled stress profiles, not the headline throughput dataset.

Native safety builds use AddressSanitizer and applicable compiler/runtime checks. Sanitized runs never supply performance results. Concurrency review and stress are required; do not promise ThreadSanitizer support on a Windows toolchain that does not provide it.

## 13. Implementation phases and acceptance gates

The following paths are proposed; none except this plan has been created. Prefer a few cohesive source files, not a framework of interfaces.

| Phase / task | Files | Depends on | Runnable acceptance |
| --- | --- | --- | --- |
| 1.1 Freeze contract | `docs/protocol.md`, `docs/workloads.md` | This design | Golden bytes, ACKs, validation rules, retention semantics, and CLI scenarios are unambiguous |
| 1.2 Pin builds | `global.json`, `src/dotnet/Bench.csproj`, `src/cpp/CMakeLists.txt`, dependency pins | 1.1 | Both Release binaries print version/build metadata and pass toolchain smoke checks |
| 1.3 Build corpus/oracle | `tools/corpus.py`, `tests/fixtures/`, `data/manifest.json` | 1.1 | Repeated generation yields identical bytes, counts, digests, and SHA-256 hashes |
| 2.1 C# framed transport | `src/dotnet/Program.cs`, `src/dotnet/Transport.cs` | 1.2 | Exact header/body reads, partial sends, acknowledgements, deadlines, and bounded buffers work |
| 2.2 C++ framed transport | `src/cpp/main.cpp`, `src/cpp/transport.cpp` | 1.2 | Equivalent socket behavior, native lifetime checks pass |
| 2.3 Protocol conformance | `tests/protocol.py` plus small framing self-checks | 1.3, 2.1, 2.2 | All four pairings pass splitting/coalescing/failure cases before performance work |
| 3.1 Aggregate mode | `src/dotnet/Processing.cs`, `src/cpp/processing.cpp` | 2.3 | Full valid/invalid corpus parity and exact category/digest equality |
| 3.2 Retain/reuse mode | Same processing files | 3.1 | Owned rows/text survive receive reuse; retention/eviction and memory bounds match |
| 3.3 Retain/allocate mode | Same processing files | 3.2 | Same retained semantics, allocating behavior observed rather than assumed |
| 4.1 Client load modes | `src/dotnet/Load.cs`, `src/cpp/load.cpp` | 3.3 | Bounded concurrent send/ACK read, schedule accounting, fixed workload replay |
| 4.2 Metrics and runner | `bench/run.ps1`, `bench/report.py`, scenario manifests | 4.1 | Known timing vectors, histogram parity, trial order, manifests, and count reconciliation |
| 4.3 Measurement sabotage tests | `tests/measurement.py` and test-only delay switches | 4.2 | Injected stalls/loss/errors appear in metrics; generator inadequacy cannot pass silently |
| 5.1 Adversarial review passes | `reviews/review-log.md`, regression fixtures | 4.3 | All passes in section 14 completed with evidence |
| 5.2 Pilot and confirmation | `bench/scenarios/`, `results/<run-id>/` | 5.1 | Calibrated client; frozen profiles; paired reproducible macrobenchmark trials |
| 5.3 Soak and final audit | Same result/review paths | 5.2 | Bounded memory, overload recovery, held-out corpus validation, final run integrity |
| 6 Optional binary experiment | Protocol/processing/corpus additions | JSON final audit | Same semantic records/results, independently rerun framing and fairness checks |

The C# and C++ transport tasks can proceed independently only after the wire contract is frozen. Aggregate implementations can similarly proceed independently against the same oracle. Integration and adversarial review are sequential gates, not approvals inferred from code compiling.

Each phase ends in an executable demonstration: golden corpus check, socket exchange, equivalent aggregation, retained-data survival, reproducible scheduled-load run, and finally audited results. Commit logical increments once a repository exists. Rollback means reverting the affected increment and invalidating dependent result IDs; never overwrite old benchmark evidence with a different executable.

## 14. Required adversarial review after implementation

These are multiple distinct passes over implemented code and executable evidence. Reviewing this proposal is not a substitute. Fresh reviewers can be used when available; a single review claiming to check everything is insufficient.

1. **Protocol attacker.** Start from wire bytes, not happy-path code. Attempt truncation, overlength allocation, malformed types, interleaved writes, partial sends, reset races, half-close, slowloris behavior, and response backpressure. Produce minimal regression fixtures for each defect. Gate: complete framing/failure suite passes in all four pairings.
2. **Semantic and workload attacker.** Try to make both implementations agree on a wrong answer or perform unequal work. Use independent oracle/golden vectors, permutations, escape equivalence, hidden invalid values, integer extremes, late invalid fields, ownership overwrite, and retained-data rereads. Audit that lazy JSON traversal consumes/checks the complete required document. Gate: identical classifications and full results, zero partial commits.
3. **Lifetime/concurrency attacker.** Audit pending native I/O after cancellation, parser/connection ownership, simultaneous read/write, buffer reuse, shutdown, and retention eviction. Run native sanitizers and repeated churn. Gate: no known leaks, use-after-free, race-induced corruption, or deadlock; memory reaches a bounded steady range in sustained tests.
4. **Measurement attacker.** Inject a 25 ms and a 100 ms server pause, slow the client, stop reading replies, withhold a response, corrupt a result, and overflow the scheduled queue. Confirm the harness exposes scheduled latency, admission failures, bad results, censored requests, and lost telemetry. Check timing denominators, drain, histogram overflow, and sample-size claims. Gate: deliberately bad implementations cannot obtain a clean result.
5. **Fairness and drift attacker.** Audit compiled manifests/code paths side by side: same bytes, categories, validation, ownership, frame/window limits, ACK policy, CPU masks, socket buffers, warmup, and compiler/runtime configuration. Reject accidental C++ lazy extraction versus C# full materialization, or allocating C# versus reused C++ in the same row. Check for special handling of benchmark IDs/content. Gate: publish an equivalence table and every remaining asymmetry.
6. **Result adversary.** Inspect randomized paired trials, client headroom, saturation knees, GC correlations, excluded runs, thermal effects, and held-out corpus. Repeat surprising results with the other client and a fresh process. Gate: conclusions survive the specified checks or are narrowed to the supported observation.

For every finding record the failing command/seed, expected/actual behavior, root cause, fix, regression test, and affected result IDs. Fix the shared cause, rerun the relevant earlier gates, and repeat the affected adversarial pass. After changes, old measurements are stale. Freeze source/build/corpus hashes before final confirmation and review once more for post-review drift.

## 15. Deliverables and allowed conclusions

Deliver two interoperable client/server binaries and source, pinned build instructions, wire/workload specifications, deterministic corpus/oracle tools, correctness checks, an automated runner, raw result manifests/histograms, review evidence, and a generated report.

The report should show:

- Throughput versus scheduled p99/p99.9 at common absolute rates, split by workload and frame size.
- Maximum sustainable and deadline-constrained capacities with uncertainty and failed-request accounting.
- CPU seconds/record, memory, allocations, retained size, and queue occupancy.
- GC suspension/latency timelines for representative diagnostic runs, with client activity visible.
- Scaling, transport-only and processing-only controls, plus explicit loopback/generator limitations.
- Results for aggregate, retain/reuse, and retain/allocate in separate rows. No single overall score hiding these workloads.

A valid conclusion looks like: "On this Windows host, these pinned builds, this corpus, and this load, implementation A completed X% more records within a 5 ms deadline at Y memory/CPU cost." A blanket statement that C# or C++ always wins, that GC caused an untraced outlier, or that localhost results guarantee network/hard-real-time performance is not supported.

The minimal first milestone is a correct framed exchange with cross-language fixtures. The final milestone is the sustained, audited application benchmark above. No microbenchmark result substitutes for it.
