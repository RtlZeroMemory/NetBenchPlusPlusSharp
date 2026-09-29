"""Two equal owned batches expose post-commit versus scratch-inclusive capacity."""
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
from protocol import ACK, begin, encode, event, frame, response
import corpus

OUT = pathlib.Path(__file__).resolve().parent
EXE = ROOT / "src/cpp/build/Release/tcpbench.exe"


def run(mode):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    args = [str(EXE), "server", "--port", str(port), "--control-stdin", "1",
            "--mode", mode, "--max-frame", "4096", "--max-connections", "1",
            "--retain-batches", "1", "--retain-bytes", "1048576", "--output", str(OUT / (mode + "-capacity.json"))]
    proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        ready = proc.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        payload = encode([event(message="x" * 1000)])
        want = corpus.process(payload)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            begin(sock)
            for sequence in (2, 3):
                sock.sendall(frame(2, sequence, payload))
                assert ACK.unpack(response(sock, 2, sequence)) == (want["records"], want["digest"])
            sock.sendall(frame(3, 4))
            assert struct.unpack_from(">Q", response(sock, 3, 4))[0] == 2
        stdout, stderr = proc.communicate("stop\n", timeout=3)
        assert proc.returncode == 0, stderr
        (OUT / (mode + "-capacity.stdout.log")).write_text(ready + stdout, encoding="utf-8")
        (OUT / (mode + "-capacity.stderr.log")).write_text(stderr, encoding="utf-8")
        metrics = json.loads(stdout)
        return {"mode": mode, "args": args, "completed_frames": metrics["completed_frames"],
                "reported_peak_owned_storage_per_connection": metrics["peak_owned_storage_per_connection"],
                "reported_peak_retained_canonical_per_connection": metrics["peak_retained_canonical_per_connection"]}
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def main():
    global EXE, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=pathlib.Path, default=EXE)
    parser.add_argument("--output-dir", type=pathlib.Path, default=OUT / f"capacity-recheck-{time.time_ns()}")
    args = parser.parse_args()
    EXE, OUT = args.exe.resolve(), args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(EXE.read_bytes()).hexdigest()
    cases = [run(mode) for mode in ("retain-allocate", "retain-reuse")]
    result = {"binary_sha256": before, "binary_unchanged": before == hashlib.sha256(EXE.read_bytes()).hexdigest(),
              "message_bytes_per_batch": 1000, "records_per_batch": 1, "batches": 2, "cases": cases}
    (OUT / "capacity-evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert result["binary_unchanged"]
    assert all(case["completed_frames"] == 2 for case in cases)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
