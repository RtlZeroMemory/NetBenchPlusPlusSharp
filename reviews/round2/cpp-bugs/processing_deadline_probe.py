"""Bounded normal-processing frame-budget probe; no injected pause."""
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
ROW = b'{"id":1,"timestamp_ns":1,"source":1,"kind":"kind00","value_milli":1,"flags":1,"message":""}'
PAYLOAD = b"[" + b",".join([ROW] * 170000) + b"]"


def run(mode, timeout):
    name = f"processing-{mode}-{timeout}ms"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    args = [str(EXE), "server", "--port", str(port), "--control-stdin", "1",
            "--mode", mode, "--max-frame", "16777216", "--max-connections", "1",
            "--workers", "1", "--socket-buffer", "16777216", "--frame-timeout-ms", str(timeout),
            "--idle-timeout-ms", "5000", "--output", str(OUT / (name + ".json"))]
    proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        ready = proc.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16777216)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            begin(sock)
            packet = frame(2, 2, PAYLOAD)
            started = time.perf_counter()
            sent = True
            try:
                sock.sendall(packet)
            except (ConnectionResetError, ConnectionAbortedError):
                sent = False
            send_seconds = time.perf_counter() - started
            try:
                answer = sock.recv(32)
                state = "closed" if not answer else "response"
            except (ConnectionResetError, ConnectionAbortedError):
                state = "closed"
            elapsed = time.perf_counter() - started
        stdout, stderr = proc.communicate("stop\n", timeout=3)
        assert proc.returncode == 0, stderr
        (OUT / (name + ".stdout.log")).write_text(ready + stdout, encoding="utf-8")
        (OUT / (name + ".stderr.log")).write_text(stderr, encoding="utf-8")
        metrics = json.loads(stdout)
        return {"mode": mode, "args": args, "deadline_ms": timeout, "payload_bytes": len(PAYLOAD),
                "send_completed": sent, "send_seconds": send_seconds, "read_state": state,
                "data_to_reply_or_close_seconds": elapsed, "completed_frames": metrics["completed_frames"],
                "completed_records": metrics["completed_records"], "rejection_reasons": metrics["rejection_reasons"],
                "client_tcp_nodelay": True, "received_protocol_bytes": metrics["received_protocol_bytes"],
                "peak_owned_storage_per_connection": metrics["peak_owned_storage_per_connection"]}
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def main():
    global EXE, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=EXE)
    parser.add_argument("--output-dir", type=pathlib.Path, default=OUT / f"processing-recheck-{time.time_ns()}")
    parser.add_argument("--deadline-ms", type=int, default=30)
    args = parser.parse_args()
    EXE, OUT = args.exe.resolve(), args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(EXE.read_bytes()).hexdigest()
    cases = [run(mode, args.deadline_ms) for mode in ("aggregate", "retain-reuse", "retain-allocate")]
    result = {"binary_sha256": before, "binary_unchanged": before == hashlib.sha256(EXE.read_bytes()).hexdigest(),
              "cases": cases}
    (OUT / "processing-evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result["binary_unchanged"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
