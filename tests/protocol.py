"""Independent real-socket conformance and cross-client checks; stdlib only."""
from __future__ import annotations

import argparse
import json
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "bench")]
import corpus  # noqa: E402
from run import BINARIES, command  # noqa: E402,F401  (one source of binary paths)

WIRE = struct.Struct(">IHHQ")
ACK = struct.Struct(">QQ")
TOTAL = struct.Struct(">QQQQ64Q64q")


def event(**changes):
    value = dict(id=42, timestamp_ns=1700000000000000000, source=123,
                 kind="kind03", value_milli=-12345, flags=7, message="თბილისი 🌡️")
    value.update(changes)
    return value


def encode(events):
    return json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode()


def fixtures():
    base = encode([event()])
    with_message = lambda raw: encode([event(message='@')]).replace(b'"@"', b'"' + raw + b'"')
    valid = [b"[]", base, encode([event(id=corpus.MASK, timestamp_ns=corpus.MASK,
                                      source=0xffffffff, flags=0xffffffff, message="")]),
             encode([event(message="a" * 4096)]),
             encode([event(message="\0\n\t\"\\😀")]),
             base.replace(b'"id"', b'"\\u0069d"'),
             base.replace(b'"timestamp_ns"', b'"' + b''.join(b'\\u%04x' % c for c in b'timestamp_ns') + b'"')
                 .replace(b'"kind03"', b'"\\u006bind03"'),
             with_message(b'\\u0041' + b'A' * 4095), with_message(b'\\u0001' * 4096),
             base.replace(b'"value_milli":-12345', b'"value_milli":-0'),
             b" \r\n\t" + base + b"\r\n\t ",
             encode([dict(reversed(list(event().items()))), event(kind="kind15", flags=0)])]
    invalid = [b"", b" ", b"{}", b"null", b"[{}]", base + b"{}", base + b"x",
               base[:-1] + b",]", b"\xef\xbb\xbf" + base, b"/*x*/" + base,
               base.replace(b'"id":42', b'"id":42,"id":1'),
               base.replace(b'"id":42', b'"id":42,"\\u0069d":1'),
               base.replace(b'"id":42', b'"id":42,"extra":1'),
               base.replace(b'"id":42,', b""),
               base.replace(b'"id":42', b'"id":-0'),
               base.replace(b'"id":42', b'"id":18446744073709551616'),
               base.replace(b'"id":42', b'"id":4.2e1'),
               base.replace(b'"id":42', b'"id":42.0'),
               base.replace(b'"id":42', b'"id":042'),
               base.replace(b'"id":42', b'"id":true'),
               base.replace(b'"value_milli":-12345', b'"value_milli":NaN'),
               encode([event(source=1 << 32)]), encode([event(flags=-1)]),
               encode([event(value_milli=1000001)]), encode([event(value_milli=-1000001)]),
               encode([event(kind="kind16")]), encode([event(kind="Kind03")]),
               encode([event(message=12)]), encode([event(message="x" * 4097)]),
               with_message(b'\\u0041A' + b'A' * 4095), with_message(b'\\u0001' * 4097),
               with_message(b'\\n' + b'A' * 30000), base.replace(b'"id":42', b'"id":42,"\\u0069' + b'd' * 20 + b'":1'),
               encode([event(message="x")]).replace(b'"message":"x"', b'"message":"\\ud800"'),
               encode([event(message="x")]).replace(b'"message":"x"', b'"message":"\\udc00"'),
               encode([event(message="x")]).replace(b'"message":"x"', b'"message":"\xff"'),
               encode([event(message="x")]).replace(b'"message":"x"', b'"message":"\xc0\x80"'),
               encode([event(message={"nested": [1]})]),
               base[:-1] + b"," + encode([event(flags=-1)])[1:]]
    return valid, invalid


def exact(sock, count):
    result = bytearray()
    while len(result) < count:
        data = sock.recv(count - len(result))
        if not data:
            raise AssertionError(f"Unexpected EOF after {len(result)}/{count} bytes")
        result.extend(data)
    return bytes(result)


def frame(kind, sequence, body=b"", version=1, length=None):
    return WIRE.pack(len(body) if length is None else length, version, kind, sequence) + body


def response(sock, kind, sequence):
    length, version, actual_kind, actual_sequence = WIRE.unpack(exact(sock, WIRE.size))
    assert (version, actual_kind, actual_sequence) == (1, kind | 0x8000, sequence), "Response header"
    assert length <= TOTAL.size, "Response size bound"
    return exact(sock, length)


def begin(sock, sequence=1):
    sock.sendall(frame(1, sequence, bytes(32)))
    assert response(sock, 1, sequence) == b""


def expect_closed(sock):
    try:
        got = sock.recv(1)
        assert not got, f"Invalid request received success/output {got.hex()}"
    except (ConnectionResetError, ConnectionAbortedError):
        pass


def connection(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=4)
    sock.settimeout(4)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def free_port():
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        return reserve.getsockname()[1]


class Summary:
    """Expected End summary of one epoch, accumulated from acknowledged batches."""

    def __init__(self):
        self.batches = self.size = self.records = 0
        self.digest, self.counts, self.sums = corpus.OFFSET, [0] * 64, [0] * 64

    def add(self, sequence, payload, expected):
        self.batches += 1
        self.size += len(payload)
        self.records += expected["records"]
        self.digest = corpus.fnv(struct.pack(">QQQ", sequence, expected["records"], expected["digest"]), self.digest)
        self.counts = [a + b for a, b in zip(self.counts, expected["counts"])]
        self.sums = [a + b for a, b in zip(self.sums, expected["sums"])]

    def pack(self, extra_batches=0):
        return TOTAL.pack(self.batches + extra_batches, self.size, self.records, self.digest, *self.counts, *self.sums)


class Server:
    """A server under test, stopped through --control-stdin; stop() returns its final JSON."""

    def __init__(self, binary, *options):
        self.port = free_port()
        self.process = subprocess.Popen(command(Path(binary)) + ["server", "--port", str(self.port),
                                                                "--control-stdin", "1", *options],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
        ready = self.process.stdout.readline()
        assert json.loads(ready)["event"] == "ready", ready

    def connect(self):
        sock = connection(self.port)
        begin(sock)
        return sock

    def stop(self, timeout=10):
        out, err = self.process.communicate("stop\n", timeout=timeout)
        assert self.process.returncode == 0, err
        return json.loads(out.strip().splitlines()[-1])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.process.kill()
            self.process.communicate()


def run_protocol(port):
    valid, invalid = fixtures()
    checks = 0
    for payload in valid:
        with connection(port) as sock:
            begin(sock)
            request = frame(2, 2, payload)
            for byte in request:  # OS may coalesce; framing selftests must also cap completions.
                sock.sendall(bytes([byte]))
            expected = corpus.process(payload)
            assert ACK.unpack(response(sock, 2, 2)) == (expected["records"], expected["digest"])
            sock.sendall(frame(3, 3))
            summary = Summary()
            summary.add(2, payload, expected)
            assert response(sock, 3, 3) == summary.pack(), "End summary"
        checks += 1

    # Coalescing, deterministic repeated reuse/eviction, epoch reset, and half-close.
    with connection(port) as sock:
        sequence = 1
        for epoch in range(2):
            begin(sock, sequence)
            sequence += 1
            summary, requests, expected_responses = Summary(), [], []
            for i in range(24):
                payload = encode([event(id=i + epoch * 100, message="reuse" * (i + 1))])
                expected = corpus.process(payload)
                requests.append(frame(2, sequence, payload))
                expected_responses.append((sequence, expected))
                summary.add(sequence, payload, expected)
                sequence += 1
            requests.append(frame(3, sequence))
            sock.sendall(b"".join(requests))
            if epoch == 1:
                sock.shutdown(socket.SHUT_WR)
            for seq, expected in expected_responses:
                assert ACK.unpack(response(sock, 2, seq)) == (expected["records"], expected["digest"])
            assert response(sock, 3, sequence) == summary.pack()
            sequence += 1
        expect_closed(sock)
    checks += 1

    for payload in invalid:
        with connection(port) as sock:
            begin(sock)
            sock.sendall(frame(2, 2, payload))
            expect_closed(sock)
        checks += 1

    bad_headers = [frame(2, 2, length=65537), frame(2, 2, length=0xffffffff),
                   frame(2, 2, b"[]", version=2), frame(4, 2, b"[]"),
                   frame(2, 1, b"[]"), frame(3, 2, b"x"), frame(1, 2, bytes(32))]
    for request in bad_headers:
        with connection(port) as sock:
            begin(sock)
            sock.sendall(request)
            expect_closed(sock)
        checks += 1
    for request in [frame(2, 1, b"[]"), frame(3, 1), frame(1, 1, bytes(31)),
                    frame(1, 1, b"x" * 32)]:
        with connection(port) as sock:
            sock.sendall(request)
            expect_closed(sock)
        checks += 1

    for offset in [1, 4, 15, 16, 17]:
        with connection(port) as sock:
            begin(sock)
            request = frame(2, 2, b"[{}]")
            sock.sendall(request[:offset])
            sock.shutdown(socket.SHUT_WR)
            expect_closed(sock)
        checks += 1

    with connection(port) as sock:
        begin(sock)
        time.sleep(0.35)  # Between-frame idle must not trigger frame timeout.
        sock.sendall(frame(2, 2, b"[]"))
        assert ACK.unpack(response(sock, 2, 2)) == (0, corpus.OFFSET)
    checks += 1
    with connection(port) as sock:
        begin(sock)
        sock.sendall(b"\0")
        expect_closed(sock)  # Partial header progress deadline.
    checks += 1
    return checks


def oracle_checks():
    assert corpus.fnv(b"hello") == 0xa430d84680aabd0b
    valid, invalid = fixtures()
    for value in valid:
        corpus.process(value)
    for value in invalid:
        try:
            corpus.process(value)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"Oracle accepted {value!r}")
    original = corpus.process(encode([event()]))
    assert corpus.process(encode([dict(reversed(list(event().items())))])) == original
    assert corpus.process(encode([event()]).replace(b'"id"', b'"\\u0069d"')) == original
    return len(valid) + len(invalid) + 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path)
    parser.add_argument("--modes", nargs="+", default=["aggregate", "retain-reuse", "retain-allocate"])
    parser.add_argument("--export-fixtures", action="store_true", help="rewrite tests/fixtures/golden.bin")
    args = parser.parse_args()
    count = oracle_checks()
    if args.export_fixtures:
        corpus.write_frames(ROOT / "tests/fixtures/golden.bin", fixtures()[0])
    for mode in args.modes if args.server else []:
        with Server(args.server.resolve(), "--mode", mode, "--max-frame", "65536", "--workers", "2",
                    "--idle-timeout-ms", "250", "--frame-timeout-ms", "1500") as server:
            checks = run_protocol(server.port)
            assert server.stop()["valid"]
        count += checks
        print(json.dumps({"server": str(args.server), "mode": mode, "passed": checks}), flush=True)
    print(json.dumps({"total_checks": count, "status": "passed"}))


if __name__ == "__main__":
    main()
