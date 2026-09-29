"""Small independent checks for the corpus, affinity inheritance, and runner accounting."""
import json
import csv
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bench"))
import corpus
import run
import report as report_tool


def main():
    folder = ROOT / "results/harness"
    folder.mkdir(parents=True, exist_ok=True)
    first, second = folder / "first.bin", folder / "second.bin"
    left = corpus.write_frames(first, corpus.generate_payloads(16384, 4096, 123))
    right = corpus.write_frames(second, corpus.generate_payloads(16384, 4096, 123))
    assert left == right and first.read_bytes() == second.read_bytes()
    for payload, expected in corpus.read_corpus(first):
        actual = corpus.process(payload)
        assert all(actual[key] == value for key, value in expected.items())
    malformed = folder / "truncated.bin"
    malformed.write_bytes(first.read_bytes()[:-1])
    try:
        list(corpus.read_corpus(malformed))
    except ValueError:
        pass
    else:
        raise AssertionError("Truncated corpus accepted")
    report_folder = folder / "report-gates"
    report_folder.mkdir(exist_ok=True)
    entries = []
    for pair in range(6):
        for server in ["csharp", "cpp"]:
            result = {"valid": True, "success": True, "generator_adequate": True,
                      "sustainable": False, "window_seconds": 1, "window_completed_frames": 100,
                      "completed_frames": 100, "offered": 120, "admitted": 100, "rejected": 20,
                      "failed": 0, "timedout": 0, "unresolved": 0, "misses_1ms": 23,
                      "latency_ns": {"count": 100, "overflow_60s": 0, "p99": 10000}}
            if pair == 1 and server == "cpp": result["generator_adequate"] = False
            if pair == 2 and server == "csharp": result["valid"] = False
            if pair == 2 and server == "cpp": del result["generator_adequate"]
            scenario = {"mode": "aggregate", "frame_bytes": 4096, "connections": 1,
                        "rate": 120, "duration": 1, "phase": "measurement", "pair": pair,
                        "corpus_sha256": server if pair == 3 else "common"}
            entry = {"server": server, "client": "cpp", "scenario": scenario,
                     "result": result, "path": f"fixture-{pair}-{server}"}
            if pair == 4:
                entry["builds"] = {"csharp": server, "cpp": "same"}
            if pair == 5:
                entry["cpu_masks"] = {"server": 1 if server == "cpp" else 2}
            entries.append(entry)
    (report_folder / "results.json").write_text(json.dumps(entries), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "bench/report.py"), str(report_folder)], check=True)
    with (report_folder / "trials.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12 and sum(row["comparison_eligible"] == "True" for row in rows) == 9
    assert rows[0]["sustainable"] == "False" and rows[0]["comparison_eligible"] == "True"
    assert rows[0]["misses_1ms"] == "23" and rows[0]["latency_samples"] == "100"
    report = (report_folder / "report.md").read_text(encoding="utf-8")
    assert "Eligible trials: 9 / 12" in report and "fixture-1-cpp" in report
    assert report.count("| 1 | 1.0000 |") == 1, "Invalid, inadequate or unlike pairs entered comparison"
    assert "runner_failure" in report_tool.exclusion_reasons(entries[0] | {"runner_error": "forced stop"})
    for seconds in [float("nan"), float("inf"), 0, -1]:
        entry = entries[0] | {"result": entries[0]["result"] | {"window_seconds": seconds}}
        assert "invalid_window" in report_tool.exclusion_reasons(entry)
    # Exercise the real campaign dispatch without running 54 timed subprocess trials.
    campaign_folder = folder / f"campaign-plan-{time.time_ns()}"
    planned = []

    def capture_trial(destination, executables, masks, scenario, server, client, ordinal):
        summary = {"scenario": scenario, "server": server, "client": client,
                   "result": {"window_completed_frames": 50 if server == "csharp" else 100,
                              "window_seconds": 1}}
        planned.append(summary)
        return summary

    argv = ["run.py", "--suite", "first", "--output", str(campaign_folder), "--corpus-bytes", "1024"]
    with patch.object(sys, "argv", argv), patch.object(run, "trial", capture_trial), \
            patch.object(run, "environment_details", return_value={}):
        run.main()
    assert len(planned) == 54
    assert sum(row["scenario"]["phase"] == "closed-loop" for row in planned) == 24
    assert sum(row["scenario"]["phase"] == "alternate-client" for row in planned) == 8
    scheduled = [row for row in planned if row["scenario"]["phase"] == "scheduled-exploration"]
    assert len(scheduled) == 18 and {row["scenario"]["rate"] for row in scheduled} == {25, 45, 60}
    assert all(row["scenario"]["duration"] == 30 and row["scenario"]["warmup"] == 5 for row in planned)
    if os.name == "nt":
        masks = run.cpu_masks(4)
        assert not masks["server"] & masks["client"]
        assert not masks["spare"] & (masks["client"] | masks["server"])
        for core in masks["physical_core_masks"]:
            assert not (core & masks["server"] and core & masks["client"])
        child_code = """import ctypes,json,os
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetCurrentProcess.restype=ctypes.c_void_p
k.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
p,s=ctypes.c_size_t(),ctypes.c_size_t()
assert k.GetProcessAffinityMask(k.GetCurrentProcess(),ctypes.byref(p),ctypes.byref(s))
print(json.dumps({'mask':p.value,'processors':os.environ['DOTNET_PROCESSOR_COUNT']}))
"""
        with (folder / "affinity.json").open("w", encoding="utf-8") as log:
            child = run.spawn([sys.executable, "-c", child_code], masks["server"], log)
            assert child.wait(timeout=10) == 0
            child.stdin.close()
        inherited = json.loads((folder / "affinity.json").read_text())
        assert inherited == {"mask": masks["server"], "processors": "4"}
        # A real protocol rejection must remain reportable, even though the runner stops.
        if (ROOT / "src/cpp/build/Release/tcpbench.exe").is_file():
            failed_folder = folder / f"failed-trial-{time.time_ns()}"
            failed_folder.mkdir()
            scenario = {"mode": "aggregate", "frame_bytes": 1, "connections": 1,
                        "duration": .1, "warmup": 0, "window": 1, "rate": 100,
                        "seed": 42, "corpus": str(first), "phase": "failure-check", "pair": 0}
            try:
                run.trial(failed_folder, run.binaries(), masks, scenario, "cpp", "cpp", 1)
            except run.TrialFailure as failure:
                saved = json.loads((Path(failure.summary["path"]) / "summary.json").read_text())
                assert saved == failure.summary and saved["runner_error"]
                assert saved["result"]["valid"] is False
                assert "runner_failure" in report_tool.exclusion_reasons(saved)
            else:
                raise AssertionError("Invalid frame size obtained a successful trial")
    print(json.dumps({"status": "passed", "checks": [
        "deterministic corpus", "oracle metadata", "truncation",
        "report gates and raw retention", "overload evidence retained",
        "corpus/build/CPU pair identity", "54-trial campaign dispatch",
        "SMT separation", "affinity at child startup", "failed-trial persistence"]}))


if __name__ == "__main__":
    main()
