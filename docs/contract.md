# Protocol and benchmark contract v2

The shared reference for both implementations. Both executables provide `server`, `client`, `process` and `selftest`. Options use `--name value`; unknown or duplicate options and invalid ranges fail with a nonzero exit code. v2 keeps the v1 wire format, JSON rules, digest and corpus format unchanged; it changes the closed-loop load model, deadline checking, timing boundaries and the result schema.

## Wire

All integers are big endian. Header (16 bytes): payload length u32, version u16 (= 1), type u16, sequence u64. Requests start at sequence 1 and increase by exactly one, controls included; responses echo the sequence with type | 0x8000. Hard body limit 16,777,216 bytes. EOF between frames is a clean close; EOF inside one is truncation. No retries, no resynchronization: any framing, validation or deadline failure closes the connection without a success response.

| Type | Request body | Response body |
| --- | --- | --- |
| Begin (1) | exactly 32 manifest-hash bytes; must match the server's `--manifest-hash` (default 32 zero bytes) | empty. Resets epoch counters, keeps retained data. Not allowed inside an epoch. |
| JsonBatch (2) | nonempty JSON, at most `--max-frame` bytes, only inside an epoch | 16 bytes: records u64, batch digest u64 |
| End (3) | empty, only inside an epoch | 1,056 bytes: batches u64, payload bytes u64, records u64, epoch digest u64, 64 bucket counts u64, 64 bucket sums i64 |

The epoch digest is FNV-1a 64 over each successful data request's sequence u64, records u64 and batch digest u64, starting at the FNV offset. An epoch holds at most 10⁹ records. In `transport` mode bodies are received but not parsed: records 0, digest = payload length, buckets zero.

## Strict JSON and canonical digest

The root is an array of objects with exactly seven fields in any order, compared after unescaping (escaped names cannot hide unknown or duplicate fields): `id` u64, `timestamp_ns` u64, `source` u32, `kind` `"kind00"`…`"kind15"`, `value_milli` i64 in [−1,000,000, 1,000,000], `flags` u32, `message` string. Integers are JSON integer lexemes only (no fraction, exponent, quotes or leading zeros); unsigned fields reject any minus sign, `value_milli` accepts `-0`. Strings must be valid UTF-8 with valid escapes and no lone surrogates; a decoded message is at most 4,096 bytes. No BOM, comments, trailing commas, extra roots or non-whitespace after the root; depth at most 4 with the root array at depth 1.

FNV-1a 64 (offset 14695981039346656037, prime 1099511628211, wrapping). Per batch, start at the offset; for each record in array order hash byte 0x52, id u64, timestamp_ns u64, source u32, kind index u8, value_milli i64 (two's complement), flags u32, decoded message length u32, decoded message bytes, all big endian. Bucket = kind × 4 + (flags & 3): count += 1, sum += value_milli. Canonical retained size = 38 + decoded message length.

## Processing modes

| Mode | Work per batch |
| --- | --- |
| `aggregate` | Validate and decode every field; digest and buckets computed directly. |
| `retain-reuse` | Materialize owned typed rows and decoded UTF-8 text, compute the result from those rows, retain the batch, and recycle evicted storage. |
| `retain-allocate` | The same with fresh storage for every batch; evicted storage is released. |
| `transport` | Receive bodies only; a diagnostic control. |

A batch commits only after it is completely valid. Retention keeps at most `--retain-batches` batches and `--retain-bytes` canonical bytes per connection; the oldest batches are evicted as needed, and every planned eviction is revisited and verified against its stored digest before any retained or epoch state changes. A batch larger than the byte limit fails without changing state. Retained batches are verified again at End and at a clean EOF. Owned storage is chunked identically in both implementations: the first row chunk grows from 16 to 1,024 rows by doubling and later row chunks hold 1,024 rows; the first text chunk grows geometrically from max(256, message) to 65,536 bytes and later text chunks hold 65,536 bytes; a message never straddles chunks (no single allocation exceeds 64 KiB, so nothing reaches the .NET large-object heap); the owned-capacity peak counts retained, spare and incoming scratch capacity together (48 bytes per row plus text capacity), excluding parser and runtime memory.

## Deadlines

A frame deadline starts at a frame's first header byte (`--frame-timeout-ms`, default 30,000) and an idle deadline at the last byte of progress (`--idle-timeout-ms`, default 5,000); the earlier applies through receiving, processing, retained verification and response writing. A quiet connection between frames has no deadline. Both servers check deadlines at every socket completion, from a 10 ms scanner thread, and cooperatively during processing (before work, every 1,024 records, before commit). Expired work never commits and never receives a success response. Shutdown is observed at the same points. These are cooperative checks, not hard real-time preemption.

## Commands

* `server --port 9000 --mode aggregate --max-frame 1048576 --max-connections 32 --workers 4 --manifest-hash <64 hex> --idle-timeout-ms 5000 --frame-timeout-ms 30000 --retain-batches 8 --retain-bytes 67108864 --socket-buffer 262144 [--output file] [--run-seconds N] [--control-stdin 1] [--pause-every N --pause-ms M] [--io-cap N]`. Binds 127.0.0.1 and prints one `{"event":"ready",…}` line. `--socket-buffer 0` keeps Windows autotuning; any other value sets both SO_SNDBUF and SO_RCVBUF. TCP_NODELAY is always on. `--control-stdin 1` stops on a `stop` line or EOF; final metrics are written after all connections close. `--pause-every/--pause-ms` inject a budget-checked busy spin before processing every Nth data frame (diagnostic). `--io-cap` limits every socket operation (diagnostic partial I/O). `--workers` sets native IOCP threads; the managed server records it and uses the runtime thread pool, with the CPU mask as the budget.
* `client --port 9000 --corpus file --mode aggregate --connections 4 --window 8 --inflight-bytes 67108864 --duration 10 --warmup 2 --rate 0 --arrival steady --seed 42 --drain-seconds 30 --socket-buffer 262144 [--manifest-hash …] [--output file] [--io-cap N]`.
* `process --corpus file --mode aggregate --duration 5 --warmup 1 [--retain-batches …] [--retain-bytes …] [--output file]`: full-corpus cycles through the server's processor, no sockets; every batch's records, digest, counts and sums are checked against the corpus metadata.
* `selftest [--corpus file]`: canonical digest (shared golden value 3660725836179230917), strict JSON cases, atomic rejection, budgets, retention and eviction atomicity, histogram and schedule arithmetic; with a corpus, a verified full pass per JSON mode.

## Load generation

Each client connection has one sender and one acknowledgement reader. Corpus frames are chosen by a connection-local cursor (start = connection index, advance by `connections` per offered request, persisting across warmup and measurement).

* **Closed loop** (`--rate 0`): each connection keeps `window / connections` requests outstanding (the window must be a multiple of the connection count). A request is admitted by its own connection when an acknowledgement frees a slot; its intended time is its admission, immediately before sending.
* **Scheduled** (`--rate R`, frames per second across all connections): arrivals are independent of acknowledgements and assigned round-robin to connections. Steady arrival i is at T0 + i/R; Poisson uses SplitMix64 (seed `--seed`) with the exponential transform; burst repeats 5 s at 0.6R and 1 s at 1.5R. Poisson and burst schedules are precomputed (10-million-arrival limit). A global cap of `window` frames and `inflight-bytes` bytes admits or rejects each arrival at its scheduled time; rejected arrivals still advance cursors and count as misses. Pacing uses a process-local high-resolution waitable timer and at most 1 ms of final spinning on a raised-priority thread.

Phases: warmup and measurement each exchange Begin on every connection, start data at T0 = now + 100 ms, stop admitting at T1 = T0 + duration (an arrival intended before T1 but still undispatched at T1 + 1 ms is `generator_late_rejected`), and allow admitted data until T1 + `--drain-seconds`. Healthy phases then wait until T1 and exchange End on every connection, verifying the full summary. On the drain deadline or any failure, all sockets are shut down and pending I/O cancelled. Early failure keeps the complete intended schedule: undispatched arrivals are counted as `aborted_schedule_rejected`. Accounting: `offered = admitted + rejected`, `admitted = acknowledged + failed + unresolved`, `timedout ⊆ unresolved`; `failed` counts wrong acknowledgements, `unresolved` requests without a response.

## Timing

QPC/Stopwatch ticks only. Scheduled latency = acknowledgement received − intended time; submitted latency = acknowledgement − first send; client delay = first send − intended; generator lateness = dispatch − intended (scheduled runs only). Histograms are shared log-linear: exact nanoseconds through 1,023, then 256 buckets per power of two; values of 60 s or more count as overflow with no finite bucket; a quantile is the bucket upper bound at rank ⌈q·count⌉, and `null` when empty or in overflow. Five size classes: ≤4 KiB, ≤64 KiB, ≤256 KiB, ≤1 MiB, larger.

Server stages sample data frame 1,024, 2,048, … of each epoch; the last epoch of each connection is merged at close. **Receive** runs from the previous response's completion (or connection start) to the last body byte and includes waiting for the client; **processing** runs from the last body byte to the response being ready; **decode** covers parsing (and, in aggregate, the digest and buckets); **retained visit** covers the retained-row visit and eviction verification. Stage p99 is `null` below 10,000 samples and p99.9 below 1,000,000.

## Result schema

Both implementations write the same keys (histograms: `count`, `p50_ns`, `p90_ns`, `p99_ns`, `p999_ns`, `max_ns`, `overflow_60s`, `buckets` as `[upper_ns, count]`).

* Client: `role`, `implementation`, `mode`, `valid`, `error`, `load_model`, `config`, `corpus`, `counts` (offered, admitted, rejected, acknowledged, failed, unresolved, timedout, generator_late_rejected, aborted_schedule_rejected), `window` (seconds, frames, records, payload_bytes inside [T0, T1)), `cohort` (all measured requests through the last data acknowledgement, with drain_seconds and drain_limit_seconds), `latency` (scheduled, submitted, client_delay, by_size), `misses` (over 1/5/10 ms, including rejected, failed and unresolved), `generator` (lateness, late_over_1ms, queue high-water marks for scheduled runs), `resources` (measured phase, Begin through End: seconds, cpu_seconds, logical_cpus, allocations or allocated_bytes, gc, peak working set), `io`, `build`.
* Server: `role`, `implementation`, `mode`, `valid`, `error`, `config`, `connections` (accepted, rejected, graceful, closed reasons), `lifetime` (whole process), `measured` (first Begin to last End across connections: complete, seconds, frames, records, payload_bytes, cpu_seconds, allocations, allocated_bytes, gc, working set), `retention`, `io`, `stages`, `build`. Native `allocations`/`allocated_bytes` count global `operator new` calls and bytes; managed `allocated_bytes` is `GC.GetTotalAllocatedBytes(precise)` and `allocations` is null; `gc` is null for native code.
* Process: `role`, `implementation`, `mode`, `valid`, `seconds`, `frames`, `records`, `payload_bytes`, `records_per_second`, `payload_mib_per_second`, `resources`, `retention`, `build`.

## Corpus format (`.bin`)

Header: ASCII `TCPBCH01`, frame count u32, reserved u32 = 0. Per frame: payload length u32, expected records u64, expected digest u64, 64 bucket counts u64, 64 bucket sums i64 (1,044 metadata bytes), then the raw UTF-8 payload. Lengths and counts are validated before allocation; truncated or trailing bytes are rejected. `tools/corpus.py` generates corpora (profiles `mixed`, `easy`, `hard`; optional log-uniform frame sizes) and computes every expectation with an independent Python oracle.
