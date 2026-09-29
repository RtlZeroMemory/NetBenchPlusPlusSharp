"""Independent adversarial C++ protocol review probes (stdlib only)."""
from __future__ import annotations

import argparse
import ctypes
import itertools
import json
import random
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
import corpus

WIRE = struct.Struct(">IHHQ")
ACK = struct.Struct(">QQ")
TOTAL = struct.Struct(">QQQQ64Q64q")


def packet(kind, seq, payload=b""):
    return WIRE.pack(len(payload), 1, kind, seq) + payload


def exact(sock, size):
    value = bytearray()
    while len(value) < size:
        part = sock.recv(size - len(value))
        if not part:
            raise EOFError(len(value))
        value.extend(part)
    return bytes(value)


def response(sock, kind, seq):
    length, version, got_kind, got_seq = WIRE.unpack(exact(sock, 16))
    assert (version, got_kind, got_seq) == (1, kind | 0x8000, seq)
    assert length <= TOTAL.size
    return exact(sock, length)


def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.settimeout(3)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def begin(sock):
    sock.sendall(packet(1, 1, bytes(32)))
    assert response(sock, 1, 1) == b""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, binary, mode="aggregate", extra=(), label="probe"):
        self.port = free_port()
        self.path = OUT / f"{label}-{binary.parent.name}-{mode}-{self.port}.log"
        self.log = self.path.open("w", encoding="utf-8")
        self.command = [str(binary), "server", "--port", str(self.port), "--mode", mode,
                        "--max-frame", "65536", "--workers", "4", "--control-stdin", "1",
                        "--retain-batches", "2", "--retain-bytes", "8192", *extra]
        self.process = subprocess.Popen(self.command, cwd=ROOT, stdin=subprocess.PIPE,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.path.read_text())
            if '"event":"ready"' in self.path.read_text():
                return
            time.sleep(.01)
        self.process.kill()
        raise AssertionError("startup timeout")

    def stop(self, timeout=8):
        if self.process.poll() is None:
            self.process.stdin.write(b"stop\n")
            self.process.stdin.flush()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise AssertionError(f"shutdown timeout: {self.path}")
        self.log.close()
        lines = self.path.read_text().splitlines()
        assert self.process.returncode == 0, lines
        result = json.loads(lines[-1])
        assert result["valid"], result
        return result


def event():
    return dict(id=0, timestamp_ns=0, source=0, kind="kind00", value_milli=0,
                flags=0, message="x")


def encoding(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def semantics(binary, mode):
    baseline = encoding([event()])
    cases = {b"[]", baseline}
    # Exercise both separators and complete input consumption, independently of simdjson.
    for index in range(len(baseline)):
        cases.add(baseline[:index] + baseline[index + 1:])
        for value in [b",", b":", b"[", b"]", b"{", b"}", b"\0", b"\v", b"\f"]:
            cases.add(baseline[:index] + value + baseline[index + 1:])
    for suffix in [b"\0", b"\v", b"\f", b" []", b" null", b",", b"[", b"]", b"{}"]:
        cases.add(baseline + suffix)
        cases.add(b"[]" + suffix)
    for field in ["id", "timestamp_ns", "source", "flags", "value_milli"]:
        marker = f'"{field}":0'.encode()
        for value in [b"-0", b"-1", b"00", b"01", b"+1", b"1.0", b"1e0", b"1E+0",
                      b"0e-0", b"18446744073709551615", b"18446744073709551616",
                      b"9223372036854775807", b"-9223372036854775808", b"-9223372036854775809",
                      b"4294967295", b"4294967296", b"1000000", b"1000001", b"-1000000",
                      b"-1000001", b"null", b"true", b'"0"', b"[0]", b"{}", b"0 " * 20]:
            cases.add(baseline.replace(marker, f'"{field}":'.encode() + value))
    for value in [b'"\\u0000"', b'"\\uD800"', b'"\\uDC00"', b'"\\uD800\\uDFFF"',
                  b'"\\uDBFF\\uDFFF"', b'"\\uDBFF\\uD800"', b'"\\uDFFF\\uDBFF"',
                  b'"\\u12G4"', b'"\\u123"', b'"\\x41"', b'"\\0"', b'"\xed\xa0\x80"',
                  b'"\xf4\x8f\xbf\xbf"', b'"\xf4\x90\x80\x80"', b'"\xc0\x80"',
                  b'"\xe0\x80\x80"', b'"\xf0\x80\x80\x80"', b'"\x80"', b'"\xff"',
                  b'"a\0b"', b'"' + b"a" * 4096 + b'"', b'"' + b"a" * 4097 + b'"',
                  b'"' + b"\\u00e9" * 2048 + b'"', b'"' + b"\\u00e9" * 2049 + b'"']:
        cases.add(baseline.replace(b'"message":"x"', b'"message":' + value))
    for field in event():
        name = f'"{field}"'.encode()
        escaped = '"' + ''.join(f"\\u{ord(c):04x}" for c in field) + '"'
        cases.add(baseline.replace(name, escaped.encode()))
        cases.add(baseline.replace(name, b'"\\uD800"'))
        cases.add(baseline.replace(name, b'"' + field.encode() + b'\\u0000"'))
    rng = random.Random(273)
    items = list(event().items())
    for _ in range(100):
        rng.shuffle(items)
        cases.add(encoding([dict(items)]))
    server = Server(binary, mode, label="semantic")
    accepted = rejected = 0
    mismatches = []
    try:
        for index, payload in enumerate(sorted(cases)):
            try:
                expected = corpus.process(payload)
            except (ValueError, TypeError):
                expected = None
            with connect(server.port) as sock:
                begin(sock)
                sock.sendall(packet(2, 2, payload))
                try:
                    actual = ACK.unpack(response(sock, 2, 2))
                except (EOFError, ConnectionResetError, ConnectionAbortedError):
                    actual = None
                if expected is None:
                    rejected += 1
                    if actual is not None:
                        mismatches.append((index, "invalid accepted", payload.hex(), actual))
                else:
                    accepted += 1
                    wanted = (expected["records"], expected["digest"])
                    if actual != wanted:
                        mismatches.append((index, "valid mismatch", payload.hex(), actual, wanted))
                    else:
                        sock.sendall(packet(3, 3))
                        summary = TOTAL.unpack(response(sock, 3, 3))
                        digest = corpus.fnv(struct.pack(">QQQ", 2, *wanted))
                        assert summary == (1, len(payload), expected["records"], digest,
                                           *expected["counts"], *expected["sums"])
        result = dict(scenario="semantics", binary=str(binary), mode=mode, accepted=accepted,
                      rejected=rejected, mismatches=mismatches, metrics=server.stop())
    except BaseException:
        server.stop()
        raise
    print(json.dumps(result), flush=True)
    (OUT / f"semantic-{binary.parent.name}-{mode}.json").write_text(json.dumps(result, indent=2))
    assert not mismatches


def deadlines(binary):
    server = Server(binary, extra=["--frame-timeout-ms", "100", "--idle-timeout-ms", "100",
                                   "--pause-every", "1", "--pause-ms", "500"], label="deadline")
    with connect(server.port) as sock:
        begin(sock)
        started = time.monotonic()
        sock.sendall(packet(2, 2, b"[]"))
        try:
            actual = ACK.unpack(response(sock, 2, 2))
        except (EOFError, ConnectionResetError, ConnectionAbortedError):
            actual = None
        elapsed = time.monotonic() - started
    result = dict(scenario="deadline", binary=str(binary), deadline_ms=100,
                  pause_ms=500, elapsed_seconds=elapsed, ack=actual, metrics=server.stop())
    print(json.dumps(result), flush=True)
    (OUT / f"deadline-{binary.parent.name}.json").write_text(json.dumps(result, indent=2))


def churn(binary, rounds):
    rng = random.Random(65537)
    total = 0
    for round_index in range(rounds):
        server = Server(binary, "retain-reuse", extra=["--io-cap", "1", "--max-connections", "32",
                        "--frame-timeout-ms", "100", "--idle-timeout-ms", "30"], label="churn")
        sockets = []
        for index in range(24):
            sock = connect(server.port)
            sockets.append(sock)
            # Mix immediate headers, partial headers/bodies and resets before cancellation.
            request = packet(1, 1, bytes(32)) + packet(2, 2, encoding([event()]))
            offset = rng.randrange(len(request) + 1)
            sock.sendall(request[:offset])
            if index % 3 == 0:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
                sock.close()
            elif index % 3 == 1:
                sock.shutdown(socket.SHUT_WR)
        result = server.stop()
        for sock in sockets:
            sock.close()
        total += result["accepted_connections"]
    print(json.dumps(dict(scenario="churn", binary=str(binary), rounds=rounds,
                          accepted_connections=total, status="passed")), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["semantics", "deadlines", "churn"])
    parser.add_argument("--binary", type=Path, default=ROOT / "src/cpp/build/Release/tcpbench.exe")
    parser.add_argument("--mode", default="aggregate")
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.scenario == "semantics":
        semantics(args.binary.resolve(), args.mode)
    elif args.scenario == "deadlines":
        deadlines(args.binary.resolve())
    else:
        churn(args.binary.resolve(), args.rounds)


if __name__ == "__main__":
    main()
