"""Held-out generated records checked through both socket implementations and oracle."""
import argparse
import json
import random
from pathlib import Path

from protocol import ACK, Server, Summary, corpus, frame, response


def payloads(seed, count):
    rng = random.Random(seed)
    texts = ["", "a", "\0\b\f\n\r\t", '"\\/', "გამარჯობა", "😀💻", "𝄞", "é", "e\u0301", "漢字"]
    for index in range(count):
        values = []
        for _ in range(rng.randrange(0, 9)):
            event = {"id": rng.choice([0, corpus.MASK, rng.getrandbits(64)]),
                     "timestamp_ns": rng.getrandbits(64), "source": rng.getrandbits(32),
                     "kind": f"kind{rng.randrange(16):02d}",
                     "value_milli": rng.choice([-1000000, 1000000, 0, rng.randrange(-1000000, 1000001)]),
                     "flags": rng.getrandbits(32), "message": rng.choice(texts) * rng.randrange(0, 100)}
            items = list(event.items())
            rng.shuffle(items)
            values.append(dict(items))
        encoded = json.dumps(values, ensure_ascii=index % 2 == 0,
                             separators=(",", ":") if index % 3 else (", ", ": ")).encode()
        if index % 5 == 0:
            encoded = encoded.replace(b'"source"', b'"\\u0073ource"')
        yield encoded


def run(binary, mode, fixtures):
    with Server(binary, "--mode", mode, "--max-frame", "65536") as server:
        with server.connect() as sock:
            summary = Summary()
            for index, (payload, expected) in enumerate(fixtures):
                seq = index + 2
                sock.sendall(frame(2, seq, payload))
                actual = ACK.unpack(response(sock, 2, seq))
                assert actual == (expected["records"], expected["digest"]), (mode, index, actual, expected)
                summary.add(seq, payload, expected)
            end_seq = len(fixtures) + 2
            sock.sendall(frame(3, end_seq))
            assert response(sock, 3, end_seq) == summary.pack()
        assert server.stop()["valid"]
    return {"binary": str(binary), "mode": mode, "validated_frames": len(fixtures), "records": summary.records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers", nargs="+", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=982451653)
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.frames <= 100000:
        parser.error("frames must be 1..100000")
    fixtures = [(payload, corpus.process(payload)) for payload in payloads(args.seed, args.frames)]
    for binary in args.servers:
        for mode in ["aggregate", "retain-reuse", "retain-allocate"]:
            print(json.dumps(run(binary.resolve(), mode, fixtures)), flush=True)


if __name__ == "__main__":
    main()
