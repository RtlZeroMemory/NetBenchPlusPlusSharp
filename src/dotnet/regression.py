"""Failed-drain timing regression using the shared independent faulty peer."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from measurement import Peer

peer = Peer("withhold")
try:
    run = subprocess.run([
        "dotnet", str(ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"), "client",
        "--port", str(peer.port), "--corpus", str(ROOT / "tests/fixtures/golden.bin"),
        "--connections", "1", "--warmup", "0", "--duration", "0.1", "--rate", "100",
        "--window", "8", "--drain-seconds", "0.5",
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
finally:
    peer.close()

result = json.loads(run.stdout)
assert run.returncode != 0 and result["valid"] is False, result
assert result["generator_adequate"] is False, result
assert result["acknowledged"] == 0 and result["timedout"] > 0, result
assert result["configured_drain_seconds"] == .5, result
assert result["drain_limit_seconds"] == .5, result
assert result["drain_seconds"] >= .45, result
assert result["cohort_seconds"] >= .55, result
assert abs(result["cohort_seconds"] - result["window_seconds"] - result["drain_seconds"]) < .001, result
assert result["admitted"] == result["acknowledged"] + result["failed"] + result["unresolved"], result
assert len(result["size_latency"]) == 5, result
assert sum(hist["count"] for hist in result["size_latency"]) == result["acknowledged"], result
print(json.dumps({"check": "failed-drain-timing", "status": "passed",
                  "drain_seconds": result["drain_seconds"], "cohort_seconds": result["cohort_seconds"]}))
