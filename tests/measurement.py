"""Adversarial load-generator checks against a deliberately faulty Python peer."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from protocol import ACK, WIRE, ROOT, Summary, command, exact, frame
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
        summary, stalled = Summary(), False
        try:
            while not self.stop.is_set():
                length, version, kind, seq = WIRE.unpack(exact(sock, 16))
                body = exact(sock, length)
                if kind == 1:
                    summary, reply = Summary(), b""
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
                    summary.add(seq, body, result)
                    reply = ACK.pack(result["records"], result["digest"] ^ (1 if self.fault == "corrupt" else 0))
                    if self.fault == "wrong-sequence":
                        seq += 1
                    if self.fault == "oversized-response":
                        sock.sendall(WIRE.pack(0xffffffff, 1, kind | 0x8000, seq))
                        return
                elif kind == 3:
                    reply = summary.pack(1 if self.fault == "wrong-summary" else 0)
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


FAILING = ["drop", "withhold", "withhold-short-drain", "corrupt", "wrong-sequence",
           "oversized-response", "wrong-summary"]


def run(binary, fault, folder):
    peer = Peer("withhold" if fault == "withhold-short-drain" else fault)
    output = folder / f"{fault}.json"
    rate = {"overload": "1000", "ack-inside-drain": "1", "withhold-short-drain": "100", "closed": "0"}.get(fault, "50")
    duration = "0.1" if fault == "withhold-short-drain" else "0.4"
    drain = "0.5" if fault == "withhold-short-drain" else "1"
    args = command(binary) + ["client", "--port", str(peer.port), "--mode", "aggregate",
        "--corpus", str(ROOT / "tests/fixtures/golden.bin"), "--connections", "1",
        "--warmup", "0", "--duration", duration, "--rate", rate,
        "--window", "1" if fault == "overload" else "8" if fault == "withhold-short-drain" else "16",
        "--drain-seconds", drain, "--output", str(output)]
    try:
        completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
    finally:
        peer.close()
    (folder / f"{fault}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    data = json.loads(output.read_text(encoding="utf-8-sig"))
    counts, cohort = data["counts"], data["cohort"]
    # Accounting identities hold for every run, healthy or failed.
    assert counts["offered"] == counts["admitted"] + counts["rejected"], counts
    assert counts["admitted"] == counts["acknowledged"] + counts["failed"] + counts["unresolved"], counts
    assert counts["timedout"] <= counts["unresolved"], counts
    assert sum(h["count"] for h in data["latency"]["by_size"]) == counts["acknowledged"]
    assert data["latency"]["scheduled"]["count"] == counts["acknowledged"]
    if fault in FAILING:
        assert completed.returncode != 0 and data["valid"] is False, f"Fault {fault} marked valid"
        if fault != "withhold-short-drain":
            assert counts["offered"] == 20, "Failed run lost intended scheduled demand"
        if fault in ["drop", "corrupt", "wrong-sequence", "oversized-response"]:
            assert counts["aborted_schedule_rejected"] > 0, counts
        if fault == "corrupt":
            assert counts["failed"] >= 1, counts
        if fault == "withhold":
            assert counts["timedout"] > 0 and counts["unresolved"] > 0
            assert cohort["drain_seconds"] >= .9 and cohort["seconds"] >= 1.3
        if fault == "withhold-short-drain":
            # Failed waiting is visible in the cohort and drain intervals, not hidden.
            assert counts["acknowledged"] == 0 and counts["timedout"] > 0, counts
            assert cohort["drain_limit_seconds"] == .5 and cohort["drain_seconds"] >= .45, cohort
            assert abs(cohort["seconds"] - data["window"]["seconds"] - cohort["drain_seconds"]) < .002, cohort
    else:
        assert completed.returncode == 0 and data["valid"], completed.stdout + completed.stderr
        assert counts["acknowledged"] > 0
        if fault == "overload":
            assert counts["rejected"] > 0, "Overload admission failures disappeared"
            assert data["misses"]["over_1ms"] >= counts["rejected"], "Rejected demand must count as misses"
        if fault == "pause":
            assert data["latency"]["scheduled"]["max_ns"] >= 80_000_000, "Injected 100ms pause invisible"
        if fault == "ack-inside-drain":
            assert counts["acknowledged"] == 1 and counts["unresolved"] == 0
            assert cohort["seconds"] >= 1.15 and cohort["drain_seconds"] >= .75
        if fault == "closed":
            assert data["load_model"] == "closed-loop" and counts["rejected"] == 0
            assert data["generator"]["late_over_1ms"] == 0 and data["generator"]["lateness"]["count"] == 0
    return {"fault": fault, "exit_code": completed.returncode, "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    args = parser.parse_args()
    binary = args.client.resolve()
    folder = ROOT / "results/measurement" / binary.stem
    folder.mkdir(parents=True, exist_ok=True)
    for fault in ["none", "closed", "pause", "overload", *FAILING, "ack-inside-drain"]:
        print(json.dumps(run(binary, fault, folder)), flush=True)


if __name__ == "__main__":
    main()
