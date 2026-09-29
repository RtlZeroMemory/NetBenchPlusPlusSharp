"""Independent focused network recheck of the round-2 frozen .NET DLL."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
DLL = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
EXPECTED = "cb2e213e9089d240236fcfa52dede69c8ef1bd4fe064d65592123c237f570fdd"
OUT = Path(__file__).resolve().parent / ("evidence-" + str(time.time_ns()))
HEADER = struct.Struct(">IHHQ")
def frame(kind, seq, body=b""):
    return HEADER.pack(len(body), 1, kind, seq) + body
def exact(sock, n):
    result = bytearray()
    while len(result) < n:
        part = sock.recv(n - len(result))
        if not part:
            raise EOFError("closed")
        result += part
    return bytes(result)
def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    sock.sendall(frame(1, 1, bytes(32)))
    assert exact(sock, 16) == frame(0x8001, 1)
    return sock
def closed(sock):
    try:
        assert sock.recv(1) == b"", "unexpected ACK"
    except (ConnectionResetError, ConnectionAbortedError):
        pass
def ack(sock, seq):
    assert exact(sock, 16) == HEADER.pack(16, 1, 0x8002, seq)
    return struct.unpack(">QQ", exact(sock, 16))
def row(message="x"):
    return json.dumps(dict(id=1,timestamp_ns=2,source=3,kind="kind00",value_milli=4,flags=0,message=message), separators=(",", ":")).encode()
@contextmanager
def server(name, mode="aggregate", frame_ms=100, idle_ms=100, pause=0):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    output = OUT / (name + ".json")
    args = ["dotnet", str(DLL), "server", "--port", str(port), "--mode", mode,
            "--max-frame", "16777216", "--max-connections", "1", "--retain-batches", "1",
            "--frame-timeout-ms", str(frame_ms), "--idle-timeout-ms", str(idle_ms),
            "--control-stdin", "1", "--output", str(output)]
    if pause:
        args += ["--pause-every", "1", "--pause-ms", str(pause)]
    with (OUT / (name + ".log")).open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 10
            while '"event":"ready"' not in (OUT / (name + ".log")).read_text(encoding="utf-8"):
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(.01)
            yield process, port, output
        finally:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
            process.wait(timeout=3)
            process.stdin.close()
            assert process.returncode == 0
def expiry(mode, payload, name, **options):
    with server(name, mode, **options) as (_, port, output):
        with connect(port) as sock:
            start = time.monotonic()
            sock.sendall(frame(2, 2, payload))
            sock.settimeout(.5)
            closed(sock)
            elapsed = time.monotonic() - start
        with connect(port):
            pass
    metrics = json.loads(output.read_text())
    assert metrics["completed"] == 0 and metrics["errors"] == {"deadline":1}, metrics
    assert metrics["rejected_connections"] == 0, metrics
    assert metrics["transport"]["ReceiveBytes"] >= len(payload) + 64, metrics
    return dict(case=name, elapsed=elapsed, peak=metrics["owned_storage_peak_per_connection"])
def capacity(mode, failure):
    name = mode + ("-failed-parse-capacity" if failure else "-capacity-quiet-end-eof")
    small = b"[" + row("x" * 1000) + b"]"
    with server(name, mode, frame_ms=1000, idle_ms=1000) as (_, port, output):
        with connect(port) as sock:
            for seq in (2, 3):
                sock.sendall(frame(2, seq, small))
                assert ack(sock, seq)[0] == 1
            if failure:
                malformed = b"[" + b",".join([row("z" * 2000)] * 100) + b',{"id":'
                sock.sendall(frame(2, 4, malformed))
                closed(sock)
            else:
                # Exceed a whole previous frame deadline before issuing End, then before quiet EOF.
                time.sleep(1.05)
                sock.sendall(frame(3, 4))
                assert exact(sock, 16) == HEADER.pack(1056, 1, 0x8003, 4)
                summary = exact(sock, 1056)
                assert struct.unpack(">QQQ", summary[:24]) == (2, 2*len(small), 2)
                time.sleep(1.05)
                sock.shutdown(socket.SHUT_WR)
                closed(sock)
    metrics = json.loads(output.read_text())
    assert metrics["completed"] == 2
    assert metrics["owned_storage_peak_per_connection"] == (263912 if failure else 3536), metrics
    assert bool(metrics["errors"]) == failure, metrics
    return dict(case=name, peak=metrics["owned_storage_peak_per_connection"], errors=metrics["errors"])
def shutdown(mode):
    name = mode + "-shutdown-pause"
    with server(name, mode, frame_ms=30000, idle_ms=30000, pause=10000) as (process, port, output):
        with connect(port) as sock:
            sock.sendall(frame(2, 2, b"[]"))
            time.sleep(.05)
            start = time.monotonic()
            process.stdin.write("stop\n")
            process.stdin.flush()
            process.wait(timeout=.6)
            elapsed = time.monotonic() - start
            closed(sock)
    metrics = json.loads(output.read_text())
    assert metrics["completed"] == 0 and metrics["errors"] == {}, metrics
    return dict(case=name, elapsed=elapsed)
def main():
    OUT.mkdir(parents=True)
    assert hashlib.sha256(DLL.read_bytes()).hexdigest() == EXPECTED
    big = b"[" + b",".join([row()] * 170000) + b"]"
    results = []
    for mode in ("aggregate", "retain-reuse", "retain-allocate"):
        results += [expiry(mode, big, mode + "-cpu"), expiry(mode, b"[]", mode + "-pause", pause=900), shutdown(mode)]
    results += [expiry("transport", b"[]", "transport-idle-pause", frame_ms=1000, idle_ms=100, pause=900)]
    for mode in ("retain-reuse", "retain-allocate"):
        results += [capacity(mode, True), capacity(mode, False)]
    assert hashlib.sha256(DLL.read_bytes()).hexdigest() == EXPECTED
    evidence = dict(binary_sha256=EXPECTED, cases=results)
    (OUT / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(dict(folder=str(OUT), **evidence), indent=2))
if __name__ == "__main__":
    main()
