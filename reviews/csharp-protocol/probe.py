"""Adversarial probes of the frozen .NET binary; writes only this review folder."""
from __future__ import annotations

import json
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
import protocol as p

BINARY = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
BASE = p.encode([p.event(message="x")])


class Server:
    def __init__(self, mode="aggregate", **options):
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            self.port = temporary.getsockname()[1]
        self.log_path = FOLDER / f"server-{mode}-{self.port}.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        config = {"port": self.port, "mode": mode, "max-frame": 65536,
                  "idle-timeout-ms": 150, "frame-timeout-ms": 650,
                  "control-stdin": 1, "retain-batches": 3,
                  "retain-bytes": 20000, **options}
        command = ["dotnet", str(BINARY), "server"]
        for name, value in config.items():
            command.extend(["--" + name, str(value)])
        self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                                        stdout=self.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.log_path.read_text())
            if self.log_path.exists() and '"event":"ready"' in self.log_path.read_text():
                return
            time.sleep(.02)
        raise AssertionError("Server startup timed out")

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.write("stop\n")
            self.process.stdin.flush()
        self.process.wait(timeout=10)
        self.process.stdin.close()
        self.log.close()
        output = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        assert self.process.returncode == 0, output
        return output[-1]


def unicode_cases():
    invalid = []
    sequences = [b"\x80", b"\xc0\x80", b"\xc1\xbf", b"\xc2", b"\xc2x",
                 b"\xe0\x80\x80", b"\xe0\x9f\xbf", b"\xe1\x80",
                 b"\xed\xa0\x80", b"\xed\xbf\xbf", b"\xf0\x80\x80\x80",
                 b"\xf0\x8f\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80",
                 b"\xfe", b"\xff", b"\xef\xbf", b"\x00", b"\x1f"]
    for sequence in sequences:
        for text in [sequence, b"\\u0041" + sequence, sequence + b"\\n"]:
            invalid.append(BASE.replace(b'"message":"x"', b'"message":"' + text + b'"'))
        invalid.append(BASE.replace(b'"id"', b'"id' + sequence + b'\\u0064"'))
    for text in [b"\\ud800", b"\\udfff", b"\\ud800\\ud800", b"\\udfff\\ud800",
                 b"\\udbff\\ue000", b"\\ud800\\n\\udc00", b"\\ud800\\\\udc00"]:
        invalid.append(BASE.replace(b'"message":"x"', b'"message":"' + text + b'"'))
    valid = [p.encode([p.event(message=text)]) for text in [
        "\U00010000", "\U0010ffff", "\uffff\ufdd0", "\ufeff", "a" * 4096,
        "😀" * 1024, "\u0000" * 4096, "é" * 2048,
        "\\ud800", "\\ud800\\udc00"]]
    valid.extend(json.dumps([p.event(message=text)], ensure_ascii=True,
                           separators=(",", ":")).encode() for text in
                 ["😀" * 1024, "\U0010ffff", "\U00010000", "é" * 2048])
    invalid.extend(p.encode([p.event(message=text)]) for text in ["😀" * 1024 + "x", "é" * 2049])
    return valid, invalid


def semantic_suite():
    valid, invalid = unicode_cases()
    for ws in [b"\x0b", b"\x0c", b"\xa0", b"\xc2\xa0", b"\xe2\x80\xa8"]:
        invalid += [ws + BASE, BASE + ws]
    for number in [b"+1", b"-0", b"0e0", b"0E+0", b"0.000", b"01", b"--1",
                   b"18446744073709551616", b"9" * 20000]:
        invalid.append(BASE.replace(b'"id":42', b'"id":' + number))
    valid.append(BASE.replace(b'"value_milli":-12345', b'"value_milli":-0'))
    valid.append(BASE.replace(b'"kind":"kind03"', b'"kind":"\\u006b\\u0069\\u006e\\u0064\\u0030\\u0033"'))
    valid.append(BASE.replace(b'"timestamp_ns"', b'"\\u0074\\u0069\\u006d\\u0065\\u0073\\u0074\\u0061\\u006d\\u0070\\u005f\\u006e\\u0073"'))
    total = 0
    for mode in ["aggregate", "retain-reuse", "retain-allocate"]:
        server = Server(mode)
        try:
            with p.connection(server.port) as sock:
                p.begin(sock)
                counts, sums, digest, size, records = [0] * 64, [0] * 64, p.corpus.OFFSET, 0, 0
                for index, payload in enumerate(valid):
                    expected = p.corpus.process(payload)
                    seq = index + 2
                    sock.sendall(p.frame(2, seq, payload))
                    actual = p.ACK.unpack(p.response(sock, 2, seq))
                    assert actual == (expected["records"], expected["digest"]), (mode, index, actual)
                    counts = [a + b for a, b in zip(counts, expected["counts"])]
                    sums = [a + b for a, b in zip(sums, expected["sums"])]
                    digest = p.corpus.fnv(struct.pack(">QQQ", seq, *actual), digest)
                    size += len(payload)
                    records += expected["records"]
                    total += 1
                sock.sendall(p.frame(3, len(valid) + 2))
                assert p.TOTAL.unpack(p.response(sock, 3, len(valid) + 2)) == (
                    len(valid), size, records, digest, *counts, *sums)
            for index, payload in enumerate(invalid):
                try:
                    p.corpus.process(payload)
                except (ValueError, TypeError):
                    pass
                else:
                    raise AssertionError(("Oracle accepted invalid", index, payload[:160]))
                with p.connection(server.port) as sock:
                    p.begin(sock)
                    sock.sendall(p.frame(2, 2, payload))
                    p.expect_closed(sock)
                total += 1
        finally:
            result = server.stop()
        assert result["completed"] == len(valid), result
    return total


def lifetime_suite():
    total = 0
    server = Server(**{"max-connections": 32})
    try:
        # A trickle stays below the inactivity timeout but must hit the absolute deadline.
        with p.connection(server.port) as sock:
            p.begin(sock)
            start = time.monotonic()
            sock.sendall(p.frame(2, 2, b"[]", length=1000))
            while time.monotonic() - start < .85:
                try:
                    sock.sendall(b" ")
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    break
                time.sleep(.08)
            p.expect_closed(sock)
            elapsed = time.monotonic() - start
            assert elapsed < 1.5, elapsed
        total += 1
        # Reset during body reception must release admission and allow another request.
        for i in range(30):
            sock = p.connection(server.port)
            p.begin(sock)
            sock.sendall(p.frame(2, 2, b"[", length=1000))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
            sock.close()
        time.sleep(.05)
        with p.connection(server.port) as sock:
            p.begin(sock)
            sock.sendall(p.frame(2, 2, b"[]"))
            assert p.ACK.unpack(p.response(sock, 2, 2)) == (0, p.corpus.OFFSET)
        total += 31
        # Simultaneously cancel quiet, partial-header, and partial-body reads.
        sockets = [p.connection(server.port) for _ in range(12)]
        for i, sock in enumerate(sockets):
            if i % 3 == 1:
                sock.sendall(b"\0")
            elif i % 3 == 2:
                p.begin(sock)
                sock.sendall(p.frame(2, 2, b"[", length=1000))
        start = time.monotonic()
        result = server.stop()
        assert time.monotonic() - start < 3
        for sock in sockets:
            p.expect_closed(sock)
            sock.close()
        total += len(sockets)
        assert result["completed"] == 1, result
    finally:
        if server.process.poll() is None:
            server.stop()
    return total


def process_metadata_probe():
    path = FOLDER / "wrong-bucket.bin"
    expected = p.corpus.process(BASE)
    counts, sums = expected["counts"][:], expected["sums"][:]
    source = 3 * 4 + (7 & 3)
    counts[0], counts[source] = counts[source], counts[0]
    sums[0], sums[source] = sums[source], sums[0]
    path.write_bytes(p.corpus.HEADER.pack(b"TCPBCH01", 1, 0) +
                     p.corpus.META.pack(len(BASE), expected["records"], expected["digest"]) +
                     p.corpus.BINS.pack(*counts, *sums) + BASE)
    run = subprocess.run(["dotnet", str(BINARY), "process", "--corpus", str(path),
                          "--duration", ".001", "--warmup", "0"], cwd=ROOT,
                         capture_output=True, text=True, timeout=10)
    (FOLDER / "wrong-bucket-process.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    actual = json.loads(run.stdout)
    return {"returncode": run.returncode, "valid": actual.get("valid"),
            "fixture": str(path), "expected": "reject category metadata mismatch"}


def final_edge_suite():
    total = 0
    # Force 1-byte read and write completions through the already-independent protocol suite.
    server = Server(**{"io-cap": 1})
    try:
        total += p.run_protocol(server.port, "aggregate")
    finally:
        server.stop()
    # Retention-capacity failure must not count the staged rows as completed work.
    for mode in ["retain-reuse", "retain-allocate"]:
        server = Server(mode, **{"retain-bytes": 50})
        try:
            with p.connection(server.port) as sock:
                p.begin(sock)
                sock.sendall(p.frame(2, 2, BASE))
                p.response(sock, 2, 2)
                two = p.encode([p.event(message="x"), p.event(message="x")])
                sock.sendall(p.frame(2, 3, two))
                p.expect_closed(sock)
            result = server.stop()
            assert result["completed"] == 1 and result["records"] == 1, result
            assert result["errors"].get("retention_capacity") == 1, result
            total += 1
        finally:
            if server.process.poll() is None:
                server.stop()
    # Stop reading replies; repeated End summaries fill the response path quickly.
    server = Server(**{"socket-buffer": 1024})
    try:
        with p.connection(server.port) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            p.begin(sock)
            requests = []
            for i in range(4000):
                requests += [p.frame(3, 2 + 2 * i), p.frame(1, 3 + 2 * i, bytes(32))]
            sock.settimeout(2)
            try:
                sock.sendall(b"".join(requests))
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
                pass
            time.sleep(.85)
            # Earlier valid ACKs may remain queued before the deadline closes the connection.
            while True:
                try:
                    if not sock.recv(65536):
                        break
                except (ConnectionResetError, ConnectionAbortedError):
                    break
        result = server.stop()
        assert result["errors"].get("deadline") == 1, result
        total += 1
    finally:
        if server.process.poll() is None:
            server.stop()
    return total


if __name__ == "__main__":
    FOLDER.mkdir(exist_ok=True, parents=True)
    result = {"semantic_checks": semantic_suite(), "lifetime_checks": lifetime_suite(),
              "final_edge_checks": final_edge_suite(),
              "process_metadata": process_metadata_probe()}
    (FOLDER / "probe-results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
