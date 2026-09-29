"""Reproduce native worker-startup failure under a Windows job commit limit."""
import argparse
import ctypes
from ctypes import wintypes
import json
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


class BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ["ReadOperationCount", "WriteOperationCount",
        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount"]]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def trial(binary, workers, mib):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [str(binary), "server", "--port", str(port), "--workers", str(workers),
               "--max-frame", "1", "--max-connections", "1", "--run-seconds", "0.1"]
    job = kernel.CreateJobObjectW(None, None)
    assert job
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.ProcessMemoryLimit = mib * 1024 * 1024
    assert kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW | 4)  # CREATE_SUSPENDED
    try:
        if not kernel.AssignProcessToJobObject(job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        assert ntdll.NtResumeProcess(int(process._handle)) == 0
        stdout, stderr = process.communicate(timeout=8)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        kernel.CloseHandle(job)
    return dict(binary=str(binary), workers=workers, process_commit_limit_mib=mib,
                command=command, exit_code=process.returncode,
                stdout=stdout.decode(errors="replace"), stderr=stderr.decode(errors="replace"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "src/cpp/build/Release/tcpbench.exe")
    parser.add_argument("--mib", nargs="+", type=int, default=[4, 6, 8, 10, 12, 16, 24])
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 256])
    args = parser.parse_args()
    results = []
    for mib in args.mib:
        for workers in args.workers:
            result = trial(args.binary.resolve(), workers, mib)
            results.append(result)
            print(json.dumps(result), flush=True)
    (OUT / "startup-memory.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
