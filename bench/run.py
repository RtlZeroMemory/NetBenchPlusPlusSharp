"""Windows loopback macrobenchmark orchestration; Python never sends timed data."""
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import corpus


class TrialFailure(RuntimeError):
    """A failed trial still carries its persisted result for campaign reporting."""

    def __init__(self, summary):
        super().__init__(summary["runner_error"])
        self.summary = summary


def binaries():
    cs = ROOT / "src/dotnet/bin/Release/net11.0/Bench.dll"
    native = ROOT / "src/cpp/build/Release/tcpbench.exe"
    for path in [cs, native]:
        if not path.is_file():
            raise FileNotFoundError(f"Build first: missing {path}")
    return {"csharp": cs, "cpp": native}


def command(binary: Path):
    return ["dotnet", str(binary)] if binary.suffix == ".dll" else [str(binary)]


def cpu_masks(server_cores=4):
    """One logical CPU from each physical core; this host has one CPU group."""
    if os.name != "nt":
        raise RuntimeError("This runner targets native Windows, not WSL")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetLogicalProcessorInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetLogicalProcessorInformation.restype = wintypes.BOOL
    length = wintypes.DWORD()
    kernel.GetLogicalProcessorInformation(None, ctypes.byref(length))
    if not length.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(length.value)
    if not kernel.GetLogicalProcessorInformation(buffer, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())

    class Info(ctypes.Structure):
        _fields_ = [("mask", ctypes.c_size_t), ("relationship", wintypes.DWORD),
                    ("data", ctypes.c_ulonglong * 2)]

    if ctypes.sizeof(Info) != 32:
        raise RuntimeError("Use 64-bit Python")
    cores = []
    for offset in range(0, length.value, ctypes.sizeof(Info)):
        item = Info.from_buffer_copy(buffer, offset)
        if item.relationship == 0:
            cores.append(int(item.mask))
    cores.sort()
    if len(cores) < server_cores + 2:
        raise RuntimeError("Need server cores, at least one client core and one spare core")
    logical = [mask & -mask for mask in cores]
    server = sum(logical[:server_cores])
    client = sum(logical[server_cores:-1])
    return {"physical_core_masks": cores, "server": server, "client": client,
            "spare": logical[-1], "server_cores": server_cores}


def spawn(args, mask, log, env=None):
    """Children inherit affinity, so runtime initialization sees its CPU budget."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    handle = kernel.GetCurrentProcess()
    previous, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(handle, ctypes.byref(previous), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    if mask & ~system.value or not kernel.SetProcessAffinityMask(handle, mask):
        raise RuntimeError(f"Cannot apply requested CPU mask {mask:#x}")
    child_env = os.environ.copy()
    child_env["DOTNET_PROCESSOR_COUNT"] = str(mask.bit_count())
    if env:
        child_env.update(env)
    try:
        return subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.PIPE, env=child_env)
    finally:
        if not kernel.SetProcessAffinityMask(handle, previous.value):
            raise ctypes.WinError(ctypes.get_last_error())


def wait_ready(process, port, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited {process.returncode} before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("Server readiness timeout")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def environment_details():
    commands = {"dotnet": ["dotnet", "--info"], "cmake": ["cmake", "--version"],
                "power_plan": ["powercfg", "/getactivescheme"],
                "cpu_memory": ["powershell", "-NoProfile", "-Command",
                    "[pscustomobject]@{CPU=@(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize);MemoryBytes=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory} | ConvertTo-Json -Depth 4"]}
    runtime_settings = ["TieredCompilation", "TieredPGO", "ReadyToRun", "gcServer",
                        "gcConcurrent", "GCHeapHardLimit", "GCHeapCount", "GCStress"]
    output = {"runtime_environment": {
        prefix + name: os.environ.get(prefix + name)
        for prefix in ["DOTNET_", "COMPlus_"] for name in runtime_settings}}
    for name, args in commands.items():
        try:
            item = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=30)
            output[name] = {"exit_code": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
        except (OSError, subprocess.TimeoutExpired) as error:
            output[name] = {"unavailable": str(error)}
    paths = [ROOT / "global.json", ROOT / "docs/contract.md", ROOT / ".clang-format", ROOT / ".editorconfig"]
    for pattern in ["src/dotnet/*.cs", "src/dotnet/*.csproj", "src/cpp/*.cpp", "src/cpp/*.hpp", "src/cpp/CMakeLists.txt", "bench/*.py", "tools/*.py"]:
        paths.extend(ROOT.glob(pattern))
    output["source_sha256"] = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(set(paths)) if p.is_file()}
    return output


def result_value(result, *names, default=None):
    for name in names:
        if name in result:
            return result[name]
    return default


def trial(folder, executables, masks, scenario, server_name, client_name, ordinal):
    destination = folder / f"{ordinal:04d}-{scenario['mode']}-{server_name}-{client_name}"
    destination.mkdir()
    with socket.socket() as temporary:
        temporary.bind(("127.0.0.1", 0))
        port = temporary.getsockname()[1]
    manifest = dict(scenario, server=server_name, client=client_name, masks=masks,
                    binaries={k: {"path": str(v), "sha256": sha256(v)} for k, v in executables.items()})
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_hash = hashlib.sha256(canonical).hexdigest()
    (destination / "manifest.json").write_bytes(canonical)
    # Keep server alive beyond the client's own drain bound; termination is runner cleanup.
    server_args = command(executables[server_name]) + ["server", "--port", str(port),
        "--mode", scenario["mode"], "--max-frame", str(scenario["frame_bytes"]),
        "--max-connections", str(scenario["connections"] + 1), "--workers", str(masks["server_cores"]),
        "--manifest-hash", manifest_hash, "--control-stdin", "1",
        "--output", str(destination / "server.json")]
    client_args = command(executables[client_name]) + ["client", "--port", str(port),
        "--corpus", scenario["corpus"], "--mode", scenario["mode"],
        "--connections", str(scenario["connections"]), "--duration", str(scenario["duration"]),
        "--warmup", str(scenario["warmup"]), "--window", str(scenario["window"]),
        "--rate", str(scenario["rate"]), "--arrival", scenario.get("arrival", "steady"),
        "--seed", str(scenario["seed"]),
        "--manifest-hash", manifest_hash, "--output", str(destination / "client.json")]
    (destination / "commands.json").write_text(json.dumps({"server": server_args, "client": client_args}, indent=2), encoding="utf-8")
    started = time.monotonic()
    error = None
    with (destination / "server.log").open("w", encoding="utf-8") as server_log:
        server = spawn(server_args, masks["server"], server_log)
        client = None
        try:
            wait_ready(server, port)
            with (destination / "client.log").open("w", encoding="utf-8") as client_log:
                client = spawn(client_args, masks["client"], client_log)
                timeout = 2 * scenario["warmup"] + scenario["duration"] + 120
                code = client.wait(timeout=timeout)
            if code:
                raise RuntimeError(f"Client exited {code}; inspect {destination}")
            result = json.loads((destination / "client.json").read_text(encoding="utf-8-sig"))
            if result.get("valid") is False or result.get("success") is False:
                raise RuntimeError(f"Invalid result: {destination}")
        except Exception as failure:
            error = str(failure)
        finally:
            if client is not None and client.poll() is None:
                client.kill()
                client.wait()
            if client is not None and client.stdin:
                client.stdin.close()
            if server.poll() is None:
                try:
                    server.stdin.write(b"stop\n")
                    server.stdin.flush()
                    server.stdin.close()
                    server.wait(timeout=30)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    server.kill()
                    server.wait()
                    error = error or f"Server did not shut down cleanly; inspect {destination}"
            if server.returncode:
                error = error or f"Server exited {server.returncode}; inspect {destination}"
    outputs = {}
    for role in ["client", "server"]:
        try:
            outputs[role] = json.loads((destination / f"{role}.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as failure:
            outputs[role] = {"valid": False, "error": str(failure)}
            error = error or f"Missing or invalid {role} result: {destination}"
    if outputs["server"].get("valid") is False:
        error = error or f"Invalid server result: {destination}"
    summary = {"path": str(destination), "scenario": scenario, "server": server_name,
               "client": client_name, "elapsed_seconds": time.monotonic() - started,
               "builds": {name: info["sha256"] for name, info in manifest["binaries"].items()},
               "cpu_masks": masks, "runner_error": error,
               "result": outputs["client"], "server_result": outputs["server"]}
    (destination / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"trial": ordinal, "mode": scenario["mode"], "server": server_name,
                      "client": client_name, "rate": scenario["rate"], "result": str(destination / "client.json")}), flush=True)
    if error:
        raise TrialFailure(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["smoke", "first", "pilot", "confirm", "soak"], default="smoke")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--warmup", type=float)
    parser.add_argument("--corpus-bytes", type=int)
    parser.add_argument("--server-cores", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--client", choices=["cpp", "csharp"], default="cpp")
    parser.add_argument("--arrival", choices=["steady", "poisson", "burst"], default="steady")
    args = parser.parse_args()
    if args.duration is not None and (not 0 < args.duration <= 86400):
        parser.error("duration must be 0..86400 seconds")
    if args.warmup is not None and not 0 <= args.warmup <= 3600:
        parser.error("warmup must be 0..3600 seconds")
    if args.corpus_bytes is not None and args.corpus_bytes <= 0:
        parser.error("corpus-bytes must be positive")
    executables, masks = binaries(), cpu_masks(args.server_cores)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = (args.output or ROOT / "results" / f"{stamp}-{args.suite}").resolve()
    folder.mkdir(parents=True, exist_ok=False)
    defaults = {"smoke": (1, 0.2, 1024**2), "first": (30, 5, 256 * 1024**2),
                "pilot": (30, 5, 256 * 1024**2),
                "confirm": (120, 30, 2 * 1024**3), "soak": (1800, 30, 2 * 1024**3)}
    duration, warmup, corpus_size = defaults[args.suite]
    if args.duration is not None: duration = args.duration
    if args.warmup is not None: warmup = args.warmup
    if args.corpus_bytes is not None: corpus_size = args.corpus_bytes
    manifest = {"suite": args.suite, "started_utc": stamp, "platform": platform.platform(),
                "python": sys.version, "masks": masks, "arguments": vars(args) | {"output": str(folder)},
                "environment": environment_details(),
                "notice": "Shared-host loopback application comparison. Smoke checks correctness; first/pilot are exploratory, not universal language rankings."}
    (folder / "environment.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    modes = ["aggregate", "retain-reuse", "retain-allocate"]
    if args.suite == "smoke":
        cells = [(mode, 4096, 4) for mode in modes]
    elif args.suite == "pilot":
        cells = [(mode, size, connections) for mode in modes for size in [65536, 1048576] for connections in [1, 16]]
    else:
        cells = [("aggregate", 65536, 16), ("aggregate", 1048576, 1),
                 ("retain-reuse", 65536, 16), ("retain-allocate", 65536, 16)]
    corpora = {}
    for size in sorted({cell[1] for cell in cells}):
        path = ROOT / "data" / f"corpus-{size}-{corpus_size}-{args.seed}.bin"
        sidecar = path.with_suffix(".json")
        if not path.exists() or not sidecar.exists():
            generated = corpus.write_frames(path, corpus.generate_payloads(corpus_size, size, args.seed))
            generated.update(seed=args.seed, target_frame_bytes=size)
            sidecar.write_text(json.dumps(generated, indent=2), encoding="utf-8")
        info = json.loads(sidecar.read_text(encoding="utf-8"))
        if sha256(path) != info["sha256"]:
            raise RuntimeError(f"Corpus hash mismatch: {path}")
        corpora[size] = (path, info)
    rng, results, ordinal = random.Random(args.seed), [], 0

    def run_cell(cell, rate, repeats=1, phase="measurement", d=duration, w=warmup, client_names=None):
        nonlocal ordinal
        mode, size, connections = cell
        path, info = corpora[size]
        scenario = {"mode": mode, "frame_bytes": size, "connections": connections,
                    "duration": d, "warmup": w, "window": 64, "rate": rate,
                    "seed": args.seed,
                    "arrival": args.arrival, "corpus": str(path), "corpus_sha256": info["sha256"],
                    "corpus_payload_bytes": info["payload_bytes"], "phase": phase}
        entries = []
        for repetition in range(repeats):
            clients = client_names or (["cpp", "csharp"] if args.suite == "smoke" else [args.client])
            pairs = [(s, c) for c in clients
                     for s in ["csharp", "cpp"]]
            rng.shuffle(pairs)
            for server, client in pairs:
                ordinal += 1
                try:
                    summary = trial(folder, executables, masks, scenario | {"pair": repetition}, server, client, ordinal)
                except TrialFailure as failure:
                    results.append(failure.summary)
                    (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                    raise
                results.append(summary)
                entries.append(summary)
                (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        return entries

    if args.suite == "first":
        capacities = {}
        for cell in cells:
            measured = run_cell(cell, 0, repeats=3, phase="closed-loop")
            capacities[cell] = min(statistics.median(
                entry["result"]["window_completed_frames"] / entry["result"]["window_seconds"]
                for entry in measured if entry["server"] == server)
                for server in ["csharp", "cpp"])
        alternate = "csharp" if args.client == "cpp" else "cpp"
        for cell in cells:
            run_cell(cell, 0, phase="alternate-client", client_names=[alternate])
        for size, connections in sorted({(cell[1], cell[2]) for cell in cells}):
            run_cell(("transport", size, connections), 0, phase="transport-control")
        for cell in cells:
            if cell[1] == 65536:
                for ratio in [0.5, 0.9, 1.2]:
                    run_cell(cell, max(1, capacities[cell] * ratio), phase="scheduled-exploration")
    for cell in cells if args.suite != "first" else []:
        if args.suite in ["confirm", "soak"]:
            pilot = run_cell(cell, 0, phase="calibration", d=30, w=5)
            capacities = []
            for entry in pilot:
                result = entry["result"]
                frames = result_value(result, "window_completed_frames", "completed_frames")
                seconds = result_value(result, "window_seconds", "duration_seconds", default=30)
                if frames is None or frames <= 0:
                    raise RuntimeError("Calibration result lacks positive completed frame count")
                capacities.append(frames / seconds)
            common = min(capacities)
            for ratio in ([0.5, 0.9, 1.2] if args.suite == "confirm" else [0.7]):
                run_cell(cell, max(1, common * ratio), repeats=7 if args.suite == "confirm" else 1)
        else:
            run_cell(cell, 0)
            if args.suite == "smoke":
                run_cell(cell, 100)
    print(json.dumps({"completed_trials": len(results), "results": str(folder)}))


if __name__ == "__main__":
    main()
