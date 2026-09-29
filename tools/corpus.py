"""Deterministic corpus generator and deliberately slow independent protocol oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
from pathlib import Path

OFFSET = 14695981039346656037
PRIME = 1099511628211
MASK = (1 << 64) - 1
MAX_FRAME = 16 * 1024 * 1024
FIELDS_ORDER = ["id", "timestamp_ns", "source", "kind", "value_milli", "flags", "message"]
HEADER = struct.Struct(">8sII")
META = struct.Struct(">IQQ")
BINS = struct.Struct(">64Q64q")
ROW = struct.Struct(">BQQIBqII")


def fnv(data: bytes, digest: int = OFFSET) -> int:
    for value in data:
        digest = ((digest ^ value) * PRIME) & MASK
    return digest


class Integer(int):
    def __new__(cls, token: str):
        value = super().__new__(cls, token)
        value.token = token
        return value


def fail_number(token: str):
    raise ValueError(f"Not an integer token: {token[:30]}")


def object_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate property")
        result[name] = value
    return result


def integer(value, low: int, high: int, unsigned=False):
    if not isinstance(value, Integer) or not low <= value <= high:
        raise ValueError("Integer type/range")
    if unsigned and value.token.startswith("-"):
        raise ValueError("Unsigned negative token")
    return int(value)


def process(payload: bytes) -> dict:
    if not 0 < len(payload) <= MAX_FRAME:
        raise ValueError("Payload length")
    try:
        data = json.loads(payload.decode("utf-8", errors="strict"),
                          parse_int=Integer, parse_float=fail_number,
                          parse_constant=fail_number, object_pairs_hook=object_pairs)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid Unicode or nesting") from error
    if not isinstance(data, list):
        raise ValueError("Root must be an array")
    counts, sums, digest = [0] * 64, [0] * 64, OFFSET
    for event in data:
        if not isinstance(event, dict) or set(event) != set(FIELDS_ORDER):
            raise ValueError("Record fields")
        identity = integer(event["id"], 0, MASK, True)
        timestamp = integer(event["timestamp_ns"], 0, MASK, True)
        source = integer(event["source"], 0, (1 << 32) - 1, True)
        value = integer(event["value_milli"], -1_000_000, 1_000_000)
        flags = integer(event["flags"], 0, (1 << 32) - 1, True)
        kind = event["kind"]
        if not isinstance(kind, str) or kind not in [f"kind{i:02d}" for i in range(16)]:
            raise ValueError("Kind")
        message = event["message"]
        if not isinstance(message, str):
            raise ValueError("Message type")
        try:
            message_bytes = message.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError("Unpaired surrogate") from error
        if len(message_bytes) > 4096:
            raise ValueError("Message too long")
        kind_index = int(kind[-2:])
        digest = fnv(ROW.pack(0x52, identity, timestamp, source, kind_index,
                              value, flags, len(message_bytes)), digest)
        digest = fnv(message_bytes, digest)
        bucket = kind_index * 4 + (flags & 3)
        counts[bucket] += 1
        sums[bucket] += value
    return {"records": len(data), "digest": digest, "counts": counts, "sums": sums}


def read_corpus(path: Path):
    with path.open("rb") as stream:
        header = stream.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("Truncated corpus header")
        magic, count, reserved = HEADER.unpack(header)
        if magic != b"TCPBCH01" or reserved or not 0 < count <= 10_000_000:
            raise ValueError("Invalid corpus header")
        for _ in range(count):
            metadata = stream.read(META.size + BINS.size)
            if len(metadata) != META.size + BINS.size:
                raise ValueError("Truncated corpus metadata")
            length, records, digest = META.unpack_from(metadata)
            if not 0 < length <= MAX_FRAME or records > 1_000_000_000:
                raise ValueError("Corpus length/count")
            bins = BINS.unpack_from(metadata, META.size)
            payload = stream.read(length)
            if len(payload) != length:
                raise ValueError("Truncated corpus payload")
            yield payload, {"records": records, "digest": digest,
                            "counts": list(bins[:64]), "sums": list(bins[64:])}
        if stream.read(1):
            raise ValueError("Trailing corpus bytes")


def write_frames(path: Path, payloads):
    path.parent.mkdir(parents=True, exist_ok=True)
    count, total, records, largest = 0, 0, 0, 0
    with path.open("wb") as stream:
        stream.write(HEADER.pack(b"TCPBCH01", 0, 0))
        for payload in payloads:
            expected = process(payload)
            stream.write(META.pack(len(payload), expected["records"], expected["digest"]))
            stream.write(BINS.pack(*expected["counts"], *expected["sums"]))
            stream.write(payload)
            count += 1
            total += len(payload)
            largest = max(largest, len(payload))
            records += expected["records"]
        if not count:
            raise ValueError("Empty corpus")
        stream.seek(0)
        stream.write(HEADER.pack(b"TCPBCH01", count, 0))
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"format": "TCPBCH01", "frames": count, "payload_bytes": total, "max_frame_bytes": largest,
            "records": records, "sha256": sha}


MIXED_MESSAGES = ["", "sensor ok", "temperature: 24", "თბილისი 🌡️", 'quote " slash \\ newline\n',
                  "測定結果 정상", "x" * 256, "event: " + "A" * 37]


def mixed_record(rng, identity):
    """First-pass profile: eight fixed messages, shuffled fields, every third record ASCII-escaped."""
    event = {"id": identity, "timestamp_ns": 1_700_000_000_000_000_000 + identity,
             "source": rng.randrange(10000), "kind": f"kind{rng.randrange(16):02d}",
             "value_milli": rng.randrange(-1_000_000, 1_000_001),
             "flags": rng.randrange(1 << 32), "message": rng.choice(MIXED_MESSAGES)}
    pairs = list(event.items())
    rng.shuffle(pairs)
    return json.dumps(dict(pairs), ensure_ascii=(identity % 3 == 0), separators=(",", ":"))


EASY_TEXT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-.:"


def easy_record(rng, identity):
    """Canonical field order, compact, unique ASCII messages of 8-64 bytes needing no escapes."""
    event = {"id": identity, "timestamp_ns": 1_700_000_000_000_000_000 + identity * 1000,
             "source": rng.randrange(10000), "kind": f"kind{rng.randrange(16):02d}",
             "value_milli": rng.randrange(-1_000_000, 1_000_001), "flags": rng.randrange(1 << 16),
             "message": "".join(rng.choices(EASY_TEXT, k=rng.randint(8, 64)))}
    return json.dumps(event, separators=(",", ":"))


# Character pools by UTF-8 width and escaping need; runs of one pool form hard messages.
HARD_POOLS = [
    ("ascii", "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.;:!?-_()[]{}@#$%^&*+=<>|~`'", 50),
    ("escaped", '"\\/\b\f\n\r\t' + "".join(map(chr, range(0, 32))), 8),
    ("two-byte", "éèêëàâäôöûüçñßÆØÅæøåΩΔЖжЯя", 15),
    ("three-byte", "漢字測定結果정상日本語中文한국어€₹हक्षि‍́", 15),
    ("four-byte", "😀💻🌡𝄞🚀🧪🐍🜂", 12),
]
HARD_SEPARATORS = [((",", ":"), 80), ((", ", ": "), 12), ((",\n ", " : "), 5), (("\t,\r\n", ":\t"), 3)]


def hard_message(rng):
    """Heavy-tailed decoded length (0 B..4 KiB) built from runs of mixed-width characters."""
    r = rng.random()
    target = (rng.randint(0, 31) if r < .45 else rng.randint(32, 511) if r < .80
              else rng.randint(512, 2047) if r < .97 else rng.randint(2048, 4096))
    parts, size = [], 0
    while size < target:
        _, pool, _ = rng.choices(HARD_POOLS, weights=[w for *_, w in HARD_POOLS])[0]
        run = "".join(rng.choices(pool, k=rng.randint(1, 48)))
        while run and size + len(run.encode()) > target:
            run = run[:-1]
        if not run:
            break
        parts.append(run)
        size += len(run.encode())
    return "".join(parts)


def hard_record(rng, identity):
    """Shuffled fields, mixed whitespace, escapes, Unicode, boundary integers and -0."""
    value = rng.choices([-1_000_000, 1_000_000, 0, None], weights=[5, 5, 5, 85])[0]
    event = {"id": rng.choices([0, MASK, rng.randrange(1000), rng.getrandbits(64)], weights=[5, 5, 20, 70])[0],
             "timestamp_ns": rng.choice([rng.getrandbits(64), 1_700_000_000_000_000_000 + identity]),
             "source": rng.choices([0, (1 << 32) - 1, rng.getrandbits(32)], weights=[10, 10, 80])[0],
             "kind": f"kind{rng.randrange(16):02d}",
             "value_milli": rng.randrange(-1_000_000, 1_000_001) if value is None else value,
             "flags": rng.choices([0, (1 << 32) - 1, rng.getrandbits(32)], weights=[10, 10, 80])[0],
             "message": hard_message(rng)}
    order = list(FIELDS_ORDER)
    rng.shuffle(order)
    ascii_only = rng.random() < .3
    item_sep, key_sep = rng.choices([s for s, _ in HARD_SEPARATORS], weights=[w for _, w in HARD_SEPARATORS])[0]
    escaped_key = rng.choice(order) if rng.random() < .03 else None
    parts = []
    for name in order:
        key = f'"\\u{ord(name[0]):04x}{name[1:]}"' if name == escaped_key else json.dumps(name)
        v = event[name]
        if isinstance(v, str):
            text = json.dumps(v, ensure_ascii=ascii_only)
            text = text.replace("/", "\\/") if rng.random() < .2 else text
        else:
            text = "-0" if name == "value_milli" and v == 0 and rng.random() < .5 else str(v)
        parts.append(key + key_sep + text)
    return "{" + item_sep.join(parts) + "}"


PROFILES = {"mixed": mixed_record, "easy": easy_record, "hard": hard_record}


def generate_payloads(target_bytes: int, frame_bytes: int, seed: int, profile: str = "mixed",
                      min_frame_bytes: int | None = None):
    """Frames of complete records up to a target size; the mixed profile is byte-identical
    to the first-pass generator. With min_frame_bytes, each frame's target is log-uniform
    in [min_frame_bytes, frame_bytes], drawn from a separate RNG."""
    rng, sizes = random.Random(seed), random.Random(seed ^ 0x5EED5EED)
    record = PROFILES[profile]

    def next_target():
        if not min_frame_bytes:
            return frame_bytes
        return int(math.exp(sizes.uniform(math.log(min_frame_bytes), math.log(frame_bytes))))

    size, identity, target = 0, 0, next_target()
    frame, frame_size = [], 2
    while size < target_bytes:
        item = record(rng, identity).encode("utf-8")
        if frame and frame_size + len(item) + 1 > target:
            payload = b"[" + b",".join(frame) + b"]"
            yield payload
            size += len(payload)
            frame, frame_size, target = [], 2, next_target()
        frame.append(item)
        frame_size += len(item) + (len(frame) > 1)
        identity += 1
    # Complete records only; the last un-emitted record is intentionally discarded.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--bytes", type=int, default=2 * 1024**3)
    generate.add_argument("--frame-bytes", type=int, default=65536)
    generate.add_argument("--min-frame-bytes", type=int, help="mixed frame sizes, log-uniform")
    generate.add_argument("--profile", choices=sorted(PROFILES), default="mixed")
    generate.add_argument("--seed", type=int, default=42)
    verify = sub.add_parser("verify")
    verify.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        if args.bytes <= 0 or not 1024 <= args.frame_bytes <= MAX_FRAME or \
                not 1024 <= (args.min_frame_bytes or 1024) <= args.frame_bytes:
            parser.error("bytes must be positive; 1024 <= min-frame-bytes <= frame-bytes <= 16 MiB")
        payloads = generate_payloads(args.bytes, args.frame_bytes, args.seed, args.profile, args.min_frame_bytes)
        result = write_frames(args.output, payloads)
        result.update(seed=args.seed, profile=args.profile, target_frame_bytes=args.frame_bytes,
                      min_frame_bytes=args.min_frame_bytes,
                      generator="Python stdlib random; exact payloads are authoritative")
        args.output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    else:
        count = 0
        for payload, expected in read_corpus(args.path):
            actual = process(payload)
            if any(actual[key] != value for key, value in expected.items()):
                raise ValueError(f"Oracle mismatch at frame {count}")
            count += 1
        result = {"verified_frames": count}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
