"""Diagnostic: C# server fairness mechanism variants under EASY closed-loop saturation.

always  = await Task.Yield() after every response (current source)
pending = yield only when ThreadPool.PendingWorkItemCount > 0 (scratch variant)
Fresh processes, same masks and C++ client as the runner; alternating order; 20 s + 10 s warmup.
"""
import json
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import run  # noqa: E402

VARIANTS = {"always": ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll", "pending": Path(sys.argv[1])}
CORPUS = str(next((ROOT / "data").glob("easy64k-easy-536870912-65536-101.bin")))
masks = run.cpu_layout(4)
folder = HERE / "yield-variants"
folder.mkdir(exist_ok=True)
for rep in range(2):
    for name in (["always", "pending"] if rep % 2 == 0 else ["pending", "always"]):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        out = folder / f"{rep}-{name}"
        out.mkdir(exist_ok=True)
        with (out / "server.log").open("w") as slog, (out / "client.log").open("w") as clog:
            srv = run.spawn(["dotnet", str(VARIANTS[name]), "server", "--port", str(port), "--max-frame", "65536",
                             "--max-connections", "16", "--socket-buffer", "4194304", "--control-stdin", "1",
                             "--output", str(out / "server.json")], masks["server"], slog)
            run.wait_ready(srv, out / "server.log")
            cli = run.spawn([str(run.BINARIES["cpp"]), "client", "--port", str(port), "--corpus", CORPUS,
                             "--connections", "16", "--window", "64", "--duration", "20", "--warmup", "10",
                             "--socket-buffer", "4194304", "--output", str(out / "client.json")],
                            masks["client"], clog, stdin=None)
            cli.wait()
            srv.stdin.write(b"stop\n")
            srv.stdin.close()
            srv.wait()
        c = json.loads((out / "client.json").read_text(encoding="utf-8-sig"))
        s = json.loads((out / "server.json").read_text(encoding="utf-8-sig"))
        h, m = c["latency"]["scheduled"], s["measured"]
        print(json.dumps({"variant": name, "rep": rep, "Mrec_s": round(c["window"]["records"] / c["window"]["seconds"] / 1e6, 3),
                          "p50_ms": h["p50_ns"] / 1e6, "p99_ms": h["p99_ns"] / 1e6, "p999_ms": h["p999_ns"] / 1e6,
                          "max_ms": h["max_ns"] / 1e6, "server_cores": round(m["cpu_seconds"] / m["seconds"], 2)}), flush=True)
