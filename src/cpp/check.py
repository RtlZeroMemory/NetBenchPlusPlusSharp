"""Native integration/lifetime check; shared protocol and measurement suites are in tests/."""
import argparse
import json
import pathlib
import socket
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--exe", default="src/cpp/build/Release/tcpbench.exe")
    args = p.parse_args()
    exe = str((ROOT / args.exe).resolve())
    subprocess.run([exe, "selftest"], check=True, cwd=ROOT)
    for mode in ("aggregate", "retain-reuse", "retain-allocate", "transport"):
        subprocess.run([exe, "process", "--mode", mode, "--corpus", "tests/fixtures/golden.bin",
                        "--duration", ".02", "--warmup", ".01"], check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        for arrival, rate, cap in (("steady", "0", "0"), ("steady", "100", "3"),
                                   ("poisson", "100", "0"), ("burst", "100", "0")):
            number = port()
            server = subprocess.Popen([exe, "server", "--mode", mode, "--port", str(number),
                                       "--control-stdin", "1", "--workers", "2", "--io-cap", cap],
                                      cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            try:
                assert json.loads(server.stdout.readline())["event"] == "ready"
                client = subprocess.run([exe, "client", "--mode", mode, "--port", str(number),
                                         "--corpus", "tests/fixtures/golden.bin", "--connections", "4",
                                         "--duration", ".12", "--warmup", ".03", "--arrival", arrival,
                                         "--rate", rate, "--io-cap", cap], cwd=ROOT, capture_output=True,
                                        text=True, timeout=20)
                assert client.returncode == 0, client.stderr
                result = json.loads(client.stdout)
                assert result["valid"] and result["admitted"] == result["completed_frames"]
                assert result["queue_high_water"] <= 8
                out, err = server.communicate("stop\n", timeout=10)
                assert server.returncode == 0, err
                assert json.loads(out)["valid"]
                print(json.dumps({"mode": mode, "arrival": arrival, "rate": rate,
                                  "io_cap": cap, "frames": result["completed_frames"], "passed": True}))
            finally:
                if server.poll() is None:
                    server.kill()
                    server.communicate()


if __name__ == "__main__":
    main()
