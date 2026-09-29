"""Run existing shared checks without overwriting historical review evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
DLL = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
sys.path.insert(0, str(ROOT / "tests"))
import measurement
import protocol

before = hashlib.sha256(DLL.read_bytes()).hexdigest()
folder = Path(__file__).resolve().parent / f"{before[:12]}-{time.time_ns()}"
folder.mkdir(exist_ok=False)
results = []

for name, args in [
    ("selftest-golden", ["dotnet", str(DLL), "selftest", "--corpus", "tests/fixtures/golden.bin"]),
    ("selftest-smoke", ["dotnet", str(DLL), "selftest", "--corpus", "data/smoke.bin"]),
    ("failed-drain", [sys.executable, "src/dotnet/regression.py"]),
    ("server-regressions", [sys.executable, "src/dotnet/server_regression.py", "--output-dir", str(folder / "server")]),
    ("histogram-probe", ["dotnet", "reviews/csharp-measurement/bin/Release/net11.0/Probe.dll", str(DLL)]),
    ("format", ["dotnet", "format", "whitespace", "src/dotnet/Bench.csproj", "--no-restore", "--verify-no-changes"]),
]:
    run = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    (folder / f"{name}.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    assert run.returncode == 0, (name, run.stdout, run.stderr)
    results.append({"check": name, "status": "passed"})
    print(json.dumps(results[-1]), flush=True)

count = protocol.oracle_checks()
for mode in ("aggregate", "retain-reuse", "retain-allocate"):
    process, log, port = protocol.start_server(DLL, mode, folder / "protocol")
    try:
        passed = protocol.run_protocol(port, mode)
        count += passed
        results.append({"check": f"protocol-{mode}", "passed": passed})
        print(json.dumps(results[-1]), flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        log.close()

(folder / "measurement").mkdir()
for fault in ("none", "pause", "overload", "drop", "withhold", "corrupt", "wrong-sequence", "oversized-response", "wrong-summary", "ack-inside-drain"):
    results.append(measurement.run(DLL, fault, folder / "measurement"))
    print(json.dumps(results[-1]), flush=True)

assert hashlib.sha256(DLL.read_bytes()).hexdigest() == before
evidence = {"binary_sha256": before, "protocol_checks": count, "results": results}
(folder / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"binary_sha256": before, "protocol_checks": count, "status": "passed"}))
