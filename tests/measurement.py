"""Adversarial load-generator checks against a deliberately faulty Python peer."""
from __future__ import annotations

import argparse
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from protocol import ACK, TOTAL, WIRE, ROOT, command, exact, frame
sys.path.insert(0, str(ROOT / "tools"))
import corpus


class Peer:
    def __init__(self, fault):
        self.fault = fault
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.stop = threading.Event()
        self.peers, self.threads = [], []
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sock.settimeout(5)
            self.peers.append(sock)
            thread = threading.Thread(target=self.serve, args=(sock,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def serve(self, sock):
        batches = size = records = 0
        digest = corpus.OFFSET
        counts, sums = [0] * 64, [0] * 64
        stalled = False
        try:
            while not self.stop.is_set():
                length, version, kind, seq = WIRE.unpack(exact(sock, 16))
                body = exact(sock, length)
                if kind == 1:
                    batches = size = records = 0
                    digest, counts, sums = corpus.OFFSET, [0] * 64, [0] * 64
                    reply = b""
                elif kind == 2:
                    result = corpus.process(body)
                    if self.fault == "drop":
                        return
                    if self.fault == "withhold":
                        self.stop.wait(10)
                        return
                    if self.fault == "pause" and not stalled:
                        time.sleep(0.1)
                        stalled = True
                    if self.fault == "ack-inside-drain":
                        time.sleep(1.2)  # After T0+drain, before T1+drain (1.4 seconds).
                    if self.fault == "overload":
                        time.sleep(0.05)
                    batches += 1
                    size += len(body)
                    records += result["records"]
                    digest = corpus.fnv(struct.pack(">QQQ", seq, result["records"], result["digest"]), digest)
                    counts = [a + b for a, b in zip(counts, result["counts"])]
                    sums = [a + b for a, b in zip(sums, result["sums"])]
                    reply = ACK.pack(result["records"], result["digest"] ^ (1 if self.fault == "corrupt" else 0))
                    if self.fault == "wrong-sequence":
                        seq += 1
                    if self.fault == "oversized-response":
                        sock.sendall(WIRE.pack(0xffffffff, 1, kind | 0x8000, seq))
                        return
                elif kind == 3:
                    reply = TOTAL.pack(batches + (1 if self.fault == "wrong-summary" else 0),
                                       size, records, digest, *counts, *sums)
                else:
                    return
                sock.sendall(frame(kind | 0x8000, seq, reply))
        except (OSError, AssertionError):
            pass
        finally:
            sock.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        for sock in self.peers:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.thread.join(2)
        for thread in self.threads:
            thread.join(2)


def run(binary, fault, folder):
    peer = Peer(fault)
    output = folder / f"{fault}.json"
    args = command(binary) + ["client", "--port", str(peer.port), "--mode", "aggregate",
        "--corpus", str(ROOT / "tests/fixtures/golden.bin"), "--connections", "1",
        "--warmup", "0", "--duration", "0.4", "--rate", "1000" if fault == "overload" else "1" if fault == "ack-inside-drain" else "50",
        "--window", "1" if fault == "overload" else "16", "--drain-seconds", "1",
        "--output", str(output)]
    try:
        completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
    finally:
        peer.close()
    (folder / f"{fault}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if fault in ["drop", "withhold", "corrupt", "wrong-sequence", "oversized-response", "wrong-summary"]:
        assert completed.returncode != 0, f"Fault {fault} obtained successful client exit"
        data = json.loads(output.read_text(encoding="utf-8-sig"))
        assert data.get("valid") is False or data.get("success") is False, f"Fault {fault} marked valid"
        assert data["offered"] == 20, "Failed run lost intended scheduled demand"
        assert data["offered"] == data["admitted"] + data["rejected"]
        if fault in ["drop", "corrupt", "wrong-sequence", "oversized-response"]:
            assert data["aborted_schedule_rejected"] > 0
            assert data["generator_adequate"] is False
        if fault == "withhold":
            assert data["timedout"] > 0 and data["unresolved"] > 0
            assert data["drain_seconds"] >= .9 and data["cohort_seconds"] >= 1.3
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        data = json.loads(output.read_text(encoding="utf-8-sig"))
        assert data["completed_frames"] > 0
        if fault == "overload":
            assert data["rejected"] > 0, "Overload admission failures disappeared"
            assert data["offered"] == data["admitted"] + data["rejected"], "Offered demand mismatch"
        if fault == "pause":
            assert data["latency_ns"]["max"] >= 80_000_000, "Injected 100ms pause invisible"
        if fault == "ack-inside-drain":
            assert data["acknowledged"] == 1 and data["unresolved"] == 0
            assert data["cohort_seconds"] >= 1.15 and data["drain_seconds"] >= .75
    return {"fault": fault, "exit_code": completed.returncode, "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    args = parser.parse_args()
    binary = args.client.resolve()
    folder = ROOT / "results/measurement" / binary.stem
    folder.mkdir(parents=True, exist_ok=True)
    for fault in ["none", "pause", "overload", "drop", "withhold", "corrupt", "wrong-sequence", "oversized-response", "wrong-summary", "ack-inside-drain"]:
        print(json.dumps(run(binary, fault, folder)), flush=True)


if __name__ == "__main__":
    main()
