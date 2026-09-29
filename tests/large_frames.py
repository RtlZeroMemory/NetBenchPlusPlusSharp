"""Large-frame and alternate-arrival integration through the runner (correctness, not performance)."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / "results" / f"large-frames-{time.strftime('%Y%m%d-%H%M%S')}"
    subprocess.run([sys.executable, str(ROOT / "bench/run.py"), "--matrix", str(ROOT / "bench/matrices/large-frames.json"),
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
