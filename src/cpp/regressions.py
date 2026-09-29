"""Native deadline, retained-capacity, and sampled-stage regression checks."""
import argparse
import json
import pathlib
import socket
import struct
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADER = struct.Struct(">IHHQ")


def packet(kind, sequence, payload=b""):
    return HEADER.pack(len(payload), 1, kind, sequence) + payload


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        assert part, "unexpected EOF"
        data.extend(part)
    return bytes(data)


def reply(sock, kind, sequence):
    length, version, actual_kind, actual_sequence = HEADER.unpack(exact(sock, 16))
    assert (version, actual_kind, actual_sequence) == (1, kind | 0x8000, sequence)
    assert length == {1: 0, 2: 16, 3: 1056}[kind]
    return exact(sock, length)


def connect(number):
    sock = socket.create_connection(("127.0.0.1", number), timeout=3)
    sock.sendall(packet(1, 1, bytes(32)))
    reply(sock, 1, 1)
    return sock


def start(exe, *options):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        number = sock.getsockname()[1]
    proc = subprocess.Popen([exe, "server", "--port", str(number), "--control-stdin", "1", *options],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=ROOT)
    assert json.loads(proc.stdout.readline())["event"] == "ready"
    return proc, number


def stop(proc):
    out, err = proc.communicate("stop\n", timeout=5)
    assert proc.returncode == 0, err
    return json.loads(out)


def closed(sock):
    try:
        assert sock.recv(1) == b"", "expired frame received a successful ACK"
    except (ConnectionResetError, ConnectionAbortedError):
        pass


def deadline(exe):
    proc, number = start(exe, "--frame-timeout-ms", "100", "--idle-timeout-ms", "100",
                         "--pause-every", "1", "--pause-ms", "500", "--workers", "4")
    try:
        with connect(number) as busy:
            busy.sendall(packet(2, 2, b"[]"))
            time.sleep(.02)
            before = time.monotonic()
            with connect(number) as independent:
                assert time.monotonic() - before < .3, "processing blocked unrelated accept/Begin"
                independent.sendall(b"\0")
                before = time.monotonic()
                closed(independent)
                assert time.monotonic() - before < .3, "processing blocked another frame's timeout"
            closed(busy)
        metrics = stop(proc)
        assert metrics["completed_frames"] == 0
        assert metrics["rejection_reasons"].get("frame_timeout", 0) >= 2
        print(json.dumps({"regression": "processing-deadline-and-independent-progress", "passed": True}))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def stages(exe):
    proc, number = start(exe, "--pause-every", "1024", "--pause-ms", "50", "--mode", "retain-reuse")
    try:
        with connect(number) as sock:
            seq = 1
            for epoch in range(2):
                if epoch:
                    seq += 1
                    sock.sendall(packet(1, seq, bytes(32)))
                    reply(sock, 1, seq)
                for _ in range(1024):
                    seq += 1
                    sock.sendall(packet(2, seq, b"[]"))
                    assert struct.unpack(">QQ", reply(sock, 2, seq)) == (0, 14695981039346656037)
                seq += 1
                sock.sendall(packet(3, seq))
                assert struct.unpack_from(">Q", reply(sock, 3, seq))[0] == 1024
        metrics = stop(proc)
        for key in ("receive_elapsed_ns", "processing_elapsed_ns", "decode_materialize_ns", "retained_visit_ns"):
            assert metrics[key]["count"] == 1, "sample offset/reset differs from shared contract"
            assert metrics[key]["p99_ns"] is None and metrics[key]["p999_ns"] is None
        assert metrics["processing_elapsed_ns"]["max_ns"] >= 40_000_000, "injected gate omitted from processing"
        print(json.dumps({"regression": "stage-pause-offset-reset-and-tail-gates", "passed": True}))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def processing_budget(exe):
    row = (b'{"id":1,"timestamp_ns":1,"source":1,"kind":"kind00",'
           b'"value_milli":1,"flags":1,"message":""}')
    payload = b"[" + b",".join([row] * 170_000) + b"]"
    for mode in ("aggregate", "retain-reuse", "retain-allocate"):
        proc, number = start(exe, "--mode", mode, "--max-frame", "16777216",
                             "--max-connections", "1", "--workers", "1",
                             "--socket-buffer", "16777216", "--frame-timeout-ms", "30")
        try:
            acknowledged = False
            with connect(number) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16777216)
                before = time.monotonic()
                try:
                    sock.sendall(packet(2, 2, payload))
                    first = sock.recv(1)
                    if first:
                        header = first + exact(sock, 15)
                        assert HEADER.unpack(header) == (16, 1, 0x8002, 2)
                        assert struct.unpack(">QQ", exact(sock, 16))[0] == 170_000
                        acknowledged = True
                except (ConnectionResetError, ConnectionAbortedError):
                    pass
                elapsed = time.monotonic() - before
                assert elapsed < 1, "ordinary processing held the slot after its budget"
            metrics = stop(proc)
            # A faster host may finish within budget; expired work must never commit.
            assert metrics["completed_frames"] == int(acknowledged), metrics
            if not acknowledged:
                assert metrics["rejection_reasons"].get("frame_timeout", 0) == 1
            print(json.dumps({"regression": "normal-processing-budget", "mode": mode,
                              "acknowledged": acknowledged, "elapsed_seconds": elapsed,
                              "passed": True}))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def interruptible_pause(exe):
    for shutdown in (False, True):
        limit = "5000" if shutdown else "100"
        proc, number = start(exe, "--max-connections", "1", "--workers", "1",
                             "--frame-timeout-ms", limit, "--idle-timeout-ms", limit,
                             "--pause-every", "1", "--pause-ms", "900")
        try:
            with connect(number) as sock:
                sock.sendall(packet(2, 2, b"[]"))
                if shutdown:
                    time.sleep(.05)
                    before = time.monotonic()
                    metrics = stop(proc)
                else:
                    before = time.monotonic()
                    closed(sock)
                    assert time.monotonic() - before < .5, "pause ignored frame deadline"
                    time.sleep(.05)
                    with connect(number):
                        pass
                    metrics = stop(proc)
                assert time.monotonic() - before < .5, "pause ignored shutdown"
            assert metrics["completed_frames"] == 0
            assert metrics["excess_connections"] == 0
            print(json.dumps({"regression": "interruptible-pause", "shutdown": shutdown,
                              "passed": True}))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def capacity_and_quiet_eof(exe):
    peaks = []
    payload = json.dumps([dict(id=1, timestamp_ns=1, source=1, kind="kind00",
                               value_milli=1, flags=1, message="x" * 1000)]).encode()
    for mode in ("retain-reuse", "retain-allocate"):
        proc, number = start(exe, "--mode", mode, "--retain-batches", "1",
                             "--frame-timeout-ms", "100")
        try:
            with connect(number) as sock:
                for sequence in (2, 3):
                    sock.sendall(packet(2, sequence, payload))
                    assert struct.unpack(">QQ", reply(sock, 2, sequence))[0] == 1
                # EOF starts no new frame and must not reuse the previous frame's deadline.
                time.sleep(.15)
                sock.shutdown(socket.SHUT_WR)
                closed(sock)
            metrics = stop(proc)
            assert metrics["completed_frames"] == 2
            assert metrics["graceful_connections"] == 1
            assert metrics["peak_retained_canonical_per_connection"] == 1038
            peak = metrics["peak_owned_storage_per_connection"]
            assert peak >= 2 * 1038, "scratch-plus-retained owned capacity was missed"
            peaks.append(peak)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    assert peaks[0] == peaks[1], "identical live batches should have identical native capacity"
    print(json.dumps({"regression": "owned-peak-and-quiet-eof", "peak_bytes": peaks[0],
                      "passed": True}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default="src/cpp/build/Release/tcpbench.exe")
    args = parser.parse_args()
    exe = str((ROOT / args.exe).resolve())
    deadline(exe)
    stages(exe)
    processing_budget(exe)
    interruptible_pause(exe)
    capacity_and_quiet_eof(exe)


if __name__ == "__main__":
    main()
