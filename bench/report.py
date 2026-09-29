"""Per-trial CSV plus grouped tables from a campaign's results.json.

Percentiles are never averaged or pooled: tables show the lowest–highest per-trial value, and
medians across trials for throughput. Latency percentiles are taken over *offered* demand, with
rejected, failed and unresolved requests counted as infinitely late ("miss")."""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path

# Generator and client adequacy, policy v2 (docs/benchmarks/methodology.md), declared before
# the rework campaign.
LATENESS_P99_LIMIT_NS = 100_000
LATE_OVER_1MS_FRACTION = 0.001
CLIENT_CPU_LIMIT = 0.85


def client_cpu(result):
    """Client CPU over the measured phase as a share of its logical CPUs."""
    res = (result or {}).get("resources", {})
    return (res.get("cpu_seconds") or 0) / max(1e-9, (res.get("seconds") or 0) * (res.get("logical_cpus") or 1))


def offered_quantile(histogram, misses, q):
    """Bucket upper bound at rank ceil(q * (acknowledged + misses)); inf when the rank is a miss."""
    total = (histogram.get("count") or 0) + misses
    if not total:
        return None
    rank, seen = math.ceil(q * total), 0
    for upper, count in histogram.get("buckets", []):
        seen += count
        if seen >= rank:
            return upper / 1e6
    return math.inf


def row(entry):
    cell, r, s = entry["cell"], entry.get("result") or {}, entry.get("server_result") or {}
    build = s.get("build", {})
    out = {"ordinal": entry["ordinal"], "phase": cell["phase"], "cell": cell["name"], "group": cell.get("group", ""),
           "title": cell.get("title", cell["name"]), "kind": cell["kind"], "mode": cell["mode"],
           "server": entry["server"], "client": entry["client"], "repetition": cell["repetition"],
           "corpus": entry["corpus"]["name"], "profile": entry["corpus"].get("profile"),
           "connections": cell["connections"], "window": cell["window"], "rate": cell["rate"],
           "arrival": cell["arrival"], "socket_buffer": cell["socket_buffer"], "retain_batches": cell["retain_batches"],
           "duration": cell["duration"], "warmup": cell["warmup"], "runner_error": entry["runner_error"],
           "background_cpu_fraction": entry["background_cpu_seconds"] / max(1e-9, entry["elapsed_seconds"] * entry["logical_cpus"]),
           "server_sibling_busy_fraction": entry.get("server_sibling_busy_fraction"),
           "server_gc": build.get("server_gc"), "gc_dynamic_adaptation": build.get("gc_dynamic_adaptation"), "builds": json.dumps(entry["builds"], sort_keys=True),
           "path": entry["path"]}
    if cell["kind"] == "process":
        res = s.get("resources", {})
        out |= {"valid": s.get("valid") is True and not entry["runner_error"],
                "records_per_second": s.get("records_per_second"), "mib_per_second": s.get("payload_mib_per_second"),
                "process_cpu_seconds": res.get("cpu_seconds"), "process_seconds": s.get("seconds"),
                "allocated_bytes_per_record": (res.get("allocated_bytes") or 0) / max(1, s.get("records") or 0)}
        out["exclusion"] = "" if out["valid"] else "invalid"
        return out
    counts, window, latency = r.get("counts", {}), r.get("window", {}), r.get("latency", {})
    scheduled, generator = latency.get("scheduled", {}), r.get("generator", {})
    measured, gc = s.get("measured") or {}, (s.get("measured") or {}).get("gc") or {}
    seconds, offered = window.get("seconds") or 0, counts.get("offered") or 0
    misses = sum(counts.get(k) or 0 for k in ("rejected", "failed", "unresolved"))
    lateness = generator.get("lateness", {})
    valid = (r.get("valid") is True and s.get("valid") is True and not entry["runner_error"]
             and measured.get("complete") is True and not counts.get("failed") and not counts.get("unresolved"))
    scheduled_run = r.get("load_model") == "scheduled"
    undispatched = (counts.get("generator_late_rejected") or 0) + (counts.get("aborted_schedule_rejected") or 0)
    late = generator.get("late_over_1ms") or 0
    reasons = [] if valid else ["invalid_or_unreconciled"]
    wanted = cell.get("server_env", {}).get("DOTNET_GCDynamicAdaptationMode")
    if wanted is not None and entry["server"] == "csharp" and str(build.get("gc_dynamic_adaptation")) != wanted:
        reasons.append("gc_setting_not_applied")
    # Transport controls are declared client-bound (methodology): exempt, and read as lower bounds.
    if client_cpu(r) > CLIENT_CPU_LIMIT and cell["mode"] != "transport":
        reasons.append("client_cpu_over_85pct")
    if scheduled_run:
        if undispatched:
            reasons.append("undispatched_demand")
        p99 = lateness.get("p99_ns")
        if (lateness.get("count") or 0) and (p99 is None or p99 > LATENESS_P99_LIMIT_NS):
            reasons.append("lateness_p99_over_100us")
        if late > LATE_OVER_1MS_FRACTION * max(1, offered):
            reasons.append("late_over_1ms_above_0.1pct")
    records = measured.get("records") or 0
    ms = lambda ns: None if ns is None else ns / 1e6  # noqa: E731
    out |= {"valid": valid, "load_model": r.get("load_model"),
            "records_per_second": (window.get("records") or 0) / seconds if seconds else None,
            "mib_per_second": (window.get("payload_bytes") or 0) / seconds / 1048576 if seconds else None,
            "offered": offered, "admitted": counts.get("admitted"), "rejected": counts.get("rejected"),
            "acknowledged": counts.get("acknowledged"), "failed": counts.get("failed"),
            "unresolved": counts.get("unresolved"), "timedout": counts.get("timedout"),
            "p50_ms": offered_quantile(scheduled, misses, .5), "p99_ms": offered_quantile(scheduled, misses, .99),
            "p999_ms": offered_quantile(scheduled, misses, .999), "max_acknowledged_ms": ms(scheduled.get("max_ns")),
            "p99_acknowledged_ms": ms(scheduled.get("p99_ns")), "latency_samples": scheduled.get("count"),
            "client_delay_p99_ms": ms(latency.get("client_delay", {}).get("p99_ns")),
            "misses_over_1ms": r.get("misses", {}).get("over_1ms"), "misses_over_5ms": r.get("misses", {}).get("over_5ms"),
            "lateness_p99_us": (lateness.get("p99_ns") or 0) / 1000 if scheduled_run else None,
            "lateness_max_us": (lateness.get("max_ns") or 0) / 1000 if scheduled_run else None,
            "late_over_1ms": late if scheduled_run else None, "client_cpu_utilization": client_cpu(r),
            "strict_v1_adequate": (late == 0 and not undispatched and valid) if scheduled_run else None,
            "server_cores_used": (measured.get("cpu_seconds") or 0) / max(1e-9, measured.get("seconds") or 0),
            "server_cpu_us_per_frame": 1e6 * (measured.get("cpu_seconds") or 0) / max(1, measured.get("frames") or 0),
            "server_cpu_ns_per_record": 1e9 * (measured.get("cpu_seconds") or 0) / records if records else None,
            # C#: managed bytes (GC.GetTotalAllocatedBytes); C++: global operator new bytes.
            "allocated_bytes_per_record": (measured.get("allocated_bytes") or 0) / records if records else None,
            "allocations_per_frame": ((measured.get("allocations") or 0) / max(1, measured.get("frames") or 0)
                                      if measured.get("allocations") is not None else None),
            "gc_gen0": gc.get("gen0"), "gc_gen1": gc.get("gen1"), "gc_gen2": gc.get("gen2"),
            "gc_pause_ms": 1000 * gc["pause_seconds"] if "pause_seconds" in gc else None,
            "server_peak_working_set_mib": (s.get("lifetime", {}).get("peak_working_set_bytes") or 0) / 1048576,
            "owned_capacity_peak_mib": (s.get("retention", {}).get("owned_capacity_peak_per_connection") or 0) / 1048576,
            "receive_stage_p50_us": (s.get("stages", {}).get("receive", {}).get("p50_ns") or 0) / 1000,
            "processing_stage_p50_us": (s.get("stages", {}).get("processing", {}).get("p50_ns") or 0) / 1000,
            "stage_samples": s.get("stages", {}).get("processing", {}).get("count")}
    out["exclusion"] = ";".join(reasons)
    return out


def mark_pairs(rows):
    """A trial enters comparisons only if both members of its repetition pair are individually
    eligible; per-server medians then never mix unpaired trials."""
    pairs = {}
    for r in rows:
        pairs.setdefault((r["cell"], r["repetition"]), []).append(r)
    for members in pairs.values():
        paired = {m["server"] for m in members} == {"cpp", "csharp"}
        both_pass = all(not m["exclusion"] for m in members)
        for m in members:
            m["eligible"] = not m["exclusion"] and paired and both_pass
            if not m["exclusion"] and not m["eligible"]:
                m["exclusion"] = "partner_excluded" if paired else "unpaired"


def fmt(value, spec):
    return "miss" if value == math.inf else spec.format(value)


def span(values, spec="{:.2f}"):
    values = [v for v in values if v is not None]
    if not values:
        return "–"
    lo, hi = fmt(min(values), spec), fmt(max(values), spec)
    return lo if lo == hi else f"{lo}–{hi}"


def median(values, spec="{:.2f}"):
    values = [v for v in values if v is not None]
    return spec.format(statistics.median(values)) if values else "–"


def ratio_line(title, members, key, label):
    """Median of per-repetition C# / C++ ratios over eligible pairs, with a bootstrap interval. A pair
    where only one p99 is a miss stays in, as an infinite (or zero) ratio; pairs where both miss are counted."""
    pairs = {}
    for m in members:
        if m["eligible"]:
            pairs.setdefault(m["repetition"], {})[m["server"]] = m[key]
    ratios, both_missed = [], 0
    for p in pairs.values():
        if len(p) != 2 or p["cpp"] is None or p["csharp"] is None:
            continue
        if p["cpp"] == math.inf:
            both_missed += p["csharp"] == math.inf
            if p["csharp"] != math.inf:
                ratios.append(0.0)
        elif p["cpp"]:
            ratios.append(p["csharp"] / p["cpp"])
    if not ratios:
        return None
    show = lambda v: "∞ (C# miss)" if v == math.inf else f"{v:.3f}"  # noqa: E731
    interval = "–"
    if len(ratios) >= 5:
        rng = random.Random(7)
        boot = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(4000))
        interval = f"{show(boot[99])}–{show(boot[3899])}"
    note = f" ({both_missed} more pairs: both missed)" if both_missed else ""
    return f"| {title} | {label} | {len(ratios)}{note} | {show(statistics.median(ratios))} | {interval} |"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    entries = json.loads((args.directory / "results.json").read_text(encoding="utf-8"))
    rows = [row(e) for e in entries]
    mark_pairs(rows)
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (args.directory / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    groups = {}
    for r in rows:
        # Processing-only and socket trials have different columns, so they never share a table.
        groups.setdefault((r["group"], r["kind"]), {}).setdefault((r["title"], r["cell"]), []).append(r)
    name = {"cpp": "C++", "csharp": "C#"}
    lines = ["# Campaign report", "",
             f"{sum(r['eligible'] for r in rows)} of {len(rows)} trials eligible (both members of a repetition "
             "pair must pass). Throughput: median across eligible trials. Latency: lowest–highest per-trial "
             "percentile over offered requests from intended arrival; \"miss\" means the rank falls on "
             "rejected, failed or unresolved demand. Percentiles are never averaged. MiB = 1,048,576 bytes.", ""]
    for (group, kind), cells in groups.items():
        process = kind == "process"
        lines += [f"## {group or 'Ungrouped'}{' (processing only)' if process else ''}", ""]
        if process:
            lines += ["| Workload | Implementation | Eligible | M records/s | MiB/s | Range M records/s |",
                      "| --- | --- | ---: | ---: | ---: | ---: |"]
        else:
            lines += ["| Workload | Server | Eligible | M records/s | MiB/s | p50 ms | p99 ms | Samples/trial | "
                      "Rejected | Server cores used | Server CPU µs/frame | Peak RAM MiB |",
                      "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for (title, cell), members in cells.items():
            for server in ["cpp", "csharp"]:
                m = [r for r in members if r["server"] == server]
                ok = [r for r in m if r["eligible"]]
                if not m:
                    continue
                rate = median([r["records_per_second"] / 1e6 for r in ok if r["records_per_second"]], "{:.3f}")
                if process:
                    lines.append(f"| {title} | {name[server]} | {len(ok)}/{len(m)} | {rate} | "
                                 f"{median([r['mib_per_second'] for r in ok], '{:,.0f}')} | "
                                 f"{span([r['records_per_second'] / 1e6 for r in ok], '{:.3f}')} |")
                    continue
                lines.append(
                    f"| {title} | {name[server]} | {len(ok)}/{len(m)} | {rate} | "
                    f"{median([r['mib_per_second'] for r in ok], '{:,.0f}')} | {span([r['p50_ms'] for r in ok])} | "
                    f"{span([r['p99_ms'] for r in ok])} | {span([r['latency_samples'] for r in ok], '{:,.0f}')} | "
                    f"{span([r['rejected'] for r in ok], '{:,.0f}')} | {median([r['server_cores_used'] for r in ok])} | "
                    f"{median([r['server_cpu_us_per_frame'] for r in ok], '{:.1f}')} | "
                    f"{span([r['server_peak_working_set_mib'] for r in ok], '{:.0f}')} |")
        lines.append("")
    lines += ["## Memory, allocation and GC (server measured interval)", "",
              "Allocated per record: C# managed bytes (GC.GetTotalAllocatedBytes); C++ global operator new bytes. "
              "Owned capacity: retained plus scratch row/text storage per connection. GC counts are collections in "
              "the interval, not independent categories.", "",
              "| Workload | Server | Allocated B/record | Allocations/frame | GC gen0 / gen1 / gen2 | GC pause ms | Owned capacity MiB |",
              "| --- | --- | ---: | ---: | --- | ---: | ---: |"]
    for (group, kind), cells in groups.items():
        for (title, cell), members in cells.items():
            for server in ["cpp", "csharp"]:
                ok = [r for r in members if r["server"] == server and r["eligible"] and r["kind"] == "tcp"]
                if ok:
                    gcs = "–" if ok[0]["gc_gen0"] is None else " / ".join(span([r[k] for r in ok], "{:,.0f}") for k in ("gc_gen0", "gc_gen1", "gc_gen2"))
                    lines.append(f"| {title} | {name[server]} | {span([r['allocated_bytes_per_record'] for r in ok], '{:.1f}')} | "
                                 f"{span([r['allocations_per_frame'] for r in ok], '{:.1f}')} | {gcs} | "
                                 f"{span([r['gc_pause_ms'] for r in ok], '{:.1f}')} | {span([r['owned_capacity_peak_mib'] for r in ok], '{:.1f}')} |")
    lines += ["", "## Load-generator and environment checks", "",
              "Client CPU is the measured-phase share of the client's cores. Lateness is generator dispatch minus intended "
              "time (scheduled only). Strict v1 is the first-pass rule (no arrival over 1 ms late), reported but not used. "
              "Sibling busy is foreign CPU on the idle SMT siblings of the server's cores.", "",
              "| Workload | Server | Client CPU % | Client delay p99 ms | Lateness p99 µs | Late >1 ms | Strict v1 pass | Sibling busy % |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for (group, kind), cells in groups.items():
        for (title, cell), members in cells.items():
            for server in ["cpp", "csharp"]:
                m = [r for r in members if r["server"] == server and r["kind"] == "tcp"]
                if m:
                    strict = [r["strict_v1_adequate"] for r in m if r["strict_v1_adequate"] is not None]
                    lines.append(f"| {title} | {name[server]} | {span([100 * r['client_cpu_utilization'] for r in m], '{:.0f}')} | "
                                 f"{span([r['client_delay_p99_ms'] for r in m])} | {span([r['lateness_p99_us'] for r in m], '{:.0f}')} | "
                                 f"{span([r['late_over_1ms'] for r in m], '{:,.0f}')} | "
                                 f"{f'{sum(strict)}/{len(strict)}' if strict else '–'} | "
                                 f"{span([100 * (r['server_sibling_busy_fraction'] or 0) for r in m], '{:.1f}')} |")
    lines += ["", "## Paired C# / C++ ratios", "",
              "Per-repetition ratios, median across eligible pairs; bootstrap 95% interval over pairs from five pairs up "
              "(rough). Throughput ratios are shown only where throughput is not fixed by the offered rate; paced cells "
              "compare p99 latency instead (above 1 means C# was slower).", "",
              "| Workload | Metric | Pairs | Median ratio | Bootstrap interval |", "| --- | --- | ---: | ---: | --- |"]
    for (group, kind), cells in groups.items():
        for (title, cell), members in cells.items():
            paced = any(r.get("load_model") == "scheduled" for r in members)
            line = (ratio_line(title, members, "p99_ms", "p99 latency") if paced
                    else ratio_line(title, members, "mib_per_second", "MiB/s (client-bound)")
                    if members[0]["mode"] == "transport"
                    else ratio_line(title, members, "records_per_second", "records/s"))
            if line:
                lines.append(line)
    lines += ["", "## Excluded or failed trials", "", "| Trial | Reasons | Runner error |", "| --- | --- | --- |"]
    lines += [f"| {r['path']} | {r['exclusion']} | {r['runner_error'] or ''} |" for r in rows if not r["eligible"]]
    (args.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.directory / "report.md")


if __name__ == "__main__":
    main()
