"""Independent checks for the corpus generator, runner and report (no timed traffic)."""
import csv
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "bench")]
import corpus  # noqa: E402
import report  # noqa: E402
import run  # noqa: E402


def corpus_checks(folder):
    for profile in corpus.PROFILES:
        a, b = folder / f"{profile}-a.bin", folder / f"{profile}-b.bin"
        left = corpus.write_frames(a, corpus.generate_payloads(65536, 4096, 123, profile))
        right = corpus.write_frames(b, corpus.generate_payloads(65536, 4096, 123, profile))
        assert left == right and a.read_bytes() == b.read_bytes(), profile
        for payload, expected in corpus.read_corpus(a):
            assert all(corpus.process(payload)[k] == v for k, v in expected.items())
    sizes = [len(p) for p in corpus.generate_payloads(8 << 20, 1 << 20, 5, "hard", 4096)]
    assert min(sizes) < 64 << 10 and max(sizes) > 256 << 10, "mixed frame sizes are not mixed"
    # Profile "mixed" must stay byte-identical to the first-pass generator (digest of its output).
    digest = hashlib.sha256(b"".join(corpus.generate_payloads(1 << 20, 65536, 42))).hexdigest()
    assert digest == "403e5dab305bd57bcb68890cad6d0d8e7c10996b056c22a5586ccf21b73d8d0c", digest
    truncated = folder / "truncated.bin"
    truncated.write_bytes((folder / "hard-a.bin").read_bytes()[:-1])
    try:
        list(corpus.read_corpus(truncated))
    except ValueError:
        return
    raise AssertionError("truncated corpus accepted")


def entry(server, repetition, *, valid=True, cpu=0.5, lateness_ns=0, late=0, offered=1000, scheduled=True, rate=1.0):
    result = {"valid": valid, "load_model": "scheduled" if scheduled else "closed-loop",
              "counts": {"offered": offered, "admitted": offered, "rejected": 0, "acknowledged": offered, "failed": 0,
                         "unresolved": 0, "timedout": 0, "generator_late_rejected": 0, "aborted_schedule_rejected": 0},
              "window": {"seconds": 1, "frames": offered, "records": int(offered * rate), "payload_bytes": offered},
              "latency": {"scheduled": {"count": offered, "p50_ns": 1000, "p99_ns": 2000}},
              "generator": {"lateness": {"count": offered, "p99_ns": lateness_ns}, "late_over_1ms": late},
              "resources": {"seconds": 1, "cpu_seconds": 3 * cpu, "logical_cpus": 3}}
    server_result = {"valid": True, "measured": {"complete": True, "seconds": 1, "frames": offered, "records": offered,
                                                 "cpu_seconds": 1}, "lifetime": {}, "stages": {}, "retention": {}}
    cell = {"phase": "p", "name": "c", "kind": "tcp", "mode": "aggregate", "repetition": repetition, "connections": 1,
            "window": 1, "rate": 1, "arrival": "steady", "socket_buffer": 0, "retain_batches": 8, "duration": 1,
            "warmup": 0}
    return {"ordinal": repetition, "cell": cell, "server": server, "client": "cpp", "builds": {}, "corpus": {"name": "x"},
            "elapsed_seconds": 1, "background_cpu_seconds": 0, "logical_cpus": 16, "runner_error": None,
            "path": f"{server}-{repetition}", "result": result, "server_result": server_result}


def report_checks(folder):
    entries = [entry("cpp", 0), entry("csharp", 0, rate=0.5),                        # eligible pair
               entry("cpp", 1), entry("csharp", 1, lateness_ns=150_000),            # p99 lateness over 100 us
               entry("cpp", 2), entry("csharp", 2, late=2),                          # 0.2% > 0.1% over 1 ms
               entry("cpp", 3, cpu=0.9), entry("csharp", 3),                         # client CPU above 85%
               entry("cpp", 4, valid=False), entry("csharp", 4),                     # invalid
               entry("cpp", 5, late=1), entry("csharp", 5, late=1)]                  # 0.1%: allowed; strict v1 fails
    rows = [report.row(e) for e in entries]
    report.mark_pairs(rows)
    by = {(r["server"], r["repetition"]): r for r in rows}
    assert by["csharp", 1]["exclusion"] == "lateness_p99_over_100us" and by["cpp", 1]["exclusion"] == "partner_excluded"
    assert by["csharp", 2]["exclusion"] == "late_over_1ms_above_0.1pct" and not by["cpp", 2]["eligible"]
    assert by["cpp", 3]["exclusion"] == "client_cpu_over_85pct" and not by["csharp", 3]["eligible"]
    assert by["cpp", 4]["exclusion"] == "invalid_or_unreconciled" and not by["csharp", 4]["eligible"]
    assert by["cpp", 5]["eligible"] and by["cpp", 5]["strict_v1_adequate"] is False
    assert report.row(entry("cpp", 9, cpu=0.9, scheduled=False))["exclusion"] == "client_cpu_over_85pct"
    # Offered-demand percentiles: rejected demand is a miss, never a finite latency.
    histogram = {"count": 98, "buckets": [[1_000_000, 98]]}
    assert report.offered_quantile(histogram, 2, .98) == 1.0 and report.offered_quantile(histogram, 2, .99) == math.inf
    line = report.ratio_line("t", rows, "records_per_second", "records/s")
    assert line == "| t | records/s | 2 | 0.750 | – |", line  # repetitions 0 (0.5) and 5 (1.0) only
    (folder / "results.json").write_text(json.dumps(entries), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "bench/report.py"), str(folder)], check=True, capture_output=True)
    with (folder / "trials.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12 and sum(r["eligible"] == "True" for r in rows) == 4
    text = (folder / "report.md").read_text(encoding="utf-8")
    assert "4 of 12 trials eligible" in text and "lateness_p99_over_100us" in text
    capacity = run.capacity([{"cell": {"name": "c"}, "server": s, "runner_error": None,
                              "result": {"window": {"frames": f, "seconds": 2}}}
                             for s, f in [("cpp", 100), ("cpp", 120), ("csharp", 80), ("csharp", 60)]], "c")
    assert capacity == 35, capacity  # slower server's median frames per second


def runner_checks(folder):
    layout = run.cpu_layout(4)
    cores = layout["physical_core_masks"]
    assert layout["spare"] & cores[0], "physical core 0 is the spare"
    for a, b in [("server", "client"), ("server", "spare"), ("client", "spare")]:
        assert not layout[a] & layout[b]
        assert not any(core & layout[a] and core & layout[b] for core in cores), "SMT siblings shared"
    probe = ("import ctypes,json,os;k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p;"
             "k.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)];"
             "p,s=ctypes.c_size_t(),ctypes.c_size_t();k.GetProcessAffinityMask(k.GetCurrentProcess(),ctypes.byref(p),ctypes.byref(s));"
             "print(json.dumps({'mask':p.value,'processors':os.environ['DOTNET_PROCESSOR_COUNT']}))")
    with (folder / "affinity.json").open("w", encoding="utf-8") as log:
        child = run.spawn([sys.executable, "-c", probe], layout["server"], log)
        assert child.wait(timeout=10) == 0
    assert json.loads((folder / "affinity.json").read_text()) == {"mask": layout["server"], "processors": "4"}
    # A real protocol failure (frames larger than --max-frame) is persisted, not dropped.
    bad = folder / "bad.bin"
    corpus.write_frames(bad, corpus.generate_payloads(8192, 4096, 1))
    cell = run.DEFAULTS | {"name": "failure", "phase": "p", "repetition": 0, "mode": "aggregate", "duration": 0.2,
                           "warmup": 0, "connections": 1, "window": 1, "seed": 1, "drain": 2}
    info = {"name": "bad", "path": str(bad), "sha256": run.sha256(bad), "max_frame_bytes": 16}
    summary = run.trial(folder, 1, cell, "cpp", layout, info)
    saved = json.loads((Path(summary["path"]) / "summary.json").read_text(encoding="utf-8"))
    assert saved["runner_error"] and report.row(saved)["exclusion"] == "invalid_or_unreconciled"


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / "results") as temporary:
        folder = Path(temporary)
        for name, check in [("corpus", corpus_checks), ("report", report_checks), ("runner", runner_checks)]:
            (folder / name).mkdir()
            check(folder / name)
            print(json.dumps({"check": name, "status": "passed"}), flush=True)


if __name__ == "__main__":
    main()
