"""Reproduce the human-facing first-pass tables from the frozen raw campaign."""
import csv
import gzip
import json
import shutil
import statistics as stats
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

root = Path(__file__).resolve().parents[2]
folder = Path(__file__).resolve().parent
out = root / 'docs/benchmarks'
out.mkdir(parents=True, exist_ok=True)
entries = json.loads((folder / 'results.json').read_text())
assert len(entries) == 54 and all(e['result']['valid'] for e in entries)
cases = [('aggregate', 65536, 'JSON totals · 64 KiB'),
         ('retain-reuse', 65536, 'Retain records · reuse'),
         ('retain-allocate', 65536, 'Retain records · allocate'),
         ('aggregate', 1048576, 'JSON totals · 1 MiB*')]


def select(mode, size, server, phase='closed-loop'):
    return [e for e in entries if e['scenario']['mode'] == mode
            and e['scenario']['frame_bytes'] == size and e['server'] == server
            and e['scenario']['phase'] == phase]


def resource(e):
    return e['server_result'].get('resources', e['server_result'])


def interval(values, digits=2):
    lo, hi = min(values), max(values)
    left, right = f'{lo:,.{digits}f}', f'{hi:,.{digits}f}'
    return left if left == right else f'{left}–{right}'


def rate(e, field):
    return e['result'][field] / e['result']['window_seconds']


table = ['| Workload | Server | Million records/s ↑ | JSON MiB/s ↑ | p50 latency ms ↓ | p99 latency ms ↓ | Peak RAM MiB |',
         '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
summary = []
for mode, size, label in cases:
    for server, name in [('csharp', 'C#'), ('cpp', 'C++')]:
        group = select(mode, size, server)
        assert len(group) == 3
        record_rates = [rate(e, 'window_records') / 1e6 for e in group]
        payload_rates = [rate(e, 'window_payload_bytes') / 1048576 for e in group]
        latency = {k: [e['result']['latency_ns'][k] / 1e6 for e in group] for k in ['p50', 'p99']}
        memory = max(resource(e).get('peak_working_set', resource(e).get('peak_working_set_bytes')) for e in group) / 1048576
        table.append(f'| {label} | {name} | {stats.median(record_rates):.3f} | {stats.median(payload_rates):,.0f} | {interval(latency["p50"])} | {interval(latency["p99"])} | {memory:.1f} |')
        summary.append(dict(workload=label, server=server, median_million_records_s=stats.median(record_rates),
                            record_rates=record_rates, median_payload_mib_s=stats.median(payload_rates),
                            latency_ms=latency, peak_working_set_mib=memory,
                            trials=[Path(e['path']).name for e in group]))

gc = ['| C# workload | Allocated MiB per server run | Gen 0 collections | Gen 1 collections | Gen 2 collections |',
      '| --- | ---: | ---: | ---: | ---: |']
for mode, size, label in cases:
    resources = [resource(e) for e in select(mode, size, 'csharp')]
    fields = [interval([r['allocated_bytes'] / 1048576 for r in resources], 1)]
    fields += [interval([r[k] for r in resources], 0) for k in ['gen0', 'gen1', 'gen2']]
    gc.append(f'| {label} | ' + ' | '.join(fields) + ' |')

ranges = ['| Workload | C# million records/s, min–max | C++ million records/s, min–max | Median paired C# / C++ ratio |',
          '| --- | ---: | ---: | ---: |']
alternate = ['| Workload | C# million records/s | C++ million records/s | C# p99 ms | C++ p99 ms |',
             '| --- | ---: | ---: | ---: | ---: |']
stages = ['| Workload / server | Receive p50 µs, min–max | Processing p50 µs, min–max | Samples per trial |',
          '| --- | ---: | ---: | ---: |']
for mode, size, label in cases:
    groups = {s: select(mode, size, s) for s in ['csharp', 'cpp']}
    ratios = []
    for pair in range(3):
        pair_rates = {s: rate(next(e for e in groups[s] if e['scenario']['pair'] == pair), 'window_records') for s in groups}
        ratios.append(pair_rates['csharp'] / pair_rates['cpp'])
    ranges.append(f'| {label} | ' + ' | '.join(interval([rate(e, 'window_records') / 1e6 for e in groups[s]], 3) for s in groups) + f' | {stats.median(ratios):.3f} |')
    alt = {s: select(mode, size, s, 'alternate-client')[0] for s in groups}
    alternate.append(f'| {label} | {rate(alt["csharp"], "window_records") / 1e6:.3f} | {rate(alt["cpp"], "window_records") / 1e6:.3f} | {alt["csharp"]["result"]["latency_ns"]["p99"] / 1e6:.2f} | {alt["cpp"]["result"]["latency_ns"]["p99"] / 1e6:.2f} |')
    if size == 65536:
        for server, name in [('csharp', 'C#'), ('cpp', 'C++')]:
            receive = [e['server_result'].get('receive_stage_ns', e['server_result'].get('receive_elapsed_ns')) for e in groups[server]]
            process = [e['server_result'].get('processing_stage_ns', e['server_result'].get('processing_elapsed_ns')) for e in groups[server]]
            stages.append(f'| {label} / {name} | {interval([h["p50_ns"] / 1000 for h in receive], 1)} | {interval([h["p50_ns"] / 1000 for h in process], 1)} | {interval([h["count"] for h in process], 0)} |')

transport = ['| Frame target / connections | C# server MiB/s | C++ server MiB/s |', '| --- | ---: | ---: |']
for size, connections in [(65536, 16), (1048576, 1)]:
    values = {s: rate(select('transport', size, s, 'transport-control')[0], 'window_payload_bytes') / 1048576 for s in ['csharp', 'cpp']}
    transport.append(f'| {size // 1024} KiB / {connections} | {values["csharp"]:,.0f} | {values["cpp"]:,.0f} |')

text = '''# First sustained benchmark pass — 29 September 2026

All **54 trials** reconciled successfully with zero failed or unresolved requests. The measured windows processed **8.96 billion JSON records** and transferred **1.66 TiB of payload**, including transport controls. There are **36 comparison-eligible closed-loop/control trials**. All **18 scheduled-load trials** failed the predeclared generator-timing gate and are retained as diagnostics, not latency/SLO comparisons.

For the three 64 KiB workloads, native application throughput was about **12–15% higher** by the ratio of server medians. With the common C++ client, C# had lower observed p99 latency, although allocation-mode tails varied substantially. This is an application-and-client comparison, not a universal language ranking or proof that GC caused any latency spike.

## Readable results

Throughput columns are medians of three runs. Latency columns show the **lowest–highest per-trial percentile**, without averaging or pooling percentiles. Peak RAM is the maximum observed server process working set across the three runs, including setup, warmup and shutdown; it excludes the separate client process. Higher throughput is better; lower latency is better.

'''
text += '\n'.join(table)
text += '''

Each JSON object is one record. “JSON totals” validates and aggregates directly; “reuse” keeps owned typed records in reusable storage; “allocate” creates fresh owned storage for each batch. p50 describes the middle completed batch; p99 describes the slow end (99% of acknowledged batches completed within that time). These are saturation latencies with up to 64 outstanding batches, not unloaded response times. Quantiles are histogram bucket upper bounds. MiB = 1,048,576 bytes.

The 64 KiB cases use 16 connections. **The 1 MiB case uses one connection and is diagnostic:** its throughput varied, paired results did not produce a consistent winner, and only 2,146–2,578 acknowledged batches occurred per primary trial. Its empirical p99 values are not precise tail estimates. Around 64 MiB can be queued in that configuration, so near-one-second batch latency must not be called a one-second GC pause. Both native and managed runs showed the effect, and the managed server recorded no GC in those trials. The cause of the low processed throughput needs a separate transport/queueing investigation.

## Allocation and GC observations

Ranges below cover the three primary managed server runs. They include setup, five seconds of warmup, the measured window, drain and shutdown. Allocation volume is cumulative memory allocated, not memory retained or peak RAM. Generation counters describe young (0), older (1) and full-heap (2) collections; do not add them as independent categories.

'''
text += '\n'.join(gc)
text += '''

Reuse sharply reduced managed allocation pressure. Fresh allocation produced about 62.7–65.5 GiB per server run and widely varying generation-2 counts. Counters alone do not establish pause duration or causality. C++ has no managed GC; native allocation/free counts were not instrumented. Runtime “last GC” observations are not a full pause timeline and are not reported as a benchmark-wide pause percentage.

## Variation and paired comparison

'''
text += '\n'.join(ranges)
text += '''

The paired ratio uses corresponding repetition IDs, so it need not equal the ratio of two separate medians. Three pairs are exploratory; no bootstrap confidence interval is claimed. The large-frame case illustrates why pairing and raw variation matter.

## Client and transport checks

One additional pair per workload used the C# client, under the same three-core client budget. These are sensitivity checks, not three-repeat confirmation:

'''
text += '\n'.join(alternate)
text += '''

Alternate-client runs showed different tails: managed aggregate p99 was 27.98 ms with the alternate client, versus 6.62–7.01 ms with the common native client. One additional pair cannot isolate the cause of that difference; do not attribute it to the server language alone.

The transport-only controls transferred the same payloads and acknowledged frames without JSON processing. They used the C++ client:

'''
text += '\n'.join(transport)
text += '''

The 64 KiB transport rates leave limited headroom over native aggregation. This experiment does not prove an isolated server ceiling. Do not subtract transport time/throughput from JSON results to derive parser cost. Native and managed server CPU/resource counters cover their full server lifetimes; client/process-only resource scopes differ and are not compared as measured CPU per record.

## Receiving versus processing

Server histograms sample every 1024th data frame. These are per-trial p50 ranges, not averaged or additive components:

'''
text += '\n'.join(stages)
text += '''

Receive elapsed includes waiting from the header-receive issue and is not CPU time spent copying bytes. Processing includes validation, categorization and, in retained modes, materialization and eviction verification. All stage p99 values remain gated off for insufficient samples. The 1 MiB case produced only two stage samples per trial and is omitted from this stage comparison.

## Scheduled-load limits

The three 64 KiB workloads were each offered 50%, 90% and 120% of the slower primary median frame rate, identically for both servers. All 18 trials reconciled complete offered demand and had no failed/unresolved requests, but every trial had at least one scheduled arrival more than 1 ms late. The strict predeclared rule therefore excludes all 18 from comparative SLO/latency evidence. Even the lightest affected run had one such event; the gate was not weakened after observing results. Rejections, timing misses and raw latency remain in the downloadable data. No sustainable-rate or real-time winner is established by this phase.

## Reproduction and evidence

- Native Windows 11 build 26200; Ryzen 7 9800X3D, 8 physical cores / 16 logical CPUs, 61.65 GiB RAM, 96 MiB L3; High Performance power plan.
- Identical server affinity: logical CPUs 0, 2, 4, 6 (four separate physical cores). Client: 8, 10, 12 (three separate physical cores). One physical core remains outside both masks; SMT siblings are not shared between client and server. Affinity does not isolate background Windows activity, shared cache, DRAM or power.
- C# Release: SDK `11.0.100-preview.7.26381.103`, runtime `11.0.0-preview.7.26381.103`, server/concurrent GC, pinned roll-forward disabled. C++23 Release: MSVC 19.51.36260, simdjson 3.12.3 (`icelake` implementation), Windows SDK 10.0.26100.0, no sanitizer.
- Thirty measured seconds plus five seconds of warmup per trial. Three randomized server-order pairs per primary workload, fixed C++ client, global window 64, global payload-in-flight limit 64 MiB, requested socket buffers 256 KiB, TCP_NODELAY. Four server CPU cores and three client CPU cores; the native server uses four IOCP workers and the managed server uses the runtime thread pool.
- Two deterministic seed-42 corpora, each approximately 256 MiB of unique payload (larger than L3). Corpus generation and loading preceded timing. Exact hashes and actual sizes are recorded in the trial data. Strict schema, digest/category results and retained ownership are common to both implementations.
- All reviews, builds, sanitizer/protocol tests and stress probes finished before this campaign. Ordinary desktop/background activity was not instrumented or isolated. These are loopback results for this PC and configuration.

```powershell
python bench/run.py --suite first --output results/my-first-pass
python bench/report.py results/my-first-pass
```

Artifacts: [all 54 trial rows](first-pass-trials.csv), [complete results JSON, gzip](first-pass-results.json.gz), [environment and source hashes](first-pass-environment.json), [structured summary](first-pass-summary.json), [review and fix evidence](../../reviews/round2/README.md). The gzip contains the unchanged campaign `results.json`, including raw histograms and server/client results. Local manifests, commands and logs remain under `results/first-benchmark-20260929/`.

Frozen C# DLL SHA-256: `cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd`.

Frozen C++ Release EXE SHA-256: `7e1957923dcdcd1cbb71e72e9373286677473681ec2cddea1500e2f8e144a329`.

Long confirmation/soak runs, CPU-budget and socket-buffer sensitivity, complete allocation tracing and GC-suspension attribution remain further experiments. This first pass makes no universal language or hard-real-time claim.
'''
(out / 'first-pass.md').write_text(text, encoding='utf-8')
(out / 'first-pass-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
(out / 'first-pass-results.json.gz').write_bytes(gzip.compress((folder / 'results.json').read_bytes(), mtime=0))
shutil.copyfile(folder / 'trials.csv', out / 'first-pass-trials.csv')
shutil.copyfile(folder / 'environment.json', out / 'first-pass-environment.json')
(folder / 'tables.md').write_text('\n'.join(table) + '\n\n' + '\n'.join(gc) + '\n', encoding='utf-8')
print('\n'.join(table))
print('\n'.join(gc))
print('report:', out / 'first-pass.md')
