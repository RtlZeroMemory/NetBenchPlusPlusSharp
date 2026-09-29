"""Read-only adversarial measurements against the frozen Release executables."""
from __future__ import annotations

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
CPP = ROOT / "src/cpp/build/Release/tcpbench.exe"
CS = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
sys.path.insert(0, str(ROOT / "tools"))
import corpus

WIRE = struct.Struct(">IHHQ")
TOTAL = struct.Struct(">QQQQ64Q64q")


def exact(sock, size):
    output = bytearray()
    while len(output) < size:
        part = sock.recv(size - len(output))
        if not part:
            raise EOFError
        output.extend(part)
    return bytes(output)


class Peer:
    def __init__(self, fault=None, delay=0):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen()
        self.listener.settimeout(.1)
        self.stop = threading.Event()
        self.fault, self.delay = fault, delay
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
            sock.settimeout(5)
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
                    self.received.append({"time": time.perf_counter(), "sequence": seq, "length": length})
                    if self.fault == "drop":
                        return
                    if self.fault == "withhold" or self.fault == "withhold-after-one" and batches:
                        self.stop.wait(10)
                        return
                    if self.delay:
                        time.sleep(self.delay)
                    batches += 1
                    size += length
                    digest = corpus.fnv(struct.pack(">QQQ", seq, 0, length), digest)
                    reply = struct.pack(">QQ", 0, length ^ (1 if self.fault == "corrupt" else 0))
                elif kind == 3:
                    counts, sums = [0] * 64, [0] * 64
                    if self.fault == "end-count":
                        counts[63] = 1
                    elif self.fault == "end-sum":
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


def case(name, *, binary=CPP, fault=None, delay=0, duration=.4, warmup=0,
         rate=100, arrival="steady", window=8, drain=.5):
    peer = Peer(fault=fault, delay=delay)
    output = HERE / f"{name}.json"
    binary_args = ["dotnet", str(binary)] if binary.suffix == ".dll" else [str(binary)]
    args = binary_args + ["client", "--port", str(peer.port), "--corpus", str(HERE / "empty.bin"),
        "--mode", "transport", "--connections", "1", "--duration", str(duration),
        "--warmup", str(warmup), "--rate", str(rate), "--arrival", arrival, "--seed", "42",
        "--window", str(window), "--drain-seconds", str(drain), "--output", str(output)]
    started = time.perf_counter()
    try:
        completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
        finished = time.perf_counter()
        result = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else None
        (HERE / f"{name}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        observed = finished - peer.received[0]["time"] if peer.received else None
        evidence = {"name": name, "command": args, "port": peer.port, "exit_code": completed.returncode,
            "wall_seconds": finished - started, "after_first_request_seconds": observed,
            "peer_data_requests": len(peer.received), "result": result}
        return evidence
    finally:
        peer.close()


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    corpus.write_frames(HERE / "empty.bin", [b"[]"])
    evidence = {"binary_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [CPP, CS]}, "cases": []}
    cases = [
        ("steady", dict()),
        ("poisson", dict(arrival="poisson")),
        ("burst", dict(arrival="burst")),
        ("warmup", dict(warmup=.1)),
        ("overload-drain", dict(delay=.025, rate=1000, drain=2)),
        ("end-count", dict(fault="end-count")),
        ("end-sum", dict(fault="end-sum")),
        ("drop", dict(fault="drop")),
        ("corrupt", dict(fault="corrupt")),
        ("withhold", dict(fault="withhold", duration=.1)),
        ("withhold-after-one", dict(fault="withhold-after-one", duration=.1)),
    ]
    for name, settings in cases:
        item = case(name, **settings)
        data = item["result"]
        assert data is not None, item
        assert data["offered"] == data["admitted"] + data["rejected"], item
        assert data["scheduled_latency"]["count"] == data["acknowledged"], item
        assert data["queue_high_water"] <= 8 and data["inflight_bytes_high_water"] <= 16, item
        if settings.get("fault"):
            assert item["exit_code"] != 0 and not data["valid"], item
        else:
            assert item["exit_code"] == 0 and data["valid"], item
        evidence["cases"].append(item)
        print(json.dumps({"name": name, "exit_code": item["exit_code"], "wall_seconds": item["wall_seconds"],
            "after_first_request_seconds": item["after_first_request_seconds"],
            **{key: data[key] for key in ["offered", "admitted", "acknowledged", "rejected", "timedout", "unresolved", "cohort_seconds", "drain_seconds", "error"]}}), flush=True)
    (HERE / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
