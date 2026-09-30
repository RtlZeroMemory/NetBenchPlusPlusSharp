# Rework campaign methodology (declared before measurement)

This page fixes the policies, matrix and interpretation rules for the rework campaign **before** any of its trials run. The first-pass results in [first-pass.md](first-pass.md) keep their original classification under the original policy; nothing here reclassifies them. The matrix is [bench/matrices/campaign.json](../../bench/matrices/campaign.json).

## What the diagnostics established

Controlled diagnostics against the frozen first-pass binaries (`results/diagnostics-baseline-20260929/`) and the rebuilt binaries (`results/diagnostics-rework-20260929/`, `results/diagnostics-fairness-20260929/`):

1. **The common native client was saturated.** It used about 2.9 of its 3 cores in every 64 KiB run, including 50%-load scheduled runs: 33 threads shared one mutex and woke each other with `notify_all`. Its `client_delay` p99 (17–24 ms) was most of the reported "C++ server p99". Transport controls were client-limited: a fourth client core raised them by about 30%.
2. **Closed-loop tails measured client queueing.** The global window was assigned round-robin regardless of each connection's backlog, so requests queued inside the client behind slow connections.
3. **Inline completion loops starve connections.** When a worker finds a connection's next frame already buffered it keeps serving that connection. With the rebuilt servers this gave the native server a 0.64 ms p99 but a 560 ms p99.9, and the C# server 10–65 ms p99 with sampled processing maxima of 10–65 ms for ~100 µs of work (preemption; C# allocated nothing and ran no GC). After making both servers requeue each connection after every response, EASY aggregate tails fell to p99 1.98 ms / p99.9 2.3–2.6 ms (native) and p99 2.8–5.6 ms (C#) with unchanged native throughput.
4. **The 1 MiB/one-connection anomaly is TCP flow control.** With no JSON at all (transport mode plus a 2 ms server pause per frame), pipelining 1 MiB frames through 256 KiB buffers gave 94–96 MiB/s on both servers versus ~258 MiB/s at window 1; real 1 MiB JSON showed the same collapse (96–106 MiB/s). 4 MiB buffers removed the collapse and the ~18 ms p99 transfer stalls seen at window 1 (p99 3.7 ms). Near-one-second first-pass latencies were 64 queued frames × ~15 ms, not a pause.
5. **The strict generator gate could not pass.** One arrival more than 1 ms late on a desktop OS failed a whole trial, while client CPU starvation, which does distort latency, was unchecked.
6. **The diagnostic pause was quantized** to the ~15.6 ms Windows timer in both servers; it is now a budget-checked busy spin.

## Optimization before the final campaign (declared before its first trial)

A first run of this campaign ([`results/rework-campaign-20260929`](../../results/rework-campaign-20260929/NOTE.md), 158 trials, no failures) was stopped to optimize the C# implementation. Each change was A/B-tested in fresh interleaved processes ([`results/diagnostics-csharp-parser-20260929`](../../results/diagnostics-csharp-parser-20260929)):

- **C# parsing:** unescaped names, `kind` values and messages are used in place instead of copied through `CopyString`; every accept/reject decision is unchanged. Processing-only, one core: EASY aggregate 3.85 → 4.33–4.52 M records/s (C++ 4.39), HARD content unchanged (0.69).
- **Where the HARD gap is** (ablations, per 766-byte record): the FNV-1a digest costs both languages about 0.3 µs. Excluding it, C# parsing takes 1.14 µs against 0.67 µs for simdjson, and `Utf8JsonReader.Read()` tokenizing alone takes 0.62 µs. Two hand-written unescapers were 7–13% slower than `CopyString`, and 512-bit vectors (`DOTNET_PreferredVectorBitWidth=512`) made no consistent difference. All three were discarded.
- **Chunked batch storage in both languages** (no allocation over 64 KiB, so nothing reaches the .NET large-object heap). C++ throughput was unchanged (2.44–2.47 vs 2.45–2.51 M records/s over TCP) with 38% fewer bytes allocated.
- **C# server GC: DATAS off.** DATAS is the dynamic heap adaptation that .NET 9+ enables by default. Neither chunking nor DATAS off helped alone: chunking alone doubled GC pause time, because surviving small-heap objects are copied during promotion; DATAS off alone raised p99 to about 25 ms. Together, HARD retain-allocate saturation went from 1.72–1.79 to 1.81–1.84 M records/s, p99 from 20–21 ms to 16–17 ms, and full collections from about 600 to 0, at about 2.5× peak memory (1.0 GiB vs 0.4 GiB). The `csharp-default-gc` phase reruns the two HARD retain-allocate cells with the runtime default so this trade-off is measured, not assumed.

## Fixed campaign settings

- CPU layout: physical core 0 (which services most interrupts) is left to Windows and the runner; the server gets physical cores 1–4 and the client cores 5–7, one logical CPU each; SMT siblings are left idle by the benchmark. Processing-only controls run single-threaded under the server's 4-core mask so each runtime keeps its server configuration (.NET falls back to workstation GC with one CPU).
- Socket buffers 4 MiB on both ends (decided from diagnostic 4, which showed 256 KiB buffers distort frames up to 1 MiB for both servers equally). TCP_NODELAY on.
- Both servers requeue a connection after each response (native: IOCP post; C#: `Task.Yield`). Synchronous completions are handled inline within a frame, as .NET does.
- Native Release without `/GL`/`/LTCG` (measured ~30% slower JSON processing with them).

## Load models

- **Closed loop** (`--rate 0`): every connection independently keeps `window / connections` requests outstanding (16 connections × 4). Latency starts when a connection admits a request, immediately before sending. Closed-loop latency is saturation latency at a fixed depth, never unloaded latency.
- **Scheduled** (`--rate R`): arrivals come from a steady, Poisson or burst schedule built before T0, independent of replies, assigned round-robin to connections. A global cap (`window` frames and `inflight-bytes`) admits or rejects each arrival at its scheduled time. Latency starts at the intended arrival time, so generator and send-path delays are charged to the result (no coordinated omission).

## Generator and client adequacy, policy v2

A scheduled trial is adequate only if all of the following hold:

1. It is valid: every admitted request acknowledged and verified, End summaries verified, server measured interval complete.
2. The complete intended schedule was dispatched: no `generator_late_rejected` or `aborted_schedule_rejected` arrivals. An arrival is `generator_late_rejected` only if it is still undispatched 1 ms after T1; smaller jitter at the window edge is ordinary lateness under criteria 3 and 4. (Clarified before any campaign trial, after a review showed that one arrival scheduled microseconds before T1 could otherwise exclude every repetition of a steady cell; no earlier trial had such an arrival.)
3. Dispatch lateness p99 ≤ 100 µs (histogram upper bound; a missing p99 with arrivals fails).
4. At most 0.1% of planned arrivals were dispatched more than 1 ms late.
5. Client CPU over the measured phase ≤ 85% of the client's logical-CPU budget.

6. Background CPU (everything except the trial, over all logical CPUs) stays at or below 15%. Added on 30 September 2026 after other work on the PC invalidated the paced phases of the fair 2x2 campaign; quiet campaigns have a median of 4-7% and a 90th percentile of about 11%. It applies to reports generated from then on and only excludes trials.

A closed-loop trial is adequate if it is valid and meets criteria 5 and 6. Transport-only controls are exempt from criterion 5: the diagnostics showed them client-bound (a fourth client core raised them about 30%), so they are reported as lower bounds on transport capacity and compared in MiB/s. (Declared before any campaign trial.) Headroom is additionally checked by rerunning two key cells with a fourth client core (`client-headroom` phase).

Paced rates are a fraction of the slower server's capacity, taken from saturation trials whose client stayed within its CPU budget. If a server outran the client in every saturation trial, its measured rate is only a lower bound on its capacity; the paced rate then uses the lowest rate both servers are known to sustain, and each paced cell records that basis (`rate_basis`). This rule was added before the fair 2x2 campaign, after the specialized parsers made a server faster than the 3-core load generator.

Rationale: reported latencies are measured from intended arrival, so generator delay is charged to the server. Criteria 3 and 4 bound that charge: at most 1% of samples can carry more than 100 µs of generator delay and at most 0.1% more than 1 ms, so the generator cannot determine a reported p99 on its own. Criterion 5 catches the failure that distorted the first pass, a CPU-starved client. The client's send-path delay (`client_delay`) is shown for every trial but not gated, because it also contains legitimate server backpressure. The first-pass rule (zero arrivals over 1 ms late) is still reported as `strict_v1_adequate`. These thresholds were fixed after the first pass and the diagnostics, and before any campaign trial; they will not be changed after seeing campaign results.

## Measurement scopes

| Counter | Scope |
| --- | --- |
| Server `measured` | First Begin to last End across connections: CPU, allocations (C#: managed bytes; C++: global `operator new` calls and bytes), GC counts and pause time, and the frames/records processed in that interval. Per-record CPU and allocation use these matching denominators. Paced cells include each runtime's idle behaviour (e.g. thread-pool spinning) in CPU per frame. |
| Server `lifetime` | Whole process, including setup, warmup and shutdown; peak working set. |
| Client `resources` | Measured phase, Begin through End controls. |
| Client `window` | Acknowledgements inside [T0, T1): the throughput numerator. |
| Client `cohort` | All measured requests through the last data acknowledgement; drain is shown separately. |
| Processing-only | Timed full-corpus cycles after warmup, one thread. |
| Background | Whole-machine busy CPU minus the trial's own processes, and foreign busy time on the server cores' SMT siblings, per trial. |

## Interpretation rules

- A trial enters comparisons only if both members of its repetition pair are individually valid and adequate. Throughput medians and latency ranges use only such trials.
- Throughput tables show medians across trials. Latency tables show the lowest–highest per-trial percentile over offered requests, with rejected, failed and unresolved requests counted as misses. Percentiles are never averaged or pooled.
- p99 is shown with its per-trial sample count; p99.9 is not claimed below one million samples per trial.
- Paired C#/C++ ratios are computed per repetition and summarized by their median; bootstrap intervals over pairs are rough indications. Throughput ratios are used only where throughput is not fixed by the offered rate; paced cells compare p99.
- Different corpora, durations, warmups, windows, buffer sizes, CPU masks or binaries are never pooled; the runner keys every trial on its resolved cell and binary hashes.
- Processing-only and transport controls explain end-to-end results; they are not subtracted from them. Transport controls are client-limited on this host and are labelled so.
- GC is named as a cause only with GC pause evidence from the same interval; counts alone are observations.
