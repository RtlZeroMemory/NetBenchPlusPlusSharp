"""Post-fix C# checks; bounded peers, isolated output, no benchmark rebuild."""
from __future__ import annotations

import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import reproduce as original

ROOT, HERE = original.ROOT, original.HERE
OUT = HERE / "binary-postfix"
DLL = original.CS
EXPECTED_HASH = "60899c2ca28565d3a85b4b7609a1eefd0f57a49684929c897c1eefe69b3ba8c7"


def invoke(name, args):
    output = OUT / f"{name}.json"
    started = time.perf_counter()
    completed = subprocess.run(["dotnet", str(DLL)] + args + ["--output", str(output)],
                               cwd=ROOT, capture_output=True, text=True, timeout=15)
    (OUT / f"{name}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode == 0:
        result = json.loads(output.read_text(encoding="utf-8-sig"))
    else:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    return {"name": name, "exit_code": completed.returncode, "result": result,
            "wall_seconds": time.perf_counter() - started}


def metadata_checks():
    good = OUT / "two-buckets.bin"
    original.corpus.write_frames(good, [
        b'[{"id":1,"timestamp_ns":2,"source":3,"kind":"kind00","value_milli":123,"flags":0,"message":""}]',
        b'[{"id":2,"timestamp_ns":3,"source":4,"kind":"kind15","value_milli":-123,"flags":3,"message":"x"}]'])
    moved = OUT / "bad-count.bin"
    raw = bytearray(good.read_bytes())
    # Move the first frame's count and sum to a different legal bucket, preserving total records/digest.
    struct.pack_into(">Q", raw, 16 + 20, 0)
    struct.pack_into(">Q", raw, 16 + 20 + 8, 1)
    struct.pack_into(">q", raw, 16 + 532, 0)
    struct.pack_into(">q", raw, 16 + 532 + 8, 123)
    moved.write_bytes(raw)
    outcomes = []
    for mode in ["aggregate", "retain-reuse", "retain-allocate"]:
        for path, fault in [(HERE / "bad-sum.bin", "bad-sum"), (moved, "bad-count")]:
            for warmup in [0, .2]:
                run = invoke(f"{fault}-{mode}-warmup-{warmup}", ["process", "--corpus", str(path),
                    "--mode", mode, "--warmup", str(warmup), "--duration", ".03"])
                assert run["exit_code"] != 0 and run["result"]["valid"] is False and run["result"]["error"] == "process_oracle", run
                outcomes.append({"name": run["name"], "exit_code": run["exit_code"], "error": run["result"]["error"]})
    for mode in ["aggregate", "retain-reuse", "retain-allocate", "transport"]:
        for warmup in [0, .03]:
            run = invoke(f"valid-{mode}-warmup-{warmup}", ["process", "--corpus", str(good),
                "--mode", mode, "--warmup", str(warmup), "--duration", ".03"])
            data = run["result"]
            assert run["exit_code"] == 0 and data["valid"] is True and data["completed_frames"] > 0, run
            expected = 0 if mode == "transport" else data["completed_frames"]
            assert data["completed_records"] == expected, run
            outcomes.append({"name": run["name"], "exit_code": run["exit_code"],
                             "completed_frames": data["completed_frames"], "completed_records": data["completed_records"]})
    return outcomes


class TimedPeer(original.Peer):
    def serve(self, sock):
        batches = size = 0
        digest = original.corpus.OFFSET
        try:
            while not self.stop.is_set():
                length, version, kind, seq = original.WIRE.unpack(original.exact(sock, 16))
                original.exact(sock, length)
                if kind == 1:
                    batches = size = 0
                    digest = original.corpus.OFFSET
                    reply = b""
                elif kind == 2:
                    self.received.append(time.perf_counter())
                    if self.fault == "withhold" or self.fault == "partial-withhold" and batches == 1:
                        self.stop.wait(10)
                        return
                    batches += 1
                    size += length
                    digest = original.corpus.fnv(struct.pack(">QQQ", seq, 0, length), digest)
                    reply = struct.pack(">QQ", 0, length)
                elif kind == 3:
                    if self.fault == "delayed-end":
                        time.sleep(.25)
                    reply = original.TOTAL.pack(batches, size, 0, digest, *([0] * 128))
                else:
                    return
                sock.sendall(original.WIRE.pack(len(reply), 1, kind | 0x8000, seq) + reply)
        except (OSError, EOFError):
            pass
        finally:
            sock.close()


def timing_checks():
    path = OUT / "empty.bin"
    original.corpus.write_frames(path, [b"[]"])
    outcomes = []
    for fault in ["withhold", "partial-withhold", "delayed-end"]:
        peer = TimedPeer(fault=fault)
        try:
            run = invoke(fault, ["client", "--port", str(peer.port), "--corpus", str(path),
                "--mode", "transport", "--connections", "1", "--warmup", "0",
                "--duration", ".1", "--rate", "100", "--window", "8", "--drain-seconds", ".5"])
            returned = time.perf_counter()
            # Failure clients write normal client output before returning nonzero, in addition to stdout.
            output = OUT / f"{fault}.json"
            data = json.loads(output.read_text(encoding="utf-8-sig"))
            assert data["configured_drain_seconds"] == data["drain_limit_seconds"] == .5, data
            assert data["offered"] == data["admitted"] + data["rejected"] == 10, data
            assert abs(data["cohort_seconds"] - (.1 + data["drain_seconds"])) < 1e-6, data
            assert data["scheduled_latency"]["count"] == data["acknowledged"], data
            assert len(data["size_latency"]) == 5 and data["size_latency"][0]["count"] == data["acknowledged"], data
            assert all(hist["count"] == 0 for hist in data["size_latency"][1:]), data
            if fault != "delayed-end":
                assert run["exit_code"] != 0 and data["valid"] is False and data["generator_adequate"] is False, data
                assert .45 <= data["drain_seconds"] < .8 and .55 <= data["cohort_seconds"] < .9, data
                assert data["timedout"] == data["unresolved"] > 0, data
                assert data["acknowledged"] == (1 if fault == "partial-withhold" else 0), data
            else:
                assert run["exit_code"] == 0 and data["valid"] is True, data
                assert data["cohort_seconds"] < .15 and data["drain_seconds"] < .05, data
                assert returned - peer.received[0] > .3, "End delay was not applied"
            outcomes.append({"name": fault, "exit_code": run["exit_code"],
                "observed_after_window_seconds": returned - peer.received[0] - .1,
                **{k: data[k] for k in ["valid", "generator_adequate", "offered", "admitted", "acknowledged", "timedout", "unresolved", "cohort_seconds", "drain_seconds", "drain_limit_seconds"]}})
        finally:
            peer.close()
    return outcomes


def size_class_checks():
    path = OUT / "size-boundaries.bin"
    lengths = [4096, 4097, 65536, 65537, 262144, 262145, 1048576, 1048577]
    # Whitespace padding is only a diagnostic to hit exact byte boundaries, never a performance corpus.
    original.corpus.write_frames(path, [b"[" + b" " * (length - 2) + b"]" for length in lengths])
    peer = TimedPeer()
    try:
        run = invoke("size-boundaries", ["client", "--port", str(peer.port), "--corpus", str(path),
            "--mode", "transport", "--connections", "1", "--warmup", "0", "--duration", ".4",
            "--rate", "20", "--window", "8", "--drain-seconds", "1"])
        data = run["result"]
        assert run["exit_code"] == 0 and data["valid"] is True and data["acknowledged"] == 8, run
        counts = [hist["count"] for hist in data["size_latency"]]
        assert counts == [1, 2, 2, 2, 1], data["size_latency"]
        assert data["size_class_upper_payload_bytes"] == [4096, 65536, 262144, 1048576, 16777216]
        assert sum(counts) == data["scheduled_latency"]["count"] == 8
        assert list(data["size_class_scheduled_latency"]) == ["0", "1", "2", "3", "4"]
        for i, histogram in enumerate(data["size_latency"]):
            assert histogram == data["size_class_scheduled_latency"][str(i)]
            assert histogram["p999_ns"] is not None, "Stage gate leaked into raw client size histograms"
        return {"payload_lengths": lengths, "size_class_counts": counts,
                "acknowledged": data["acknowledged"], "raw_p999_preserved": True}
    finally:
        peer.close()


def stage_checks():
    outcomes = []
    for mode in ["aggregate", "retain-reuse"]:
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        output = OUT / f"server-stages-{mode}.json"
        server = subprocess.Popen(["dotnet", str(DLL), "server", "--port", str(port),
            "--mode", mode, "--max-frame", "2", "--max-connections", "2",
            "--control-stdin", "1", "--run-seconds", "10", "--output", str(output)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        logs = []
        try:
            ready = server.stdout.readline()
            logs.append(ready)
            assert json.loads(ready)["event"] == "ready", ready
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                seq = 0
                for count in [2048, 1024]:
                    seq += 1
                    sock.sendall(original.WIRE.pack(32, 1, 1, seq) + bytes(32))
                    assert original.exact(sock, 16) == original.WIRE.pack(0, 1, 0x8001, seq)
                    digest = original.corpus.OFFSET
                    for _ in range(count):
                        seq += 1
                        sock.sendall(original.WIRE.pack(2, 1, 2, seq) + b"[]")
                        reply = original.exact(sock, 32)
                        assert reply == original.WIRE.pack(16, 1, 0x8002, seq) + struct.pack(">QQ", 0, original.corpus.OFFSET)
                        digest = original.corpus.fnv(struct.pack(">QQQ", seq, 0, original.corpus.OFFSET), digest)
                    seq += 1
                    sock.sendall(original.WIRE.pack(0, 1, 3, seq))
                    assert original.exact(sock, 16) == original.WIRE.pack(1056, 1, 0x8003, seq)
                    assert original.exact(sock, 1056) == original.TOTAL.pack(count, count * 2, 0, digest, *([0] * 128))
            server.stdin.write("stop\n")
            server.stdin.flush()
            server.stdin.close()
            assert server.wait(timeout=5) == 0
            logs.append(server.stdout.read())
            data = json.loads(output.read_text(encoding="utf-8-sig"))
            for field in ["receive_stage_ns", "processing_stage_ns", "decode_stage_ns"]:
                stage = data[field]
                assert stage["count"] == 1 and stage["p99_ns"] is None and stage["p999_ns"] is None, (field, stage)
            visit = data["retained_visit_and_eviction_stage_ns"]
            assert visit["count"] == (1 if mode == "retain-reuse" else 0), visit
            assert visit["p99_ns"] is None and visit["p999_ns"] is None
            assert not data["errors"], data["errors"]
            outcomes.append({"mode": mode, "total_frames": data["completed"],
                "final_epoch_processing_samples": data["processing_stage_ns"]["count"],
                "stage_p99": data["processing_stage_ns"]["p99_ns"],
                "stage_p999": data["processing_stage_ns"]["p999_ns"]})
        finally:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=5)
            (OUT / f"server-stages-{mode}.log").write_text("".join(logs), encoding="utf-8")
    return outcomes


def main():
    OUT.mkdir(exist_ok=True)
    digest = hashlib.sha256(DLL.read_bytes()).hexdigest()
    assert digest == EXPECTED_HASH, digest
    preserved = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                 [HERE / "evidence-all.json", HERE / "bad-sum.bin", HERE / "cs-withhold-0.1-100.json"]}
    evidence = {"binary_sha256": digest, "metadata": metadata_checks(),
                "timing": timing_checks(), "size_classes": size_class_checks(), "server_stages": stage_checks()}
    for filename, digest in preserved.items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest, "Historical evidence changed"
    evidence["historical_evidence_unchanged"] = preserved
    (OUT / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
