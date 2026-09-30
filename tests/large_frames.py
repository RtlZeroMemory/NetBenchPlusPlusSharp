"""Large-frame and alternate-arrival integration through the runner (correctness, not performance)."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="both servers use their fast parser")
    args = parser.parse_args()
    folder = ROOT / "results" / f"large-frames-{'fast' if args.fast else 'library'}-{time.strftime('%Y%m%d-%H%M%S')}"
    matrix = json.loads((ROOT / "bench/matrices/large-frames.json").read_text(encoding="utf-8"))
    if args.fast:
        matrix.setdefault("defaults", {}).update({"csharp_parser": "fast", "cpp_parser": "fast"})
    folder.parent.mkdir(exist_ok=True)
    matrix_path = folder.parent / f"{folder.name}-matrix.json"
    matrix_path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "bench/run.py"), "--matrix", str(matrix_path),
                    "--output", str(folder), "--cooldown", "0.2"], check=True)
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    failures = [(r["cell"]["name"], r["server"], r["runner_error"]) for r in results if r["runner_error"]]
    assert not failures, failures
    for r in results:
        counts = r["result"]["counts"]
        assert r["result"]["valid"] and counts["acknowledged"] == r["server_result"]["measured"]["frames"], r["path"]
    print(json.dumps({"passed_trials": len(results), "results": str(folder)}))


if __name__ == "__main__":
    main()
