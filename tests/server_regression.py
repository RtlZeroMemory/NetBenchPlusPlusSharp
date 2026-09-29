"""Shared server regressions: processing deadlines, pauses, shutdown, stage sampling, owned
storage peaks and quiet EOF. Every case runs against both servers with identical assertions."""
import argparse
import json
import socket
import struct
import time
from pathlib import Path

from protocol import Server, corpus, exact, frame
from protocol import expect_closed as closed


def processing_deadline_and_independent_progress(binary):
    with Server(binary, "--frame-timeout-ms", "100", "--idle-timeout-ms", "100", "--pause-every", "1",
                "--pause-ms", "500", "--workers", "4") as server:
        with server.connect() as busy:
            busy.sendall(frame(2, 2, b"[]"))
            time.sleep(.02)
            before = time.monotonic()
            with server.connect() as independent:
                assert time.monotonic() - before < .3, "processing blocked an unrelated accept/Begin"
                independent.sendall(b"\0")
                before = time.monotonic()
                closed(independent)
                assert time.monotonic() - before < .3, "processing blocked another frame's timeout"
            closed(busy)
        metrics = server.stop()
    assert metrics["lifetime"]["frames"] == 0, metrics["lifetime"]
    assert metrics["connections"]["closed"].get("frame_timeout", 0) == 2, metrics["connections"]


def stage_sampling_offset_reset_and_gates(binary):
    with Server(binary, "--pause-every", "1024", "--pause-ms", "50", "--mode", "retain-reuse") as server:
        with server.connect() as sock:
            seq = 1
            for epoch in range(2):
                if epoch:
                    seq += 1
                    sock.sendall(frame(1, seq, bytes(32)))
                    assert exact(sock, 16) == frame(0x8001, seq)
                for _ in range(1024):
                    seq += 1
                    sock.sendall(frame(2, seq, b"[]"))
                    exact(sock, 16)
                    assert struct.unpack(">QQ", exact(sock, 16)) == (0, corpus.OFFSET)
                seq += 1
                sock.sendall(frame(3, seq))
                exact(sock, 16)
                assert struct.unpack_from(">Q", exact(sock, 1056))[0] == 1024
        metrics = server.stop()
    stages = metrics["stages"]
    assert stages["sample_every"] == 1024
    for key in ("receive", "processing", "decode", "retained_visit"):
        assert stages[key]["count"] == 1, (key, stages[key]["count"])  # frame 1024 of the last epoch
        assert stages[key]["p99_ns"] is None and stages[key]["p999_ns"] is None
    assert stages["processing"]["max_ns"] >= 40_000_000, "injected pause omitted from processing"
    assert metrics["measured"]["complete"] and metrics["measured"]["frames"] == 1024, metrics["measured"]


def cpu_budget(binary):
    # A 20 ms idle budget expires inside CPU-bound parsing (~50+ ms for 170,000 records), or
    # earlier during the transfer: Windows loopback shows >20 ms stalls mid-transfer even while
    # the server reads continuously. Either way nothing commits and the slot is released
    # promptly; the selftests cover mid-parse expiry deterministically.
    row = b'{"id":1,"timestamp_ns":1,"source":1,"kind":"kind00","value_milli":1,"flags":1,"message":"x"}'
    payload = b"[" + b",".join([row] * 170_000) + b"]"
    for mode in ("aggregate", "retain-reuse", "retain-allocate"):
        with Server(binary, "--mode", mode, "--max-frame", "16777216", "--max-connections", "1", "--workers", "1",
                    "--socket-buffer", "16777216", "--frame-timeout-ms", "10000", "--idle-timeout-ms", "20") as server:
            with server.connect() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16777216)
                sock.sendall(frame(2, 2, payload))
                before = time.monotonic()
                closed(sock)
                assert time.monotonic() - before < 1, "processing held the slot after its budget"
            metrics = server.stop()
        assert metrics["lifetime"]["frames"] == 0, metrics["lifetime"]
        assert metrics["connections"]["closed"] == {"idle_timeout": 1}, metrics["connections"]


def expiry_releases_slot(binary):
    cases = [("partial", "aggregate", "100", "frame_timeout"), ("pause", "retain-allocate", "100", "frame_timeout"),
             ("idle", "transport", "1000", "idle_timeout")]
    for kind, mode, frame_ms, reason in cases:
        options = ["--mode", mode, "--max-connections", "1", "--frame-timeout-ms", frame_ms, "--idle-timeout-ms", "100"]
        if kind != "partial":
            options += ["--pause-every", "1", "--pause-ms", "900"]
        with Server(binary, *options) as server:
            with server.connect() as busy:
                request = frame(2, 2, b"[]")
                before = time.monotonic()
                busy.sendall(request[:-1] if kind == "partial" else request)
                busy.settimeout(.6)
                closed(busy)
                assert time.monotonic() - before < .6, kind
            time.sleep(.05)
            with server.connect():  # the sole slot was released, not just the ACK suppressed
                pass
            metrics = server.stop()
        assert metrics["lifetime"]["frames"] == 0 and metrics["connections"]["rejected"] == 0, metrics
        assert metrics["connections"]["closed"].get(reason) == 1, (kind, metrics["connections"])


def shutdown_during_pause(binary):
    with Server(binary, "--pause-every", "1", "--pause-ms", "10000") as server:
        with server.connect() as busy:
            busy.sendall(frame(2, 2, b"[]"))
            time.sleep(.05)
            before = time.monotonic()
            metrics = server.stop(timeout=2)
            assert time.monotonic() - before < .6, "pause ignored shutdown"
            closed(busy)
    assert metrics["lifetime"]["frames"] == 0
    assert metrics["measured"] == {**metrics["measured"], "complete": False}


def owned_peak_and_quiet_eof(binary):
    row = {"id": 1, "timestamp_ns": 2, "source": 3, "kind": "kind00", "value_milli": 4, "flags": 0, "message": "x" * 1000}
    payload = json.dumps([row], separators=(",", ":")).encode()
    peaks = {}
    for mode in ("retain-reuse", "retain-allocate"):
        with Server(binary, "--mode", mode, "--retain-batches", "1", "--frame-timeout-ms", "100",
                    "--idle-timeout-ms", "100") as server:
            with server.connect() as sock:
                for sequence in (2, 3):
                    sock.sendall(frame(2, sequence, payload))
                    exact(sock, 16)
                    assert struct.unpack(">QQ", exact(sock, 16))[0] == 1
                time.sleep(.15)  # EOF starts no frame and must not reuse the old frame deadline
                sock.shutdown(socket.SHUT_WR)
                closed(sock)
            metrics = server.stop()
        # A clean EOF inside an epoch (no End) is distinct from a graceful close.
        assert metrics["lifetime"]["frames"] == 2 and metrics["connections"]["closed"] == {"eof_in_epoch": 1}, metrics
        assert metrics["measured"]["complete"] is False, metrics["measured"]
        assert metrics["retention"]["canonical_peak_per_connection"] == 1038
        peaks[mode] = metrics["retention"]["owned_capacity_peak_per_connection"]
    # Identical growth policy: 16 rows x 48 bytes + 1000 text bytes, scratch plus one retained.
    assert peaks == {"retain-reuse": 3536, "retain-allocate": 3536}, peaks


CASES = [processing_deadline_and_independent_progress, stage_sampling_offset_reset_and_gates, cpu_budget,
         expiry_releases_slot, shutdown_during_pause, owned_peak_and_quiet_eof]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    args = parser.parse_args()
    binary = args.server.resolve()
    for case in CASES:
        case(binary)
        print(json.dumps({"server": binary.name, "case": case.__name__, "status": "passed"}), flush=True)


if __name__ == "__main__":
    main()
