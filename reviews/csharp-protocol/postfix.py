"""Focused category-oracle recheck; preserves historical review evidence."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent / "postfix-60899c2c"
FOLDER.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tests"))
import protocol as p

BINARY = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
EXPECTED_HASH = "60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7"
assert hashlib.sha256(BINARY.read_bytes()).hexdigest() == EXPECTED_HASH


def write_frame(payload, counts=None, sums=None):
    expected = p.corpus.process(payload)
    return (p.corpus.META.pack(len(payload), expected["records"], expected["digest"]) +
            p.corpus.BINS.pack(*(expected["counts"] if counts is None else counts),
                              *(expected["sums"] if sums is None else sums)) + payload)


def corpus_file(name, frames):
    path = FOLDER / (name + ".bin")
    path.write_bytes(p.corpus.HEADER.pack(b"TCPBCH01", len(frames), 0) + b"".join(frames))
    return path


def main():
    payload = p.encode([p.event(message="x")])
    expected = p.corpus.process(payload)
    wrong_sums = expected["sums"][:]
    wrong_sums[15] += 1  # Structurally legal, digest/count correct, sum alone wrong.
    zero_payload = p.encode([p.event(value_milli=0, message="count-only")])
    zero_expected = p.corpus.process(zero_payload)
    wrong_counts = zero_expected["counts"][:]
    wrong_counts[0], wrong_counts[15] = 1, 0  # Zero sums preserve all metadata bounds.
    late_prefix = [write_frame(p.encode([p.event(kind="kind09", flags=2,
                                              value_milli=313, message="owned 😀" * 8)])),
                   write_frame(b"[]")]
    valid_frames = []
    for i in range(12):
        values = [] if i % 4 == 0 else [p.event(id=i, kind=f"kind{i % 16:02d}",
                                              flags=i % 4, value_milli=i * 17 - 100,
                                              message=("buffer 😀 " * (i + 1)))]
        valid_frames.append(write_frame(p.encode(values)))
    cases = {
        "original-wrong-bucket": (Path(__file__).resolve().parent / "wrong-bucket.bin", False),
        "wrong-counts-only": (corpus_file("wrong-counts-only", [write_frame(zero_payload, counts=wrong_counts)]), False),
        "wrong-sums-only": (corpus_file("wrong-sums-only", [write_frame(payload, sums=wrong_sums)]), False),
        "late-wrong-sums": (corpus_file("late-wrong-sums", late_prefix + [write_frame(payload, sums=wrong_sums)]), False),
        "valid-mixed-empty-retention": (corpus_file("valid-mixed-empty-retention", valid_frames), True),
    }
    results = []
    for mode in ["aggregate", "retain-reuse", "retain-allocate"]:
        for warmup in [0, .02]:
            phase = "measurement" if warmup == 0 else "warmup"
            for name, (fixture, valid) in cases.items():
                command = ["dotnet", str(BINARY), "process", "--corpus", str(fixture),
                           "--mode", mode, "--warmup", str(warmup), "--duration", ".02",
                           "--retain-batches", "2", "--retain-bytes", "20000"]
                run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
                log = FOLDER / f"{mode}-{phase}-{name}.log"
                log.write_text(run.stdout + run.stderr, encoding="utf-8")
                actual = json.loads(run.stdout)
                if valid:
                    assert run.returncode == 0 and actual["valid"] is True and actual["success"] is True, actual
                    assert actual["completed_records"] > 0 and actual["completed_frames"] >= len(valid_frames), actual
                    assert actual["build"]["executable_sha256"] == EXPECTED_HASH, actual
                else:
                    assert run.returncode == 2 and actual["valid"] is False, actual
                    assert actual["error"] == "process_oracle", actual
                results.append({"mode": mode, "warmup": warmup, "phase": phase, "case": name,
                                "exit": run.returncode, "valid": actual["valid"],
                                "error": actual.get("error"), "log": log.name})
    assert hashlib.sha256(BINARY.read_bytes()).hexdigest() == EXPECTED_HASH
    result = {"status": "passed", "binary_sha256": EXPECTED_HASH,
              "checks": len(results), "rejected_corrupt_category_cases": 24,
              "valid_control_cases": 6, "results": results}
    (FOLDER / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "results"}))


if __name__ == "__main__":
    main()
