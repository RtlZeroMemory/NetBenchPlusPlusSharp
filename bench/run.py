"""Run a declared benchmark matrix on Windows loopback. Python never sends timed data.

Each trial starts a fresh server and client under disjoint physical-core masks. Within a phase,
repetitions interleave the cells and randomize server order. Paced rates can be declared as a
fraction of the slower server's median closed-loop capacity measured earlier in the campaign.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import corpus  # noqa: E402
import report  # noqa: E402

BINARIES = {"cpp": ROOT / "src/cpp/build/Release/tcpbench.exe",
            "csharp": ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"}
DEFAULTS = {"duration": 30, "warmup": 10, "connections": 16, "window": 64, "rate": 0, "arrival": "steady",
            "socket_buffer": 262144, "retain_batches": 8, "retain_bytes": 67108864, "drain": 30,
            "inflight_bytes": 67108864, "client": "cpp", "servers": ["cpp", "csharp"], "client_cores": "client",
            "pause_every": 0, "pause_ms": 0, "io_cap": 0, "kind": "tcp", "server_env": {}}
kernel = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None


def command(binary: Path):
    return ["dotnet", str(binary)] if binary.suffix == ".dll" else [str(binary)]


def sha256(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cpu_layout(server_cores: int):
    """One logical CPU per physical core. Physical core 0, which services most interrupts, is left
    to Windows and the runner; the server takes the next cores and the client the rest. SMT
    siblings are never shared between roles."""
    if kernel is None:
        raise RuntimeError("This runner targets native Windows, not WSL")
    kernel.GetLogicalProcessorInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    length = wintypes.DWORD()
    kernel.GetLogicalProcessorInformation(None, ctypes.byref(length))
    buffer = ctypes.create_string_buffer(length.value)
    if not kernel.GetLogicalProcessorInformation(buffer, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())

    class Info(ctypes.Structure):
        _fields_ = [("mask", ctypes.c_size_t), ("relationship", wintypes.DWORD), ("data", ctypes.c_ulonglong * 2)]

    cores = sorted(int(Info.from_buffer_copy(buffer, o).mask) for o in range(0, length.value, ctypes.sizeof(Info))
                   if Info.from_buffer_copy(buffer, o).relationship == 0)
    if len(cores) < server_cores + 2:
        raise RuntimeError("Need a spare core, the server cores and at least one client core")
    lowest = [mask & -mask for mask in cores]
    return {"physical_core_masks": cores, "spare": lowest[0], "server": sum(lowest[1:1 + server_cores]),
            "client": sum(lowest[1 + server_cores:]), "server_cores": server_cores}


def spawn(args, mask, log, stdin=subprocess.PIPE, extra_env=None):
    """Children inherit the runner's affinity at creation, so runtimes initialize inside their
    budget; DOTNET_PROCESSOR_COUNT matches the mask."""
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    handle = kernel.GetCurrentProcess()
    previous, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(previous), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    if mask & ~system.value or not kernel.SetProcessAffinityMask(handle, mask):
        raise RuntimeError(f"Cannot apply CPU mask {mask:#x}")
    env = os.environ | {"DOTNET_PROCESSOR_COUNT": str(mask.bit_count())} | (extra_env or {})
    try:
        return subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=stdin, env=env)
    finally:
        kernel.SetProcessAffinityMask(handle, previous.value)


class ProcessorTimes(ctypes.Structure):
    _fields_ = [("idle", ctypes.c_longlong), ("kernel", ctypes.c_longlong), ("user", ctypes.c_longlong),
                ("dpc", ctypes.c_longlong), ("interrupt", ctypes.c_longlong), ("interrupts", ctypes.c_ulong)]


def cpu_busy_seconds():
    """Busy seconds per logical CPU (kernel time includes idle time)."""
    data = (ProcessorTimes * os.cpu_count())()
    status = ctypes.WinDLL("ntdll").NtQuerySystemInformation(8, ctypes.byref(data), ctypes.sizeof(data), None)
    if status:
        raise OSError(f"NtQuerySystemInformation failed: {status:#x}")
    return [(t.kernel + t.user - t.idle) / 1e7 for t in data]


def sibling_cpus(masks, role):
    """Logical CPUs that share a physical core with the role's CPUs but are not in its mask."""
    return [cpu for cpu in range(os.cpu_count()) for core in masks["physical_core_masks"]
            if core & masks[role] and core >> cpu & 1 and not masks[role] >> cpu & 1]


def process_cpu_seconds(process):
    """Lifetime user+kernel CPU of an exited child, from its still-open process handle."""
    times = [wintypes.FILETIME() for _ in range(4)]
    handle = wintypes.HANDLE(int(process._handle))
    if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        return None
    return sum(((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7 for t in times[2:])


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        return {"valid": False, "error": f"missing or invalid {path.name}: {error}"}


def wait_ready(process, log: Path, timeout=60):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"server exited {process.returncode} before readiness")
        if '"ready"' in log.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.05)
    raise TimeoutError("server readiness timeout")


def trial(folder: Path, ordinal: int, cell: dict, server: str, masks: dict, corpus_info: dict):
    """One fresh server/client (or processing-only) trial. Failures are recorded, never dropped."""
    destination = folder / f"{ordinal:04d}-{cell['name']}-{server}"
    if destination.exists():  # left by an interrupted run: keep it as evidence
        destination.rename(destination.with_name(f"{destination.name}-interrupted-{time.time_ns()}"))
    destination.mkdir()
    builds = {name: sha256(path) for name, path in BINARIES.items()}
    manifest = {"cell": cell, "server": server, "masks": masks, "builds": builds, "corpus_sha256": corpus_info["sha256"]}
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    client_mask = masks["client"] | (masks["spare"] if cell["client_cores"] == "client+spare" else 0)
    error, started, busy_before, own_cpu = None, time.monotonic(), cpu_busy_seconds(), 0.0
    if cell["kind"] == "process":
        args = command(BINARIES[server]) + ["process", "--corpus", corpus_info["path"], "--mode", cell["mode"],
                                            "--duration", str(cell["duration"]), "--warmup", str(cell["warmup"]),
                                            "--retain-batches", str(cell["retain_batches"]),
                                            "--retain-bytes", str(cell["retain_bytes"]),
                                            "--output", str(destination / "server.json")]
        (destination / "commands.json").write_text(json.dumps({"process": args}, indent=2), encoding="utf-8")
        # Single-threaded work under the server's CPU mask, so each runtime keeps its server
        # configuration (with one CPU, .NET would fall back to workstation GC).
        with (destination / "server.log").open("w", encoding="utf-8") as log:
            process = spawn(args, masks["server"], log, stdin=subprocess.DEVNULL, extra_env=cell["server_env"])
            if process.wait(timeout=cell["duration"] * 10 + cell["warmup"] * 10 + 300):
                error = f"process control exited {process.returncode}"
            own_cpu += process_cpu_seconds(process) or 0
        outputs = {"client": None, "server": read_json(destination / "server.json")}
    else:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        server_args = command(BINARIES[server]) + [
            "server", "--port", str(port), "--mode", cell["mode"], "--max-frame", str(corpus_info["max_frame_bytes"]),
            "--max-connections", str(cell["connections"]), "--workers", str(masks["server_cores"]),
            "--retain-batches", str(cell["retain_batches"]), "--retain-bytes", str(cell["retain_bytes"]),
            "--socket-buffer", str(cell["socket_buffer"]), "--pause-every", str(cell["pause_every"]),
            "--pause-ms", str(cell["pause_ms"]), "--io-cap", str(cell["io_cap"]),
            "--manifest-hash", manifest_hash, "--control-stdin", "1",
            "--output", str(destination / "server.json")]
        client_args = command(BINARIES[cell["client"]]) + [
            "client", "--port", str(port), "--corpus", corpus_info["path"], "--mode", cell["mode"],
            "--connections", str(cell["connections"]), "--duration", str(cell["duration"]),
            "--warmup", str(cell["warmup"]), "--window", str(cell["window"]), "--rate", str(cell["rate"]),
            "--arrival", cell["arrival"], "--seed", str(cell["seed"]), "--socket-buffer", str(cell["socket_buffer"]),
            "--inflight-bytes", str(cell["inflight_bytes"]), "--drain-seconds", str(cell["drain"]),
            "--io-cap", str(cell["io_cap"]), "--manifest-hash", manifest_hash,
            "--output", str(destination / "client.json")]
        (destination / "commands.json").write_text(json.dumps({"server": server_args, "client": client_args, "server_env": cell["server_env"]}, indent=2),
                                                   encoding="utf-8")
        server_log = destination / "server.log"
        with server_log.open("w", encoding="utf-8") as slog:
            process = spawn(server_args, masks["server"], slog, extra_env=cell["server_env"])
            client = None
            try:
                wait_ready(process, server_log)
                with (destination / "client.log").open("w", encoding="utf-8") as clog:
                    client = spawn(client_args, client_mask, clog, stdin=subprocess.DEVNULL)
                    code = client.wait(timeout=2 * cell["warmup"] + cell["duration"] + 2 * cell["drain"] + 120)
                if code:
                    error = f"client exited {code}"
            except Exception as failure:  # recorded with the trial, never silently dropped
                error = str(failure)
            finally:
                if client is not None and client.poll() is None:
                    client.kill()
                    client.wait()
                own_cpu += process_cpu_seconds(client) or 0 if client is not None else 0
                try:
                    process.stdin.write(b"stop\n")
                    process.stdin.close()
                    process.wait(timeout=60)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
                    error = error or "server did not stop cleanly"
                if process.returncode:
                    error = error or f"server exited {process.returncode}"
                own_cpu += process_cpu_seconds(process) or 0
        outputs = {"client": read_json(destination / "client.json"), "server": read_json(destination / "server.json")}
    elapsed = time.monotonic() - started
    busy = [after - before for after, before in zip(cpu_busy_seconds(), busy_before)]
    background = max(0.0, sum(busy) - own_cpu)
    # Foreign work on the idle SMT siblings of the server's cores shares those cores.
    siblings = sibling_cpus(masks, "server")
    sibling_busy = sum(busy[cpu] for cpu in siblings) / max(1e-9, time.monotonic() - started) / max(1, len(siblings))
    for role, output in outputs.items():
        if output and output.get("valid") is False:
            error = error or f"invalid {role} result: {output.get('error')}"
    summary = {"ordinal": ordinal, "path": str(destination), "cell": cell, "server": server,
               "client": cell["client"] if cell["kind"] == "tcp" else None, "builds": builds, "masks": masks,
               "corpus": corpus_info, "elapsed_seconds": elapsed, "background_cpu_seconds": background,
               "server_sibling_busy_fraction": sibling_busy,
               "logical_cpus": os.cpu_count(), "runner_error": error,
               "result": outputs["client"], "server_result": outputs["server"]}
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"trial": ordinal, "cell": cell["name"], "server": server, "error": error,
                      "seconds": round(elapsed, 1)}), flush=True)
    return summary


def prepare_corpora(matrix):
    corpora = {}
    for name, spec in matrix["corpora"].items():
        path = ROOT / "data" / f"{name}-{spec['profile']}-{spec['bytes']}-{spec['frame_bytes']}-{spec['seed']}.bin"
        sidecar = path.with_suffix(".json")
        if not path.exists() or not sidecar.exists():
            payloads = corpus.generate_payloads(spec["bytes"], spec["frame_bytes"], spec["seed"], spec["profile"],
                                                spec.get("min_frame_bytes"))
            info = corpus.write_frames(path, payloads) | spec
            sidecar.write_text(json.dumps(info, indent=2), encoding="utf-8")
        info = json.loads(sidecar.read_text(encoding="utf-8"))
        if sha256(path) != info["sha256"]:
            raise RuntimeError(f"corpus hash mismatch: {path}")
        corpora[name] = info | {"name": name, "path": str(path)}
    return corpora


def capacity(results, cell_name):
    """Slower server's median window frame rate in an earlier closed-loop cell, from valid trials
    whose client stayed within its CPU budget (so the rate is the server's, not the client's)."""
    rates = {}
    for entry in results:
        r = entry["result"]
        if (entry["cell"]["name"] == cell_name and not entry["runner_error"]
                and report.client_cpu(r) <= report.CLIENT_CPU_LIMIT):
            rates.setdefault(entry["server"], []).append(r["window"]["frames"] / r["window"]["seconds"])
    if len(rates) < 2:
        raise RuntimeError(f"no client-adequate capacity trials for both servers in {cell_name}")
    return min(statistics.median(v) for v in rates.values())


def trial_key(phase, cell, repetition, server, builds):
    """Identity of a planned trial: any change to the resolved cell or binaries is a new trial."""
    text = json.dumps([phase, cell, repetition, server, builds], sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*", help="run only these phases")
    parser.add_argument("--repetitions", type=int, help="override every phase's repetitions")
    parser.add_argument("--duration", type=float, help="override every cell's duration (smoke use)")
    parser.add_argument("--warmup", type=float, help="override every cell's warmup (smoke use)")
    parser.add_argument("--cooldown", type=float, default=2.0, help="seconds between trials")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted campaign in --output; completed trials are kept")
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    for path in BINARIES.values():
        if not path.is_file():
            raise FileNotFoundError(f"build first: missing {path}")
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=args.resume)
    previous = json.loads((folder / "results.json").read_text(encoding="utf-8")) if args.resume else []
    masks = cpu_layout(matrix.get("server_cores", 4))
    corpora = prepare_corpora(matrix)
    overrides = {k: v for k, v in [("duration", args.duration), ("warmup", args.warmup)] if v is not None}
    environment = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "platform": platform.platform(),
                   "python": sys.version, "arguments": {k: str(v) for k, v in vars(args).items()},
                   "masks": masks, "matrix": matrix, "overrides": overrides, "corpora": corpora,
                   "binaries": {name: {"path": str(p), "sha256": sha256(p)} for name, p in BINARIES.items()},
                   "runtime_environment": {k: v for k, v in os.environ.items()
                                           if k.upper().startswith(("DOTNET_", "COMPLUS_", "CORECLR_"))},
                   "sources": {str(p.relative_to(ROOT)): sha256(p) for pattern in
                               ["src/dotnet/*.cs", "src/dotnet/*.csproj", "src/cpp/*.cpp", "src/cpp/*.hpp",
                                "src/cpp/CMakeLists.txt", "bench/*.py", "tools/*.py", "global.json"]
                               for p in sorted(ROOT.glob(pattern))},
                   "tools": {}}
    for name, cmd in {"dotnet": ["dotnet", "--info"], "cmake": ["cmake", "--version"],
                      "power_plan": ["powercfg", "/getactivescheme"]}.items():
        done = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        environment["tools"][name] = done.stdout[-4000:]
    name = "environment.json" if not previous else f"environment-resume-{len(previous)}.json"
    (folder / name).write_text(json.dumps(environment, indent=2), encoding="utf-8")
    # The seeded order is replayed on resume; completed identical trials are reused, not rerun.
    builds = {name: sha256(path) for name, path in BINARIES.items()}
    done = {e["key"]: e for e in previous}
    if previous:  # results.json is rewritten below; keep the earlier index intact until the end
        (folder / f"results-before-resume-{len(previous)}.json").write_text(json.dumps(previous, indent=2), encoding="utf-8")
    rng, results, ordinal = random.Random(matrix.get("seed", 42)), [], 0
    for phase in matrix["phases"]:
        # Unselected phases still consume the seeded shuffle, so --only never changes order or ordinals.
        selected = not args.only or phase["name"] in args.only
        cells = [DEFAULTS | matrix.get("defaults", {}) | cell | overrides | {"phase": phase["name"]}
                 for cell in phase["cells"]]
        for cell in cells:
            cell.setdefault("seed", matrix.get("seed", 42))
            if isinstance(cell["rate"], dict) and selected:
                cell["rate_rule"] = cell["rate"]
                cell["rate"] = round(capacity(results, cell["rate"]["of"]) * cell["rate"]["fraction"], 3)
        for repetition in range(args.repetitions or phase.get("repetitions", 1)):
            order = list(cells)
            rng.shuffle(order)
            for cell in order:
                servers = list(cell["servers"])
                rng.shuffle(servers)
                for server in servers:
                    ordinal += 1
                    if not selected:
                        continue
                    key = trial_key(phase["name"], cell, repetition, server, builds)
                    if key in done:
                        results.append(done.pop(key))
                        continue
                    entry = trial(folder, ordinal, cell | {"repetition": repetition}, server, masks, corpora[cell["corpus"]])
                    entry["key"] = key
                    results.append(entry)
                    (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                    time.sleep(args.cooldown)
    if done:  # earlier trials that no longer match the plan are preserved separately, never pooled
        (folder / f"results-unmatched-{time.time_ns()}.json").write_text(json.dumps(list(done.values()), indent=2),
                                                                         encoding="utf-8")
    print(json.dumps({"completed_trials": len(results), "failed": sum(bool(r["runner_error"]) for r in results),
                      "results": str(folder)}))


if __name__ == "__main__":
    main()
