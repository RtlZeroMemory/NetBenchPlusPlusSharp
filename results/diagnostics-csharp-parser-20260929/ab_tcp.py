"""Interleaved TCP A/B of server builds (.dll = C#, .exe = C++) (diagnostic): python ab_tcp.py <cell> <rounds> label=dll[:ENV=VALUE]...

Uses the campaign runner's trial() with the declared campaign defaults and corpora; only the C# server
binary (and optionally one runtime environment variable) changes between variants. The C++ client is
fixed. Results: ab-tcp/<cell>/, summary on stdout.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bench"))
import report  # noqa: E402
import run  # noqa: E402

CELLS = {
    "hard-allocate": {"corpus": "hardmix", "mode": "retain-allocate", "retain_batches": 64, "retain_bytes": 16777216,
                      "duration": 30, "warmup": 20},
    "hard-reuse": {"corpus": "hardmix", "mode": "retain-reuse", "retain_batches": 64, "retain_bytes": 16777216,
                   "duration": 30, "warmup": 20},
    "easy-aggregate": {"corpus": "easy64k", "mode": "aggregate", "duration": 30, "warmup": 15},
}
name, rounds = sys.argv[1], int(sys.argv[2])
variants = []
for arg in sys.argv[3:]:
    label, spec = arg.split("=", 1)
    dll, _, env = spec.partition(":")
    variants.append((label, Path(dll).resolve(), env))
matrix = json.loads((HERE.parents[1] / "bench/matrices/campaign.json").read_text(encoding="utf-8"))
matrix["corpora"] = {CELLS[name]["corpus"]: matrix["corpora"][CELLS[name]["corpus"]]}
corpora = run.prepare_corpora(matrix)
masks = run.cpu_layout(matrix.get("server_cores", 4))
folder = HERE / "ab-tcp" / name
folder.mkdir(parents=True, exist_ok=True)
original = dict(run.BINARIES)
summary = {label: [] for label, _, _ in variants}
ordinal = 0
for r in range(rounds):
    order = variants if r % 2 == 0 else list(reversed(variants))
    for label, dll, env in order:
        ordinal += 1
        server = "cpp" if dll.suffix == ".exe" else "csharp"  # a C++ variant also serves as the (unchanged) client
        cell = run.DEFAULTS | matrix["defaults"] | CELLS[name] | {"name": f"{name}-{label}", "phase": "ab",
                                                                   "servers": [server], "seed": 42, "repetition": r}
        run.BINARIES[server] = dll
        key, _, value = env.partition("=")
        if key:
            os.environ[key] = value
        try:
            entry = run.trial(folder, ordinal, cell, server, masks, corpora[cell["corpus"]])
        finally:
            run.BINARIES.update(original)
            if key:
                os.environ.pop(key, None)
        entry["key"] = label
        row = report.row(entry)
        summary[label].append({k: row[k] for k in ("records_per_second", "p99_ms", "server_cores_used", "gc_gen0",
                                                    "gc_gen1", "gc_gen2", "gc_pause_ms", "allocated_bytes_per_record",
                                                    "server_peak_working_set_mib", "exclusion")})
        print(json.dumps({"label": label, "round": r, **summary[label][-1]}), flush=True)
(folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
