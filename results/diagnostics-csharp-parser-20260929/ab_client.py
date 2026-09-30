"""Paced-load check of client builds (diagnostic): python ab_client.py <rate> label=client.exe ...

Fixed C# server (library parser) and the EASY 64 KiB corpus; only the C++ load-generator binary changes.
Prints admitted/rejected counts and the client-side delay for each build.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bench"))
import run  # noqa: E402

rate = float(sys.argv[1])
matrix = json.loads((HERE.parents[1] / "bench/matrices/campaign.json").read_text(encoding="utf-8"))
matrix["corpora"] = {"easy64k": matrix["corpora"]["easy64k"]}
corpora = run.prepare_corpora(matrix)
masks = run.cpu_layout(4)
folder = HERE / "ab-client"
folder.mkdir(exist_ok=True)
original = dict(run.BINARIES)
for ordinal, arg in enumerate(sys.argv[2:], 1):
    label, exe = arg.split("=", 1)
    cell = run.DEFAULTS | matrix["defaults"] | {
        "name": f"paced-{label}", "phase": "ab-client", "corpus": "easy64k", "mode": "aggregate", "duration": 10,
        "warmup": 5, "rate": rate, "arrival": "steady", "servers": ["csharp"], "seed": 42, "repetition": 0}
    run.BINARIES["cpp"] = Path(exe).resolve()
    try:
        entry = run.trial(folder, int(1000 * rate) % 9000 + ordinal, cell, "csharp", masks, corpora["easy64k"])
    finally:
        run.BINARIES.update(original)
    r = entry["result"] or {}
    counts, latency = r.get("counts", {}), r.get("latency", {})
    print(json.dumps({"client": label, "error": entry["runner_error"], "offered": counts.get("offered"),
                      "rejected": counts.get("rejected"),
                      "client_delay_p99_ms": (latency.get("client_delay", {}).get("p99_ns") or 0) / 1e6,
                      "p99_ms": (latency.get("scheduled", {}).get("p99_ns") or 0) / 1e6}), flush=True)
