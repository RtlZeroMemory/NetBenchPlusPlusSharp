"""All four client/server pairings in every mode and load shape, including deterministic
partial I/O (--io-cap on both sides). Server and client accounting must agree exactly."""
import argparse
import json
import subprocess
from pathlib import Path

from protocol import BINARIES, ROOT, Server, command
CORPUS = ROOT / "tests/fixtures/golden.bin"
LOADS = {"closed": ["--rate", "0"], "capped-io": ["--rate", "0", "--io-cap", "7"],
         "steady": ["--rate", "2000"], "poisson": ["--rate", "2000", "--arrival", "poisson"]}


def exchange(server, client, mode, load):
    cap = ["--io-cap", "5"] if load == "capped-io" else []
    with Server(BINARIES[server], "--mode", mode, "--workers", "2", *cap) as srv:
        done = subprocess.run(command(BINARIES[client]) + [
            "client", "--port", str(srv.port), "--mode", mode, "--corpus", str(CORPUS), "--connections", "4",
            "--window", "8", "--duration", "0.4", "--warmup", "0.1", *LOADS[load]],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        s = srv.stop(timeout=30)
    c = json.loads(done.stdout.strip().splitlines()[-1])
    counts = c["counts"]
    assert done.returncode == 0 and c["valid"], (server, client, mode, load, c.get("error"), done.stderr[-400:])
    assert s["valid"] and s["connections"]["graceful"] == 4, s["connections"]
    assert counts["offered"] == counts["admitted"] + counts["rejected"] and counts["admitted"] == counts["acknowledged"]
    assert s["measured"]["complete"] and s["measured"]["frames"] == counts["acknowledged"], (s["measured"], counts)
    return counts["acknowledged"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpp", type=Path, help="native binary to use, e.g. the ASAN build")
    args = parser.parse_args()
    if args.cpp:
        BINARIES["cpp"] = args.cpp.resolve()
    for mode in ["aggregate", "retain-reuse", "retain-allocate", "transport"]:
        for load in LOADS:
            for server in BINARIES:
                for client in BINARIES:
                    frames = exchange(server, client, mode, load)
                    print(json.dumps({"mode": mode, "load": load, "server": server, "client": client,
                                      "frames": frames, "status": "passed"}), flush=True)


if __name__ == "__main__":
    main()
