"""EOF has no stale deadline; End after a quiet interval gets a fresh frame budget."""
import argparse
import hashlib
import json
import pathlib
import socket
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
from protocol import ACK, TOTAL, begin, encode, event, frame, response
import corpus


def run(exe, out, mode, control):
    name = f"{mode}-{control}"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    args = [str(exe), "server", "--port", str(port), "--control-stdin", "1", "--mode", mode,
            "--max-frame", "4096", "--max-connections", "1", "--workers", "1",
            "--frame-timeout-ms", "100", "--idle-timeout-ms", "100",
            "--output", str(out / (name + ".json"))]
    proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        ready = proc.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        payload = encode([event(message="x" * 1000)])
        expected = corpus.process(payload)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            begin(sock)
            sock.sendall(frame(2, 2, payload))
            assert ACK.unpack(response(sock, 2, 2)) == (expected["records"], expected["digest"])
            time.sleep(.15)
            if control == "end":
                sock.sendall(frame(3, 3))
                digest = corpus.fnv(struct.pack(">QQQ", 2, expected["records"], expected["digest"]))
                assert TOTAL.unpack(response(sock, 3, 3)) == (1, len(payload), expected["records"], digest,
                                                            *expected["counts"], *expected["sums"])
            sock.shutdown(socket.SHUT_WR)
            assert sock.recv(1) == b"", "quiet EOF was not graceful"
        stdout, stderr = proc.communicate("stop\n", timeout=3)
        (out / (name + ".stdout.log")).write_text(ready + stdout, encoding="utf-8")
        (out / (name + ".stderr.log")).write_text(stderr, encoding="utf-8")
        assert proc.returncode == 0, stderr
        metrics = json.loads(stdout)
        assert metrics["graceful_connections"] == 1, metrics
        assert metrics["completed_frames"] == 1 and metrics["rejection_reasons"] == {}, metrics
        return {"mode": mode, "control": control, "quiet_seconds": .15,
                "frame_and_idle_deadline_ms": 100, "graceful_connections": 1,
                "completed_frames": 1, "rejection_reasons": {}}
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=ROOT / "src/cpp/build/Release/tcpbench.exe")
    parser.add_argument("--output-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parent / f"quiet-recheck-{time.time_ns()}")
    args = parser.parse_args()
    exe, out = args.exe.resolve(), args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(exe.read_bytes()).hexdigest()
    cases = [run(exe, out, mode, control) for mode in ("retain-reuse", "retain-allocate")
             for control in ("eof", "end")]
    result = {"binary_sha256": before, "binary_unchanged": before == hashlib.sha256(exe.read_bytes()).hexdigest(),
              "cases": cases}
    (out / "evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result["binary_unchanged"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
