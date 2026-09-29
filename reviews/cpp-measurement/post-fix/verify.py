"""Independent post-fix checks; preserve the frozen pre-fix evidence."""
from __future__ import annotations

import hashlib
import json
import math
import socket
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE))
import probe
import stage_probe

EXPECTED_HASH = "62773a9936616bc8d0a0b3abfade96bb904ef70fba447b0fb8f0697fda265f79"
probe.HERE = HERE


def planned(duration, rate, arrival, seed=42):
    if arrival == "steady":
        return math.ceil(duration * rate)
    if arrival == "burst":
        return sum(1 for i in range(math.ceil(duration * rate * 1.5) + 1)
            if 6 * math.floor(i / (4.5 * rate)) + (
                (i % (4.5 * rate)) / (.6 * rate) if i % (4.5 * rate) < 3 * rate
                else 5 + (i % (4.5 * rate) - 3 * rate) / (1.5 * rate)) < duration)
    count, elapsed = 0, 0.
    mask = (1 << 64) - 1
    while True:
        seed = (seed + 0x9e3779b97f4a7c15) & mask
        value = seed
        value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
        value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
        value ^= value >> 31
        elapsed += -math.log(((value >> 11) + 1.) / 9007199254740993.) / rate
        if elapsed >= duration:
            return count
        count += 1


def client_cases():
    results = []
    for arrival in ["steady", "poisson", "burst"]:
        for fault in ["drop", "corrupt"]:
            item = probe.case(f"aborted-{arrival}-{fault}", fault=fault, arrival=arrival)
            data = item["result"]
            intended = planned(.4, 100, arrival)
            assert item["exit_code"] != 0 and data["valid"] is False, item
            assert data["offered"] == intended == data["admitted"] + data["rejected"], item
            assert data["aborted_schedule_rejected"] > 0, item
            assert data["aborted_schedule_rejected"] <= data["rejected"], item
            assert data["deadline_misses_1ms"] == intended, item
            assert data["deadline_misses_5ms"] == intended, item
            assert data["deadline_misses_10ms"] == intended, item
            assert data["generator_adequate"] is False, item
            assert data["scheduled_latency"]["count"] == data["acknowledged"] == 0, item
            results.append(item)
    item = probe.case("ack-inside-drain", delay=.55, duration=.1, rate=1, drain=.5)
    data = item["result"]
    assert item["exit_code"] == 0 and data["valid"], item
    assert data["acknowledged"] == data["admitted"] == data["offered"] == 1, item
    assert data["window_completed_frames"] == data["timedout"] == data["unresolved"] == 0, item
    assert .53 <= data["cohort_seconds"] < .65 and data["drain_seconds"] >= .43, item
    results.append(item)
    for fault in ["withhold", "withhold-after-one"]:
        item = probe.case(fault, fault=fault, duration=.1, drain=.5)
        data = item["result"]
        assert item["exit_code"] != 0 and data["valid"] is False, item
        assert data["offered"] == data["admitted"] + data["rejected"] == 10, item
        assert data["timedout"] == data["unresolved"] > 0, item
        assert data["cohort_seconds"] >= .59 and data["drain_seconds"] >= .49, item
        assert data["error"] in ["drain_timeout", "receive_timeout", "io_deadline"], item
        assert data["scheduled_latency"]["count"] == data["acknowledged"], item
        assert abs(data["cohort_seconds"] - (.1 + data["drain_seconds"])) < 1e-6, item
        assert abs(item["after_first_request_seconds"] - data["cohort_seconds"]) < .08, item
        results.append(item)
    item = probe.case("ack-outside-drain", delay=.7, duration=.1, rate=1, drain=.5)
    data = item["result"]
    assert item["exit_code"] != 0 and not data["valid"], item
    assert data["timedout"] == data["unresolved"] == 1 and data["acknowledged"] == 0, item
    assert data["cohort_seconds"] >= .59 and data["drain_seconds"] >= .49, item
    results.append(item)
    item = probe.case("healthy-waits-until-window-end", duration=.4, rate=1)
    data = item["result"]
    assert item["exit_code"] == 0 and data["acknowledged"] == 1, item
    assert item["after_first_request_seconds"] >= .39, item
    assert .4 <= data["cohort_seconds"] < .5 and data["window_completed_frames"] == 1, item
    results.append(item)
    item = probe.case("overload-drain", delay=.025, rate=1000, drain=2)
    data = item["result"]
    assert item["exit_code"] == 0 and data["valid"] and not data["sustainable"], item
    assert data["offered"] == 400 == data["admitted"] + data["rejected"], item
    assert data["admitted"] == data["acknowledged"] > 0 and data["rejected"] > 0, item
    assert data["deadline_misses_10ms"] == 400 and data["unresolved"] == 0, item
    assert data["queue_high_water"] <= 8 and data["inflight_bytes_high_water"] <= 16, item
    assert data["cohort_seconds"] >= .4 and data["scheduled_latency"]["count"] == data["acknowledged"], item
    results.append(item)
    return results


def stages(frame_count, epochs=1):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    name = f"stages-{frame_count}-{epochs}-epochs"
    output = HERE / f"{name}.json"
    command = [str(probe.CPP), "server", "--port", str(port), "--max-frame", "4096",
        "--workers", "1", "--max-connections", "1", "--mode", "retain-reuse",
        "--pause-every", "1024", "--pause-ms", "50", "--control-stdin", "1",
        "--output", str(output)]
    server = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        ready = server.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            seq = 0
            pauses = []
            for _ in range(epochs):
                seq += 1
                begin, _ = stage_probe.exchange(sock, 1, seq, bytes(32))
                assert begin == b""
                digest = probe.corpus.OFFSET
                for frame in range(frame_count):
                    seq += 1
                    ack, elapsed = stage_probe.exchange(sock, 2, seq, b"[]")
                    assert ack == struct.pack(">QQ", 0, probe.corpus.OFFSET)
                    digest = probe.corpus.fnv(struct.pack(">QQQ", seq, 0, probe.corpus.OFFSET), digest)
                    if frame == 1023:
                        pauses.append(elapsed)
                seq += 1
                summary, _ = stage_probe.exchange(sock, 3, seq, b"")
                assert summary == stage_probe.TOTAL.pack(frame_count, 2 * frame_count, 0, digest,
                    *([0] * 64), *([0] * 64))
        out, err = server.communicate("stop\n", timeout=5)
        (HERE / f"{name}.log").write_text(ready + out + err, encoding="utf-8")
        assert server.returncode == 0, err
        result = json.loads(output.read_text(encoding="utf-8"))
        samples = frame_count // 1024
        for key in ["receive_elapsed_ns", "processing_elapsed_ns", "decode_materialize_ns", "retained_visit_ns"]:
            assert result[key]["count"] == samples, (key, result)
            assert result[key]["p99_ns"] is None and result[key]["p999_ns"] is None, (key, result)
        if samples:
            assert result["processing_elapsed_ns"]["max_ns"] >= 40_000_000, result
            assert all(delay >= .04 for delay in pauses), pauses
        return {"name": name, "command": command, "client_pause_elapsed_seconds": pauses, "result": result}
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()


def cursor_arithmetic():
    """Review the unobservable post-abort cursor update against explicit assigned arrivals."""
    checked = 0
    for connection_count in range(1, 9):
        for corpus_count in range(1, 12):
            for dispatched in range(25):
                for remaining in range(25):
                    before = [(i + sum(connection_count for k in range(dispatched)
                        if k % connection_count == i)) % corpus_count for i in range(connection_count)]
                    expected = before[:]
                    for k in range(dispatched, dispatched + remaining):
                        index = k % connection_count
                        expected[index] = (expected[index] + connection_count) % corpus_count
                    actual = before[:]
                    for i in range(connection_count):
                        first = (i + connection_count - dispatched % connection_count) % connection_count
                        if first < remaining:
                            count = 1 + (remaining - 1 - first) // connection_count
                            actual[i] = (actual[i] + (count % corpus_count) * connection_count) % corpus_count
                    assert actual == expected, (connection_count, corpus_count, dispatched, remaining)
                    checked += 1
    return {"cases": checked, "scope": "source arithmetic review; post-abort cursor is not exported by binary"}


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    actual_hash = hashlib.sha256(probe.CPP.read_bytes()).hexdigest()
    assert actual_hash == EXPECTED_HASH, actual_hash
    probe.corpus.write_frames(HERE / "empty.bin", [b"[]"])
    evidence = {"binary_sha256": actual_hash, "clients": client_cases(),
        "stages": [stages(1023), stages(1024, epochs=2)], "cursor_arithmetic": cursor_arithmetic()}
    assert hashlib.sha256(probe.CPP.read_bytes()).hexdigest() == EXPECTED_HASH
    (HERE / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    for item in evidence["clients"]:
        data = item["result"]
        print(json.dumps({"name": item["name"], "exit_code": item["exit_code"],
            **{key: data[key] for key in ["offered", "admitted", "rejected", "acknowledged", "timedout",
                "unresolved", "aborted_schedule_rejected", "cohort_seconds", "drain_seconds", "error"]}}))
    print(json.dumps({"scoped_pass": True, "client_cases": len(evidence["clients"]),
        "stage_cases": len(evidence["stages"]), "cursor_arithmetic_cases": evidence["cursor_arithmetic"]["cases"],
        "binary_sha256": actual_hash}))


if __name__ == "__main__":
    main()
