"""Independent report-only post-fix checks; preserves pre-fix evidence."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / "report-postfix"


def generate(name, entries):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.json").write_text(json.dumps(entries), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(ROOT / "bench/report.py"), str(folder)],
                               cwd=ROOT, capture_output=True, text=True, timeout=10)
    (folder / "run.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with (folder / "trials.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    text = (folder / "report.md").read_text(encoding="utf-8")
    pair_section = text.split("## Paired completion-rate ratios", 1)[1]
    pair_rows = [line for line in pair_section.splitlines() if line.startswith("| cpp /")]
    return rows, text, pair_rows


def entry(server, pair=0):
    return {"server": server, "client": "cpp", "path": f"fixture-{pair}-{server}",
            "scenario": {"mode": "aggregate", "frame_bytes": 65536, "connections": 4,
                         "rate": 1000, "duration": 1, "warmup": .2, "window": 8,
                         "seed": 42, "arrival": "steady", "corpus_sha256": "common",
                         "phase": "measurement", "pair": pair},
            "server_result": {"valid": True},
            "result": {"valid": True, "success": True, "generator_adequate": True,
                       "sustainable": False, "window_seconds": 1,
                       "window_completed_frames": 800 if server == "csharp" else 400,
                       "completed_frames": 900, "offered": 1000, "admitted": 900,
                       "rejected": 100, "failed": 0, "timedout": 0, "unresolved": 0,
                       "scheduler_late_count": 0, "generator_late_rejected": 0,
                       "deadline_misses_1ms": 200, "deadline_misses_5ms": 150,
                       "deadline_misses_10ms": 100,
                       "scheduled_latency": {"count": 900, "overflow_60s": 1},
                       "latency_ns": {"p50": 1, "p99": 10, "p999": None, "max": 61_000_000_000}}}


def gate_cases():
    cases = ["adequate-overload", "inadequate", "missing-adequacy", "invalid-client",
             "unsuccessful-client", "invalid-server", "failed", "timedout", "unresolved",
             "zero-window", "negative-window", "duplicate", "incomplete"]
    entries = []
    for pair, name in enumerate(cases):
        left, right = entry("csharp", pair), entry("cpp", pair)
        for value in [left, right]:
            value["path"] = f"{name}-{value['server']}"
        if name == "inadequate": right["result"]["generator_adequate"] = False
        if name == "missing-adequacy": del right["result"]["generator_adequate"]
        if name == "invalid-client": right["result"]["valid"] = False
        if name == "unsuccessful-client": right["result"]["success"] = False
        if name == "invalid-server": right["server_result"]["valid"] = False
        if name in ["failed", "timedout", "unresolved"]: right["result"][name] = 1
        if name == "zero-window": right["result"]["window_seconds"] = 0
        if name == "negative-window": right["result"]["window_seconds"] = -1
        entries.append(left)
        if name != "incomplete": entries.append(right)
        if name == "duplicate": entries.append(copy.deepcopy(right))
    rows, text, pair_rows = generate("gates", entries)
    assert len(rows) == len(entries), "Lost raw trial rows"
    assert len(pair_rows) == 1 and "| 1 | 2.0000 |" in pair_rows[0], pair_rows
    assert rows[0]["sustainable"] == "False" and rows[0]["comparison_eligible"] == "True"
    for row in rows:
        if row["path"].endswith("-cpp") and row["path"].split("-cpp")[0] in cases[1:11]:
            assert row["comparison_eligible"] == "False" and row["exclusion_reasons"], row
    assert rows[0]["misses_1ms"] == "200" and rows[0]["misses_5ms"] == "150" and rows[0]["misses_10ms"] == "100"
    assert rows[0]["latency_samples"] == "900" and rows[0]["latency_overflow_60s"] == "1"
    assert rows[0]["p999_sample_gate_passed"] == "False" and rows[0]["p999_ns"] == ""
    return {"case_count": len(cases), "raw_trial_count": len(rows), "eligible_pair_count": len(pair_rows),
            "overload_retained": True, "native_aliases_preserved": True}


def identity_cases():
    cases = ["corpus_sha256", "window", "duration", "warmup", "seed", "arrival", "phase"]
    outcomes = []
    for field in cases:
        left, right = entry("csharp"), entry("cpp")
        values = {"corpus_sha256": "other", "window": 16, "duration": 2,
                  "warmup": .4, "seed": 43, "arrival": "poisson", "phase": "calibration"}
        right["scenario"][field] = values[field]
        if field == "duration": right["result"]["window_seconds"] = values[field]
        _, _, pair_rows = generate("identity-" + field, [left, right])
        assert not pair_rows, (field, pair_rows)
        outcomes.append(field)
    return {"fields_independently_separated": outcomes}


def existing_results():
    outcomes = []
    for source in [ROOT / "results/integration-smoke/results.json"] + sorted((ROOT / "results").glob("large-frames-*/results.json")):
        original = json.loads(source.read_text(encoding="utf-8"))
        rows, _, _ = generate("existing-" + source.parent.name, original)
        assert len(rows) == len(original)
        for value, row in zip(original, rows):
            result = value["result"]
            aliases = {"scheduler_late_over_1ms": "scheduler_late_count",
                       "misses_1ms": "deadline_misses_1ms", "misses_5ms": "deadline_misses_5ms",
                       "misses_10ms": "deadline_misses_10ms"}
            for normalized, native in aliases.items():
                expected = result.get(normalized, result.get(native))
                assert row[normalized] == (str(expected) if expected is not None else ""), (value["path"], normalized)
            histogram = result.get("scheduled_latency", result.get("latency_ns", {}))
            for normalized, raw in [("latency_samples", "count"), ("latency_overflow_60s", "overflow_60s")]:
                expected = histogram.get(raw)
                assert row[normalized] == (str(expected) if expected is not None else ""), (value["path"], normalized)
            for normalized, raw in [("p50_ns", "p50"), ("p99_ns", "p99"), ("p999_ns", "p999"), ("max_ns", "max")]:
                expected = result.get("latency_ns", {}).get(raw)
                assert row[normalized] == (str(expected) if expected is not None else ""), (value["path"], normalized)
        outcomes.append({"source": str(source), "raw_trial_count": len(rows),
                         "cpp_client_trials": sum(value["client"] == "cpp" for value in original)})
    return outcomes


def main():
    OUT.mkdir(exist_ok=True)
    old = HERE / "report-inadequate/report.md"
    old_hash = hashlib.sha256(old.read_bytes()).hexdigest()
    evidence = {"report_sha256": hashlib.sha256((ROOT / "bench/report.py").read_bytes()).hexdigest(),
                "gates": gate_cases(), "identity": identity_cases(), "existing": existing_results()}
    assert hashlib.sha256(old.read_bytes()).hexdigest() == old_hash, "Pre-fix report overwritten"
    evidence["original_report_sha256_unchanged"] = old_hash
    (OUT / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
