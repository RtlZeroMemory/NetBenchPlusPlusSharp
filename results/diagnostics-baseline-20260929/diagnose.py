"""Controlled diagnostics against the frozen first-pass binaries (not headline evidence).

Each case starts fresh processes under the first-pass CPU masks and changes one variable.
Usage: python diagnose.py <group> [<group> ...]   groups: large, clientcpu
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import run  # noqa: E402  (first-pass runner helpers: cpu_masks, spawn, wait_ready)

BASE = ROOT / "results/audit-baseline-20260929/bin"
EXE = {"cpp": [str(BASE / "cpp/tcpbench.exe")], "csharp": ["dotnet", str(BASE / "dotnet/Bench.dll")]}
LARGE = str(ROOT / "data/corpus-1048576-268435456-42.bin")
SMALL = str(ROOT / "data/corpus-65536-268435456-42.bin")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def case(name, server, client, mode, corpus, connections, window, max_frame, sockbuf=262144,
         duration=10, warmup=2, rate=0, server_extra=(), client_mask=None):
    folder = HERE / name
    folder.mkdir(exist_ok=False)
    masks = run.cpu_masks(4)
    port = free_port()
    s_args = EXE[server] + ["server", "--port", str(port), "--mode", mode, "--max-frame", str(max_frame),
                            "--max-connections", str(connections + 1), "--workers", "4",
                            "--socket-buffer", str(sockbuf), "--control-stdin", "1",
                            "--output", str(folder / "server.json"), *server_extra]
    c_args = EXE[client] + ["client", "--port", str(port), "--corpus", corpus, "--mode", mode,
                            "--connections", str(connections), "--duration", str(duration),
                            "--warmup", str(warmup), "--window", str(window), "--rate", str(rate),
                            "--socket-buffer", str(sockbuf), "--output", str(folder / "client.json")]
    (folder / "commands.json").write_text(json.dumps({"server": s_args, "client": c_args}, indent=2))
    with (folder / "server.log").open("w") as slog, (folder / "client.log").open("w") as clog:
        srv = run.spawn(s_args, masks["server"], slog)
        try:
            run.wait_ready(srv, port)
            cli = run.spawn(c_args, client_mask or masks["client"], clog)
            cli.wait(timeout=duration + 2 * warmup + 120)
        finally:
            try:
                srv.stdin.write(b"stop\n")
                srv.stdin.close()
                srv.wait(timeout=30)
            except Exception:
                srv.kill()
    c = json.loads((folder / "client.json").read_text(encoding="utf-8-sig"))
    s = json.loads((folder / "server.json").read_text(encoding="utf-8-sig"))
    lat = c["scheduled_latency"]
    sub = c["submitted_latency"]
    res = s.get("resources", s)
    cres = c.get("resources", c)
    row = {"case": name, "server": server, "client": client, "mode": mode, "window": window,
           "sockbuf": sockbuf, "valid": c["valid"],
           "frames_per_s": round(c["window_completed_frames" if "window_completed_frames" in c else "window_completed"] / duration, 1),
           "payload_mib_s": round(c["window_payload_bytes"] / duration / 2**20, 1),
           "p50_ms": lat["p50_ns"] and round(lat["p50_ns"] / 1e6, 3),
           "p99_ms": lat["p99_ns"] and round(lat["p99_ns"] / 1e6, 3),
           "submitted_p50_ms": sub["p50_ns"] and round(sub["p50_ns"] / 1e6, 3),
           "samples": lat["count"], "server_cpu_s": round(res["cpu_seconds"], 2),
           "client_cpu_s": round(cres["cpu_seconds"], 2)}
    print(json.dumps(row), flush=True)
    with (HERE / "summary.jsonl").open("a") as out:
        out.write(json.dumps(row) + "\n")
    return row


def process_control(impl, corpus, mode, name):
    folder = HERE / name
    folder.mkdir(exist_ok=False)
    masks = run.cpu_masks(4)
    args = EXE[impl] + ["process", "--corpus", corpus, "--mode", mode, "--duration", "5", "--warmup", "1",
                        "--output", str(folder / "process.json")]
    with (folder / "process.log").open("w") as log:
        p = run.spawn(args, masks["server"] & -masks["server"], log)  # one server core
        p.wait(timeout=300)
    r = json.loads((folder / "process.json").read_text(encoding="utf-8-sig"))
    row = {"case": name, "impl": impl, "mode": mode, "frames_per_s": round(r["frames_per_second"], 1),
           "records_per_s": round(r["records_per_second"]), "ms_per_frame": round(1000 / r["frames_per_second"], 3)}
    print(json.dumps(row), flush=True)
    with (HERE / "summary.jsonl").open("a") as out:
        out.write(json.dumps(row) + "\n")


def large():
    for impl in ["cpp", "csharp"]:
        process_control(impl, LARGE, "aggregate", f"L0-process-{impl}")
    for server in ["cpp", "csharp"]:
        case(f"L1-w64-{server}", server, "cpp", "aggregate", LARGE, 1, 64, 1048576)
        case(f"L2-w1-{server}", server, "cpp", "aggregate", LARGE, 1, 1, 1048576)
        case(f"L3-w2-{server}", server, "cpp", "aggregate", LARGE, 1, 2, 1048576)
        case(f"L4-w64-buf4m-{server}", server, "cpp", "aggregate", LARGE, 1, 64, 1048576, sockbuf=4194304)
        case(f"L5-transport-w64-{server}", server, "cpp", "transport", LARGE, 1, 64, 1048576)
        case(f"L6-transport-w1-{server}", server, "cpp", "transport", LARGE, 1, 1, 1048576)
        case(f"L7-w64-csclient-{server}", server, "csharp", "aggregate", LARGE, 1, 64, 1048576)


def pause():
    # Transport mode (no JSON) with an injected per-frame server pause: isolates receiver stalls.
    for server in ["cpp", "csharp"]:
        stall = ("--pause-every", "1", "--pause-ms", "2")
        case(f"P1-transport-w1-pause2-{server}", server, "cpp", "transport", LARGE, 1, 1, 1048576, server_extra=stall)
        case(f"P2-transport-w64-pause2-{server}", server, "cpp", "transport", LARGE, 1, 64, 1048576, server_extra=stall)
        case(f"P3-transport-w2-pause2-{server}", server, "cpp", "transport", LARGE, 1, 2, 1048576, server_extra=stall)
        case(f"P4-transport-w64-pause2-buf4m-{server}", server, "cpp", "transport", LARGE, 1, 64, 1048576,
             sockbuf=4194304, server_extra=stall)


def clientcpu():
    masks = run.cpu_masks(4)
    four = masks["client"] | masks["spare"]
    for server in ["cpp", "csharp"]:
        case(f"C1-agg64k-3core-{server}", server, "cpp", "aggregate", SMALL, 16, 64, 65536)
        case(f"C2-agg64k-4core-{server}", server, "cpp", "aggregate", SMALL, 16, 64, 65536, client_mask=four)
        case(f"C3-transport64k-3core-{server}", server, "cpp", "transport", SMALL, 16, 64, 65536)
        case(f"C4-transport64k-4core-{server}", server, "cpp", "transport", SMALL, 16, 64, 65536, client_mask=four)


if __name__ == "__main__":
    for group in sys.argv[1:]:
        globals()[group]()
