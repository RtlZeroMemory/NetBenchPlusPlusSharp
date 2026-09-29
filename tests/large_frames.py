"""Targeted large-body and alternate-arrival integration checks (not performance evidence)."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import run


def main():
    folder = ROOT / "results" / f"large-frames-{time.time_ns()}"
    folder.mkdir(parents=True)
    executables, masks = run.binaries(), run.cpu_masks()
    rows, ordinal = [], 0
    for size, modes in [(1048576, ["aggregate", "retain-reuse", "retain-allocate"]),
                        (16777216, ["aggregate", "retain-reuse", "retain-allocate", "transport"])]:
        path = ROOT / "data" / f"integration-frame-{size}.bin"
        info = run.corpus.write_frames(path, run.corpus.generate_payloads(1, size, 8675309))
        for mode in modes:
            scenario = {"mode": mode, "frame_bytes": size, "connections": 4,
                "duration": 0.5, "warmup": 0.1, "window": 64, "rate": 0,
                "arrival": "steady", "seed": 42, "corpus": str(path),
                "corpus_sha256": info["sha256"], "corpus_payload_bytes": info["payload_bytes"],
                "phase": "large-frame-correctness", "pair": 0}
            for server, client in [("csharp", "cpp"), ("cpp", "csharp")]:
                ordinal += 1
                rows.append(run.trial(folder, executables, masks, scenario, server, client, ordinal))
    # A full burst period must cross both the low and high rate sections.
    path = ROOT / "tests/fixtures/golden.bin"
    for arrival in ["poisson", "burst"]:
        scenario = {"mode": "aggregate", "frame_bytes": 65536, "connections": 4,
            "duration": 6.2 if arrival == "burst" else 1, "warmup": 0,
            "window": 64, "rate": 100, "arrival": arrival, "seed": 42,
            "corpus": str(path), "corpus_sha256": run.sha256(path),
            "corpus_payload_bytes": sum(len(payload) for payload, _ in run.corpus.read_corpus(path)),
            "phase": "arrival-correctness", "pair": 0}
        for server, client in [("csharp", "cpp"), ("cpp", "csharp")]:
            ordinal += 1
            rows.append(run.trial(folder, executables, masks, scenario, server, client, ordinal))
    (folder / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps({"passed_trials": len(rows), "results": str(folder)}))


if __name__ == "__main__":
    main()
