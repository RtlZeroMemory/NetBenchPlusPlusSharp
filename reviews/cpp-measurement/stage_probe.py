"""Check whether native sampled processing includes an intentional service pause."""
import json
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import corpus

WIRE = struct.Struct(">IHHQ")
TOTAL = struct.Struct(">QQQQ64Q64q")
CPP = ROOT / "src/cpp/build/Release/tcpbench.exe"


def exact(sock, size):
    result = bytearray()
    while len(result) < size:
        data = sock.recv(size - len(result))
        assert data, "unexpected EOF"
        result.extend(data)
    return bytes(result)


def exchange(sock, kind, sequence, body):
    start = time.perf_counter()
    sock.sendall(WIRE.pack(len(body), 1, kind, sequence) + body)
    length, version, reply_kind, reply_sequence = WIRE.unpack(exact(sock, 16))
    assert (version, reply_kind, reply_sequence) == (1, kind | 0x8000, sequence)
    response = exact(sock, length)
    return response, time.perf_counter() - start


def main():
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    output = HERE / "stage-server.json"
    command = [str(CPP), "server", "--port", str(port), "--max-frame", "4096",
        "--workers", "1", "--max-connections", "1", "--mode", "aggregate",
        "--pause-every", "1", "--pause-ms", "100", "--control-stdin", "1",
        "--output", str(output)]
    server = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        ready = server.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            begin, _ = exchange(sock, 1, 1, bytes(32))
            assert begin == b""
            elapsed = []
            digest = corpus.OFFSET
            for seq in [2, 3]:
                ack, latency = exchange(sock, 2, seq, b"[]")
                assert ack == struct.pack(">QQ", 0, corpus.OFFSET)
                elapsed.append(latency)
                digest = corpus.fnv(struct.pack(">QQQ", seq, 0, corpus.OFFSET), digest)
            summary, _ = exchange(sock, 3, 4, b"")
            assert summary == TOTAL.pack(2, 4, 0, digest, *([0] * 64), *([0] * 64))
        out, err = server.communicate("stop\n", timeout=5)
        (HERE / "stage-server.log").write_text(ready + out + err, encoding="utf-8")
        assert server.returncode == 0, err
        result = json.loads(output.read_text(encoding="utf-8"))
        evidence = {"command": command, "client_elapsed_seconds": elapsed, "result": result}
        assert all(delay > .08 for delay in elapsed), evidence
        assert result["processing_samples"] == 1, evidence
        (HERE / "stage-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"client_elapsed_seconds": elapsed, "processing_samples": result["processing_samples"],
            "processing_max_ns": result["processing_elapsed_ns"]["max_ns"],
            "processing_p999_ns": result["processing_elapsed_ns"]["p999_ns"],
            "diagnostic_faults": result["diagnostic_faults"]}))
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()


if __name__ == "__main__":
    main()
