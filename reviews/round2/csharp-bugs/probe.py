"""Bounded loopback deadline probes; does not rebuild or change the benchmark."""
from __future__ import annotations

import hashlib
import json
import socket
import struct
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DLL = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
HEADER = struct.Struct(">IHHQ")


def exact(sock, length):
    result = bytearray()
    while len(result) < length:
        part = sock.recv(length - len(result))
        if not part:
            raise EOFError("connection closed")
        result.extend(part)
    return bytes(result)


def frame(kind, sequence, body=b""):
    return HEADER.pack(len(body), 1, kind, sequence) + body


def probe(name, partial=False):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    output = OUT / f"{name}.json"
    log_path = OUT / f"{name}.log"
    args = ["dotnet", str(DLL), "server", "--port", str(port),
            "--max-frame", "4096", "--max-connections", "1",
            "--frame-timeout-ms", "100", "--idle-timeout-ms", "100",
            "--pause-every", "1", "--pause-ms", "900",
            "--control-stdin", "1", "--output", str(output)]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            until = time.monotonic() + 10
            while '"event":"ready"' not in log_path.read_text(encoding="utf-8"):
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                assert time.monotonic() < until, "startup timeout"
                time.sleep(.01)
            with socket.create_connection(("127.0.0.1", port), timeout=2) as first:
                first.sendall(frame(1, 1, bytes(32)))
                assert exact(first, 16) == frame(0x8001, 1)
                start = time.monotonic()
                first.sendall(frame(2, 2, b"[]")[:-1] if partial else frame(2, 2, b"[]"))
                first.settimeout(.35)
                try:
                    initial = first.recv(32)
                    first_state = "closed" if initial == b"" else "response"
                except socket.timeout:
                    first_state = "still_open"
                initial_seconds = time.monotonic() - start
                with socket.create_connection(("127.0.0.1", port), timeout=2) as second:
                    second.sendall(frame(1, 1, bytes(32)))
                    try:
                        second_response = exact(second, 16)
                        second_state = "accepted" if second_response == frame(0x8001, 1) else "unexpected_response"
                    except (EOFError, ConnectionResetError):
                        second_state = "rejected"
                if first_state == "still_open":
                    first.settimeout(2)
                    late = first.recv(32)
                    assert late == b"", f"unexpected success ACK: {late.hex()}"
                elapsed = time.monotonic() - start
        finally:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
            process.wait(timeout=5)
            process.stdin.close()
        assert process.returncode == 0
    metrics = json.loads(output.read_text(encoding="utf-8"))
    return {"name": name, "partial_body": partial, "args": args,
            "first_state_at_initial_read": first_state,
            "initial_read_seconds": initial_seconds,
            "connection_closed_seconds": elapsed,
            "second_connection_state": second_state,
            "errors": metrics["errors"], "completed": metrics["completed"],
            "rejected_connections": metrics["rejected_connections"]}


def main():
    before = hashlib.sha256(DLL.read_bytes()).hexdigest()
    results = [probe("partial-read-control", partial=True)]
    results.extend(probe(f"processing-pause-{i}") for i in range(3))
    after = hashlib.sha256(DLL.read_bytes()).hexdigest()
    assert before == after, "benchmark binary changed during review"
    assert results[0]["first_state_at_initial_read"] == "closed"
    assert results[0]["second_connection_state"] == "accepted"
    data = {"binary_sha256": before, "binary_unchanged": before == after, "cases": results}
    (OUT / "evidence.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
