"""Check the same frame deadline during ordinary large-batch processing."""
import json
import socket
import subprocess
import sys
import time

from probe import DLL, OUT, ROOT, exact, frame


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "cpu-deadline"
    wait = float(sys.argv[2]) if len(sys.argv) > 2 else .3
    row = b'{"id":1,"timestamp_ns":2,"source":3,"kind":"kind00","value_milli":4,"flags":0,"message":"x"}'
    payload = b"[" + b",".join([row] * 170_000) + b"]"
    assert len(payload) <= 16 * 1024 * 1024
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    log_path = OUT / f"{name}.log"
    output = OUT / f"{name}.json"
    args = ["dotnet", str(DLL), "server", "--port", str(port),
            "--max-frame", str(16 * 1024 * 1024), "--max-connections", "1",
            "--frame-timeout-ms", "100", "--idle-timeout-ms", "100",
            "--control-stdin", "1", "--output", str(output)]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            until = time.monotonic() + 10
            while '"event":"ready"' not in log_path.read_text(encoding="utf-8"):
                assert process.poll() is None and time.monotonic() < until
                time.sleep(.01)
            with socket.create_connection(("127.0.0.1", port), timeout=3) as first:
                first.sendall(frame(1, 1, bytes(32)))
                assert exact(first, 16) == frame(0x8001, 1)
                start = time.monotonic()
                first.sendall(frame(2, 2, payload))
                sent = time.monotonic() - start
                first.settimeout(wait)
                try:
                    reply = first.recv(32)
                    first_state = "closed" if not reply else "response"
                except socket.timeout:
                    first_state = "still_open"
                observed = time.monotonic() - start
                with socket.create_connection(("127.0.0.1", port), timeout=3) as second:
                    second.sendall(frame(1, 1, bytes(32)))
                    try:
                        second_state = "accepted" if exact(second, 16) == frame(0x8001, 1) else "unexpected_response"
                    except (EOFError, ConnectionResetError):
                        second_state = "rejected"
                if first_state == "still_open":
                    first.settimeout(5)
                    assert first.recv(32) == b""
                closed = time.monotonic() - start
        finally:
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
            process.wait(timeout=5)
            process.stdin.close()
        assert process.returncode == 0
    metrics = json.loads(output.read_text(encoding="utf-8"))
    data = {"args": args, "records": 170_000, "payload_bytes": len(payload),
            "sent_seconds": sent, "first_state": first_state, "observed_seconds": observed,
            "second_connection_state": second_state, "closed_seconds": closed,
            "errors": metrics["errors"], "completed": metrics["completed"]}
    (OUT / f"{name}-evidence.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
