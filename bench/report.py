"""Create a transparent per-trial CSV and grouped report without averaging quantiles."""
import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path


def exclusion_reasons(entry):
    result = entry["result"]
    reasons = []
    if entry.get("runner_error"):
        reasons.append("runner_failure")
    if result.get("valid") is not True or result.get("success") is False:
        reasons.append("invalid_or_unverified")
    if entry.get("server_result", {}).get("valid") is False:
        reasons.append("invalid_server")
    if result.get("generator_adequate") is not True:
        reasons.append("generator_inadequate_or_unverified")
    for name in ["failed", "timedout", "unresolved"]:
        if result.get(name, 0):
            reasons.append(name)
    seconds = result.get("window_seconds", result.get("duration_seconds", entry["scenario"]["duration"]))
    if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
        reasons.append("invalid_window")
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    entries = json.loads((args.directory / "results.json").read_text(encoding="utf-8"))
    rows = []
    for entry in entries:
        scenario, result = entry["scenario"], entry["result"]
        latency = result.get("latency_ns", {})
        histogram = result.get("scheduled_latency", latency)
        seconds = result.get("window_seconds", result.get("duration_seconds", scenario["duration"]))
        completed = result.get("window_completed_frames", result.get("completed_frames", 0))
        valid_seconds = isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds > 0
        server = entry.get("server_result", {})
        receive = server.get("receive_stage_ns", server.get("receive_elapsed_ns", {}))
        processing = server.get("processing_stage_ns", server.get("processing_elapsed_ns", {}))
        reasons = exclusion_reasons(entry)
        rows.append({"server": entry["server"], "client": entry["client"], "mode": scenario["mode"],
            "frame_bytes": scenario["frame_bytes"], "connections": scenario["connections"],
            "offered_rate": scenario["rate"], "phase": scenario["phase"], "pair": scenario["pair"],
            "arrival": scenario.get("arrival", "steady"), "window_seconds": seconds,
            "warmup": scenario.get("warmup"), "window": scenario.get("window"),
            "seed": scenario.get("seed"), "corpus_sha256": scenario.get("corpus_sha256"),
            "builds": json.dumps(entry.get("builds"), sort_keys=True),
            "cpu_masks": json.dumps(entry.get("cpu_masks"), sort_keys=True),
            "comparison_eligible": not reasons, "exclusion_reasons": ";".join(reasons),
            "valid": result.get("valid"), "success": result.get("success"),
            "generator_adequate": result.get("generator_adequate"), "sustainable": result.get("sustainable"),
            "error": result.get("error"), "scheduler_late_over_1ms": result.get("scheduler_late_over_1ms", result.get("scheduler_late_count")),
            "generator_late_rejected": result.get("generator_late_rejected"),
            "aborted_schedule_rejected": result.get("aborted_schedule_rejected"),
            "window_frames_per_second": completed / seconds if valid_seconds else None,
            "window_records": result.get("window_records"), "window_payload_bytes": result.get("window_payload_bytes"),
            "window_records_per_second": result.get("window_records", 0) / seconds if valid_seconds else None,
            "window_payload_mib_per_second": result.get("window_payload_bytes", 0) / seconds / 1024**2 if valid_seconds else None,
            "completed_frames": result.get("completed_frames"),
            "offered": result.get("offered"), "admitted": result.get("admitted"),
            "rejected": result.get("rejected"), "failed": result.get("failed"),
            "timedout": result.get("timedout"), "unresolved": result.get("unresolved"),
            "misses_1ms": result.get("misses_1ms", result.get("deadline_misses_1ms")),
            "misses_5ms": result.get("misses_5ms", result.get("deadline_misses_5ms")),
            "misses_10ms": result.get("misses_10ms", result.get("deadline_misses_10ms")),
            "cohort_seconds": result.get("cohort_seconds"), "drain_seconds": result.get("drain_seconds"),
            "drain_limit_seconds": result.get("drain_limit_seconds", result.get("configured_drain_seconds")),
            "latency_samples": histogram.get("count"), "latency_overflow_60s": histogram.get("overflow_60s"),
            "p999_sample_gate_passed": histogram.get("count", 0) >= 1_000_000,
            "receive_stage_samples": receive.get("count"), "receive_stage_p50_ns": receive.get("p50_ns"),
            "processing_stage_samples": processing.get("count"), "processing_stage_p50_ns": processing.get("p50_ns"),
            "p50_ns": latency.get("p50"), "p99_ns": latency.get("p99"),
            "p999_ns": latency.get("p999"), "max_ns": latency.get("max"), "path": entry["path"]})
    if not rows:
        raise ValueError("No completed trials to report")
    with (args.directory / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Loopback benchmark report", "", "Every raw trial remains available in `results.json` and `trials.csv`.",
             "Smoke/pilot numbers are validation/exploration, not a language ranking. Rates are frame completion rates; percentiles are per batch.", "",
             "Only verified, generator-adequate trials without failed/unresolved requests enter the comparisons below. Unsustainable but correctly reconciled overload trials remain eligible and are counted explicitly.",
             f"Eligible trials: {sum(row['comparison_eligible'] for row in rows)} / {len(rows)}. Missing adequacy flags are unverified, not assumed passing.", "",
             "| Excluded trial | Reasons |",
             "| --- | --- |"]
    for row in rows:
        if not row["comparison_eligible"]:
            lines.append(f"| {row['path']} | {row['exclusion_reasons']} |")
    lines += ["", "| Server | Client | Mode | Bytes/frame target | Connections | Offered frames/s | Phase | Arrival | Trials | Unsustainable trials | Median completed frames/s | Min–max completed frames/s |",
              "| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |"]
    groups = {}
    display_fields = ["client", "mode", "frame_bytes", "connections", "offered_rate", "phase", "arrival"]
    # Keep unlike corpora, durations and admission budgets out of one comparison.
    identity_fields = display_fields + ["window_seconds", "warmup", "window", "seed", "corpus_sha256", "builds", "cpu_masks"]
    for row in rows:
        if row["comparison_eligible"]:
            key = tuple(row[k] for k in ["server"] + identity_fields)
            groups.setdefault(key, []).append(row)
    for key, members in sorted(groups.items(), key=lambda item: str(item[0])):
        rates = [row["window_frames_per_second"] for row in members]
        overload = sum(row["sustainable"] is False for row in members)
        lines.append("| " + " | ".join(map(str, key[:8])) + f" | {len(rates)} | {overload} | {statistics.median(rates):.2f} | {min(rates):.2f}–{max(rates):.2f} |")
    lines += ["", "Latency percentiles are kept per trial. No percentile averages or pooled-tail precision claims are made.",
              "Inspect rejection/failure counts alongside latency; success-only latency cannot describe dropped demand.",
              "Server and client share CPU cache, DRAM, power and Windows scheduling. Diagnose client headroom before declaring a server limit."]
    paired = {}
    for row in rows:
        key = tuple(row[k] for k in identity_fields)
        paired.setdefault(key, {}).setdefault(row["pair"], []).append(row)
    lines += ["", "## Paired completion-rate ratios", "",
              "Ratio is C# / C++; 1 means equal observed frame completion rates. At fixed load below capacity, both can deliver the same rate even with different latency/CPU cost.",
              "Bootstrap intervals resample independent trial pairs, not individual requests. Seven pairs give only a rough uncertainty estimate.", "",
              "A pair is included only when both members pass. Duplicate or incomplete pairs are excluded. Differences in corpus, duration, warmup, window, seed or arrival shape form separate groups.", "",
              "| Client / mode / bytes / connections / offered rate / phase / arrival | Pairs | Median ratio | Bootstrap 95% interval |",
              "| --- | ---: | ---: | --- |"]
    rng = random.Random(42)
    for key, pairs in sorted(paired.items(), key=lambda item: str(item[0])):
        ratios = []
        for members in pairs.values():
            if len(members) != 2 or not all(row["comparison_eligible"] for row in members):
                continue
            values = {row["server"]: row["window_frames_per_second"] for row in members}
            if "csharp" in values and values.get("cpp", 0) > 0:
                ratios.append(values["csharp"] / values["cpp"])
        if not ratios:
            continue
        interval = "insufficient independent pairs"
        if len(ratios) >= 7:
            samples = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(2000))
            interval = f"{samples[49]:.4f}–{samples[1949]:.4f}"
        lines.append(f"| {' / '.join(map(str, key[:7]))} | {len(ratios)} | {statistics.median(ratios):.4f} | {interval} |")
    (args.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.directory / "report.md")


if __name__ == "__main__":
    main()
