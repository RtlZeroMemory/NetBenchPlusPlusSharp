"""Adversarial checks against frozen executables; no production rebuilds."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import corpus

CS = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
CPP = ROOT / "src/cpp/build/Release/tcpbench.exe"
WIRE = struct.Struct(">IHHQ")
TOTAL = struct.Struct(">QQQQ64Q64q")


def command(binary):
    return ["dotnet", str(binary)] if binary.suffix == ".dll" else [str(binary)]


def invoke(binary, name, args):
    output = HERE / f"{name}.json"
    started = time.perf_counter()
    completed = subprocess.run(command(binary) + args + ["--output", str(output)],
                               cwd=ROOT, capture_output=True, text=True, timeout=20)
    (HERE / f"{name}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    result = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else None
    return {"name": name, "exit_code": completed.returncode, "result": result,
            "wall_seconds": time.perf_counter() - started}


def bad_metadata():
    payload = b'[{"id":1,"timestamp_ns":2,"source":3,"kind":"kind00","value_milli":123,"flags":0,"message":""}]'
    path = HERE / "bad-sum.bin"
    corpus.write_frames(path, [payload])
    raw = bytearray(path.read_bytes())
    struct.pack_into(">q", raw, 16 + 532, 124)
    path.write_bytes(raw)
    results = []
    for mode in ["aggregate", "retain-reuse", "retain-allocate"]:
        for binary, label in [(CS, "cs"), (CPP, "cpp")]:
            run = invoke(binary, f"bad-sum-{label}-{mode}", ["process", "--corpus", str(path),
                "--mode", mode, "--duration", "0.01", "--warmup", "0"])
            assert (run["exit_code"] == 0) == (label == "cs"), run
            results.append({"name": run["name"], "exit_code": run["exit_code"],
                            "valid": run["result"]["valid"] if run["result"] else False})
    return results


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        received = sock.recv(count - len(data))
        if not received:
            raise EOFError
        data.extend(received)
    return bytes(data)


class Peer:
    """Transport peer with timestamps and independently verified End metadata."""
    def __init__(self, delay=0, fault=None):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.stop = threading.Event()
        self.delay, self.fault = delay, fault
        self.sockets, self.threads, self.received = [], [], []
        self.worker = threading.Thread(target=self.accept, daemon=True)
        self.worker.start()

    def accept(self):
        while not self.stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sock.settimeout(10)
            self.sockets.append(sock)
            thread = threading.Thread(target=self.serve, args=(sock,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def serve(self, sock):
        batches = size = 0
        digest = corpus.OFFSET
        try:
            while not self.stop.is_set():
                length, version, kind, seq = WIRE.unpack(exact(sock, 16))
                body = exact(sock, length)
                if kind == 1:
                    batches = size = 0
                    digest = corpus.OFFSET
                    reply = b""
                elif kind == 2:
                    self.received.append(time.perf_counter())
                    if self.fault == "withhold":
                        self.stop.wait(10)
                        return
                    if self.delay:
                        time.sleep(self.delay)
                    batches += 1
                    size += length
                    digest = corpus.fnv(struct.pack(">QQQ", seq, 0, length), digest)
                    reply = struct.pack(">QQ", 0, length)
                elif kind == 3:
                    counts, sums = [0] * 64, [0] * 64
                    if self.fault == "count":
                        counts[63] = 1
                    elif self.fault == "sum":
                        sums[63] = 1
                    reply = TOTAL.pack(batches, size, 0, digest, *counts, *sums)
                else:
                    return
                sock.sendall(WIRE.pack(len(reply), 1, kind | 0x8000, seq) + reply)
        except (OSError, EOFError):
            pass
        finally:
            sock.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.worker.join(2)
        for thread in self.threads:
            thread.join(2)


def client_case(binary, label, fault, duration=.1, rate=100, window=8, drain=.1, delay=0):
    peer = Peer(delay=delay, fault=fault)
    path = HERE / "empty.bin"
    corpus.write_frames(path, [b"[]"])
    try:
        result = invoke(binary, f"{label}-{fault or 'none'}-{duration}-{rate}", ["client",
            "--port", str(peer.port), "--mode", "transport", "--corpus", str(path),
            "--connections", "1", "--warmup", "0", "--duration", str(duration),
            "--rate", str(rate), "--window", str(window), "--drain-seconds", str(drain)])
        result["peer_received"] = len(peer.received)
        result["after_window_seconds"] = (time.perf_counter() - peer.received[0] - duration
                                          if peer.received else None)
        return result
    finally:
        peer.close()


def client_checks():
    results = []
    for fault in ["count", "sum", "withhold"]:
        run = client_case(CS, "cs", fault, drain=.5 if fault == "withhold" else .1)
        assert run["exit_code"] != 0 and run["result"]["valid"] is False, run
        data = run["result"]
        assert data["offered"] == data["admitted"] + data["rejected"], data
        assert data["scheduled_latency"]["count"] == data["acknowledged"], data
        if fault == "withhold":
            assert run["after_window_seconds"] >= .45 and data["drain_seconds"] == 0, run
        results.append({"name": run["name"], "exit_code": run["exit_code"],
                        "wall_seconds": run["wall_seconds"],
                        "observed_after_window_seconds": run["after_window_seconds"],
                        **{k: data[k] for k in ["offered", "admitted", "acknowledged", "failed", "timedout", "unresolved", "cohort_seconds", "drain_seconds"]}})
    for delay in [.025, .1]:
        run = client_case(CS, f"cs-pause-{delay}", None, duration=.4, rate=1000,
                          window=8, drain=3, delay=delay)
        data = run["result"]
        assert run["exit_code"] == 0 and data["valid"] is True, run
        assert data["offered"] == 400 and data["offered"] == data["admitted"] + data["rejected"], data
        assert data["admitted"] == data["acknowledged"] > 0 and data["rejected"] > 0, data
        assert data["scheduled_latency"]["count"] == data["acknowledged"], data
        assert data["scheduled_latency"]["max_ns"] >= int(delay * 1e9), data
        assert data["misses_10ms"] == data["offered"] and data["sustainable"] is False, data
        assert data["queue_high_water"] <= 8 and data["inflight_bytes_high_water"] <= 16, data
        results.append({"name": run["name"], "exit_code": run["exit_code"],
                        **{k: data[k] for k in ["offered", "admitted", "acknowledged", "rejected", "misses_10ms", "cohort_seconds", "drain_seconds"]}})
    return results


def report_gate():
    folder = HERE / "report-inadequate"
    folder.mkdir(exist_ok=True)
    scenario = {"mode": "aggregate", "frame_bytes": 65536, "connections": 1,
                "rate": 1000, "duration": 1, "phase": "measurement", "pair": 0}
    result = {"valid": True, "success": True, "sustainable": False,
              "generator_adequate": False, "scheduler_late_over_1ms": 1000,
              "offered": 1000, "admitted": 1000, "rejected": 0, "failed": 0,
              "window_seconds": 1, "window_completed_frames": 1000,
              "completed_frames": 1000, "misses_1ms": 1000,
              "latency_ns": {"p50": 10_000_000, "p99": 10_000_000,
                             "p999": 10_000_000, "max": 10_000_000}}
    entries = [{"scenario": scenario, "result": result, "server": server,
                "client": "csharp", "path": f"fixture-{server}"} for server in ["csharp", "cpp"]]
    (folder / "results.json").write_text(json.dumps(entries), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(ROOT / "bench/report.py"), str(folder)],
                               cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = (folder / "report.md").read_text(encoding="utf-8")
    csv = (folder / "trials.csv").read_text(encoding="utf-8")
    assert "| csharp | csharp | aggregate | 65536 | 1 | 1000 | 1 | 1000.00" in report
    assert "generator_adequate" not in csv and "sustainable" not in csv and "misses" not in csv
    return {"fixture": str(folder), "exit_code": completed.returncode,
            "inadequate_pairs_presented": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=["all", "metadata", "client", "report"], default="all", nargs="?")
    args = parser.parse_args()
    evidence = {"binary_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [CS, CPP]}}
    if args.check in ["all", "metadata"]:
        evidence["metadata"] = bad_metadata()
    if args.check in ["all", "client"]:
        evidence["client"] = client_checks()
    if args.check in ["all", "report"]:
        evidence["report"] = report_gate()
    (HERE / f"evidence-{args.check}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
