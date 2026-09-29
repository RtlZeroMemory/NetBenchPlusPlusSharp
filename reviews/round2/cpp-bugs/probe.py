"""Bounded native diagnostic-pause/deadline probe; loopback, frozen EXE only."""
import argparse
import hashlib
import json
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
from protocol import begin, frame

OUT = pathlib.Path(__file__).resolve().parent
EXE = ROOT / "src/cpp/build/Release/tcpbench.exe"


def state(sock):
    try:
        return "closed" if sock.recv(1) == b"" else "response"
    except socket.timeout:
        return "still_open"
    except (ConnectionResetError, ConnectionAbortedError):
        return "closed"


def run(name, partial=False, stop_during_pause=False):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    deadline = "5000" if stop_during_pause else "100"
    args = [str(EXE), "server", "--port", str(port), "--control-stdin", "1",
            "--max-frame", "4096", "--max-connections", "1", "--workers", "1",
            "--idle-timeout-ms", deadline, "--frame-timeout-ms", deadline,
            "--pause-every", "1", "--pause-ms", "900", "--output", str(OUT / (name + ".json"))]
    proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    first = second = None
    evidence = {"name": name, "args": args}
    try:
        ready = proc.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        first = socket.create_connection(("127.0.0.1", port), timeout=3)
        begin(first)
        started = time.perf_counter()
        first.sendall(frame(2, 2, b"[]")[:-1] if partial else frame(2, 2, b"[]"))
        if stop_during_pause:
            time.sleep(.05)
            stop_started = time.perf_counter()
            stdout, stderr = proc.communicate("stop\n", timeout=3)
            evidence["stop_to_exit_seconds"] = time.perf_counter() - stop_started
            evidence["data_to_exit_seconds"] = time.perf_counter() - started
        else:
            first.settimeout(.35)
            evidence["state_after_initial_read"] = state(first)
            evidence["initial_read_seconds"] = time.perf_counter() - started
            time.sleep(.05)
            second = socket.create_connection(("127.0.0.1", port), timeout=3)
            try:
                begin(second)
                evidence["second_connection_state"] = "accepted"
            except (AssertionError, OSError):
                evidence["second_connection_state"] = "rejected"
            if evidence["state_after_initial_read"] != "closed":
                first.settimeout(2)
                evidence["final_connection_state"] = state(first)
            evidence["data_to_close_observed_seconds"] = time.perf_counter() - started
            stdout, stderr = proc.communicate("stop\n", timeout=3)
        (OUT / (name + ".stdout.log")).write_text(ready + stdout, encoding="utf-8")
        (OUT / (name + ".stderr.log")).write_text(stderr, encoding="utf-8")
        assert proc.returncode == 0, stderr
        metrics = json.loads(stdout)
        evidence.update(completed_frames=metrics["completed_frames"],
                        excess_connections=metrics["excess_connections"],
                        rejection_reasons=metrics["rejection_reasons"])
        return evidence
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def main():
    global EXE, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=EXE)
    parser.add_argument("--output-dir", type=pathlib.Path, default=OUT / f"pause-recheck-{time.time_ns()}")
    args = parser.parse_args()
    EXE, OUT = args.exe.resolve(), args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(EXE.read_bytes()).hexdigest()
    cases = [run("partial-read-control", partial=True), run("processing-pause"),
             run("shutdown-during-pause", stop_during_pause=True)]
    result = {"binary_sha256": before, "binary_unchanged": before == hashlib.sha256(EXE.read_bytes()).hexdigest(),
              "cases": cases}
    (OUT / "evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result["binary_unchanged"]
    assert cases[0]["state_after_initial_read"] == "closed"
    assert cases[0]["second_connection_state"] == "accepted"
    assert all(case["completed_frames"] == 0 for case in cases)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
