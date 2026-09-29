"""Pinned processing-only A/B: python ab_process.py <label=command-prefix>... -- <corpus> <mode> [rounds]

Each round runs every binary once in a fresh process on the same single physical core (CPU 2),
alternating order, with 5 s warmup and 10 s measurement. Diagnostic only.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bench"))
import run  # noqa: E402

split = sys.argv.index("--")
binaries = dict(arg.split("=", 1) for arg in sys.argv[1:split])  # label=command[|ENV=VALUE]
import os
corpus, mode = sys.argv[split + 1], sys.argv[split + 2]
rounds = int(sys.argv[split + 3]) if len(sys.argv) > split + 3 else 2
mask = 1 << 2
results = {label: [] for label in binaries}
for r in range(rounds):
    order = list(binaries.items())
    if r % 2:
        order.reverse()
    for label, prefix in order:
        prefix, _, env = prefix.partition("|")
        key, _, value = env.partition("=")
        if key:
            os.environ[key] = value
        args = prefix.split() + ["process", "--corpus", corpus, "--mode", mode,
                                 "--duration", "10", "--warmup", "5"]
        out = HERE / "ab.log"
        with out.open("w") as log:
            p = run.spawn(args, mask, log)
            p.wait(timeout=600)
        if key:
            os.environ.pop(key)
        line = [l for l in out.read_text(encoding="utf-8-sig").splitlines() if l.startswith("{")][-1]
        d = json.loads(line)
        rate = d.get("records_per_second")
        results[label].append(round(rate / 1e6, 3))
        print(json.dumps({"label": label, "mode": mode, "corpus": Path(corpus).name,
                          "records_per_second_M": round(rate / 1e6, 3)}), flush=True)
print(json.dumps(results))
