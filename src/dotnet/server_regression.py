"""Bounded checks for processing budgets, quiet EOF, and owned-array peaks."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DLL = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
sys.path.insert(0, str(ROOT / "tests"))
from protocol import command, exact, frame


@contextmanager
def server(folder, name, *options):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    output = folder / f"{name}.json"
    args = command(DLL) + ["server", "--port", str(port), "--control-stdin", "1",
                           "--output", str(output), *options]
    process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = process.stdout.readline()
    assert json.loads(ready)["event"] == "ready", ready
    try:
        yield process, port, output
    finally:
        if process.poll() is None:
            try:
                stdout, stderr = process.communicate("stop\n", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
        else:
            stdout, stderr = process.communicate()
        (folder / f"{name}.log").write_text(ready + stdout + stderr, encoding="utf-8")
        assert process.returncode == 0, stderr


def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.sendall(frame(1, 1, bytes(32)))
    assert exact(sock, 16) == frame(0x8001, 1)
    return sock


def closed(sock):
    try:
        assert sock.recv(1) == b"", "expired work received a success ACK"
    except (ConnectionResetError, ConnectionAbortedError):
        pass


def expiry(folder, mode, kind, payload):
    name = f"{mode}-{kind}"
    options = ["--mode", mode, "--max-frame", "16777216", "--max-connections", "1",
               "--frame-timeout-ms", "1000" if kind == "idle" else "100",
               "--idle-timeout-ms", "100"]
    if kind in ("pause", "idle"):
        options += ["--pause-every", "1", "--pause-ms", "900"]
    with server(folder, name, *options) as (_, port, output):
        with connect(port) as busy:
            request = frame(2, 2, payload)
            start = time.monotonic()
            busy.sendall(request[:-1] if kind == "partial" else request)
            busy.settimeout(.6)
            closed(busy)
            elapsed = time.monotonic() - start
            assert elapsed < .6, (name, elapsed)
        # The expired connection must release the sole slot, not just suppress its ACK.
        with connect(port):
            pass
    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert metrics["completed"] == 0 and metrics["errors"].get("deadline", 0) == 1, metrics
    assert metrics["rejected_connections"] == 0, metrics
    if kind == "cpu":
        assert metrics["transport"]["ReceiveBytes"] >= len(payload) + 16 + 48, metrics
    return {"case": name, "seconds": elapsed, "status": "passed"}


def shutdown(folder):
    with server(folder, "shutdown-pause", "--pause-every", "1", "--pause-ms", "10000") as (process, port, output):
        with connect(port) as busy:
            busy.sendall(frame(2, 2, b"[]"))
            time.sleep(.05)
            start = time.monotonic()
            process.stdin.write("stop\n")
            process.stdin.flush()
            process.wait(timeout=.6)
            elapsed = time.monotonic() - start
            closed(busy)
    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert metrics["completed"] == 0, metrics
    return {"case": "shutdown-pause", "seconds": elapsed, "status": "passed"}


def retained_capacity_and_eof(folder, mode):
    row = {"id": 1, "timestamp_ns": 2, "source": 3, "kind": "kind00",
           "value_milli": 4, "flags": 0, "message": "x" * 1000}
    payload = json.dumps([row], separators=(",", ":")).encode()
    name = f"{mode}-capacity-quiet-eof"
    with server(folder, name, "--mode", mode, "--retain-batches", "1",
                "--frame-timeout-ms", "100", "--idle-timeout-ms", "100") as (_, port, output):
        with connect(port) as sock:
            for sequence in (2, 3):
                sock.sendall(frame(2, sequence, payload))
                assert exact(sock, 16) == frame(0x8002, sequence, bytes(16))[:16]
                exact(sock, 16)
            time.sleep(.15)
            sock.shutdown(socket.SHUT_WR)
            closed(sock)
    metrics = json.loads(output.read_text(encoding="utf-8"))
    assert metrics["completed"] == 2 and metrics["errors"] == {}, metrics
    assert metrics["retained_canonical_peak_per_connection"] == 1038, metrics
    peak = metrics["owned_storage_peak_per_connection"]
    assert peak >= 2 * (1000 + 48), metrics
    return {"case": name, "owned_peak": peak, "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    folder = args.output_dir or ROOT / "results" / f"dotnet-server-regression-{time.time_ns()}"
    folder.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(DLL.read_bytes()).hexdigest()
    row = b'{"id":1,"timestamp_ns":2,"source":3,"kind":"kind00","value_milli":4,"flags":0,"message":"x"}'
    large = b"[" + b",".join([row] * 170_000) + b"]"
    results = [expiry(folder, "aggregate", "partial", b"[]")]
    for mode in ("aggregate", "retain-reuse", "retain-allocate"):
        results.append(expiry(folder, mode, "pause", b"[]"))
        results.append(expiry(folder, mode, "cpu", large))
    results.append(expiry(folder, "transport", "idle", b"[]"))
    results.append(shutdown(folder))
    capacities = [retained_capacity_and_eof(folder, mode)
                  for mode in ("retain-reuse", "retain-allocate")]
    assert capacities[0]["owned_peak"] == capacities[1]["owned_peak"], capacities
    results.extend(capacities)
    assert hashlib.sha256(DLL.read_bytes()).hexdigest() == before
    evidence = {"binary_sha256": before, "cases": results}
    (folder / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
