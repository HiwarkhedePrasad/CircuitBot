#!/usr/bin/env python
"""Benchmark runner — establish baseline metrics for every circuit.

Usage:
    python -m agent.benchmark_runner                     # run all, print table
    python -m agent.benchmark_runner --html              # + generate HTML report
    python -m agent.benchmark_runner --csv               # + save CSV
    python -m agent.benchmark_runner --name rc_filter    # run single benchmark
    python -m agent.benchmark_runner --save-baseline     # overwrite stored baselines
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from agent.layout_engine import (
    BackendLayoutEngine,
    MAX_WIRE_MANHATTAN,
    MAX_COLLISIONS,
    PLACEMENT_MODE,
)


HERE = Path(__file__).resolve().parent
BENCHMARKS_DIR = HERE / "benchmarks"
RESULTS_DIR = HERE / "benchmark_results"


def _make_missing_msg() -> str:
    return "N/A"


# ── Metric helpers ────────────────────────────────────────────────────────


def _count_overlaps(components: list[dict]) -> int:
    count = 0
    for i, a in enumerate(components):
        ab = a["bbox"]
        ax1, ay1 = a["x"] + ab["x"], a["y"] + ab["y"]
        ax2, ay2 = ax1 + ab["w"], ay1 + ab["h"]
        for b in components[i + 1:]:
            bb = b["bbox"]
            bx1, by1 = b["x"] + bb["x"], b["y"] + bb["y"]
            bx2, by2 = bx1 + bb["w"], by1 + bb["h"]
            if ax2 > bx1 and ax1 < bx2 and ay2 > by1 and ay1 < by2:
                count += 1
    return count


def _count_crossings(routes: list[dict]) -> int:
    """Orientation-based segment intersection (same as layout_engine)."""
    segments: list[tuple[float, float, float, float]] = []
    for r in routes:
        pts = r.get("points") or r.get("path", [])
        if pts and isinstance(pts[0], dict):
            pts = [(p["x"], p["y"]) for p in pts]
        for i in range(len(pts) - 1):
            segments.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))

    def _orient(ax, ay, bx, by, cx, cy) -> int:
        v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(v) < 1e-12:
            return 0
        return 1 if v > 0 else -1

    def _on_seg(ax, ay, bx, by, cx, cy) -> bool:
        return (min(ax, bx) <= cx <= max(ax, bx) and
                min(ay, by) <= cy <= max(ay, by))

    count = 0
    for i, seg_a in enumerate(segments):
        ax1, ay1, ax2, ay2 = seg_a
        for seg_b in segments[i + 1:]:
            bx1, by1, bx2, by2 = seg_b
            if (abs(ax2 - bx1) < 1e-9 and abs(ay2 - by1) < 1e-9) or \
               (abs(ax1 - bx2) < 1e-9 and abs(ay1 - by2) < 1e-9):
                continue
            if (abs(ax1 - bx1) < 1e-9 and abs(ay1 - by1) < 1e-9) or \
               (abs(ax2 - bx2) < 1e-9 and abs(ay2 - by2) < 1e-9):
                continue
            o1 = _orient(ax1, ay1, ax2, ay2, bx1, by1)
            o2 = _orient(ax1, ay1, ax2, ay2, bx2, by2)
            o3 = _orient(bx1, by1, bx2, by2, ax1, ay1)
            o4 = _orient(bx1, by1, bx2, by2, ax2, ay2)
            if o1 != o2 and o3 != o4:
                count += 1
    return count


def _total_wire_length(routes: list[dict]) -> float:
    total = 0.0
    for r in routes:
        pts = r.get("points") or r.get("path", [])
        if pts and isinstance(pts[0], dict):
            pts = [(p["x"], p["y"]) for p in pts]
        for i in range(len(pts) - 1):
            total += abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
    return round(total, 2)


def _count_bends(routes: list[dict]) -> int:
    bends = 0
    for r in routes:
        pts = r.get("points") or r.get("path", [])
        if pts and isinstance(pts[0], dict):
            pts = [(p["x"], p["y"]) for p in pts]
        for i in range(1, len(pts) - 1):
            dx1 = pts[i][0] - pts[i - 1][0]
            dy1 = pts[i][1] - pts[i - 1][1]
            dx2 = pts[i + 1][0] - pts[i][0]
            dy2 = pts[i + 1][1] - pts[i][1]
            if abs(dx1 - dx2) > 1e-3 or abs(dy1 - dy2) > 1e-3:
                bends += 1
    return bends


def _alignment_score(components: list[dict]) -> float:
    """Count how many components share X or Y with a neighbor."""
    if len(components) < 2:
        return 0.0
    aligned = 0
    total_pairs = 0
    for i, a in enumerate(components):
        for b in components[i + 1:]:
            total_pairs += 1
            if abs(a["x"] - b["x"]) < 1.27:
                aligned += 1
            elif abs(a["y"] - b["y"]) < 1.27:
                aligned += 1
    if total_pairs == 0:
        return 1.0
    return round(aligned / total_pairs, 4)


# ── Circuit runner ────────────────────────────────────────────────────────


def run_benchmark(circuit: dict) -> dict:
    """Run placement + routing for a single circuit.

    Returns a dict of all metrics plus timing.
    """
    comps = circuit["components"]
    netlist = circuit["netlist"]
    pin_matrix = circuit["pin_matrix"]

    # Build engine
    engine = BackendLayoutEngine()
    for c in comps:
        engine.add_component(
            c["ref_des"], c["ops"], c["category"],
            c.get("id_str", ""), c.get("for_component", ""),
        )

    n_components = len(engine.components)

    # Phase 1: placement
    t0 = time.perf_counter()
    engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)
    placement_time = time.perf_counter() - t0

    placements = engine.get_placements()
    overlaps = _count_overlaps(engine.components)

    # Phase 2: routing
    t0 = time.perf_counter()
    routes, dropped_pairs = engine.route_traces(netlist, pin_matrix)
    routing_time = time.perf_counter() - t0

    # Metrics
    crossings = _count_crossings(routes)
    wire_len = _total_wire_length(routes)
    bends = _count_bends(routes)
    alignment = _alignment_score(engine.components)
    n_wires = len(routes)
    n_dropped = len(dropped_pairs)

    return {
        "name": circuit.get("name", "unknown"),
        "description": circuit.get("description", ""),
        "placement_time_ms": round(placement_time * 1000, 1),
        "routing_time_ms": round(routing_time * 1000, 1),
        "total_time_ms": round((placement_time + routing_time) * 1000, 1),
        "n_components": n_components,
        "n_wires": n_wires,
        "n_dropped": n_dropped,
        "overlaps": overlaps,
        "crossings": crossings,
        "wire_length_mm": wire_len,
        "bends": bends,
        "alignment": alignment,
        "failed": False,
    }


def run_single(name: str) -> dict | None:
    """Run a single benchmark by module name."""
    from agent.benchmarks import _discover_circuits

    for modname, load_fn in _discover_circuits():
        if modname == name:
            circuit = load_fn()
            result = run_benchmark(circuit)
            return result
    print(f"  [ERROR] No benchmark found: {name}")
    return None


# ── Results persistence ───────────────────────────────────────────────────


def _results_path() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / "results.json"


def load_results() -> dict:
    path = _results_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_results(results: dict):
    path = _results_path()
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")


# ── Console output ────────────────────────────────────────────────────────


def _fmt(val: Any, width: int = 12) -> str:
    s = str(val) if val is not None else "N/A"
    if isinstance(val, float):
        s = f"{val:.1f}"
    return s.rjust(width)


def print_table(baselines: dict, currents: dict):
    names = sorted(set(list(baselines.keys()) + list(currents.keys())))

    header = f"{'Benchmark':<20} {'crossings':>9} {'wire_len':>9} {'bends':>6} {'overlaps':>8} {'align':>6} {'drop':>5} {'place_ms':>9} {'route_ms':>9}"
    sep = "-" * len(header)

    print(f"\n  Benchmark mode: {PLACEMENT_MODE}")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print()
    print("  " + header)
    print("  " + sep)

    for name in names:
        c = currents.get(name)
        b = baselines.get(name)

        if c is None:
            print(f"  {name:<20}  {'[no run]':>9}")
            continue

        cross = c["crossings"]
        wlen = c["wire_length_mm"]
        bends = c["bends"]
        ovlp = c["overlaps"]
        align = c["alignment"]
        drop = c["n_dropped"]
        pt = c["placement_time_ms"]
        rt = c["routing_time_ms"]

        row = (
            f"  {name:<20}"
            f" {cross:>9}"
            f" {wlen:>9.1f}"
            f" {bends:>6}"
            f" {ovlp:>8}"
            f" {align:>6.2f}"
            f" {drop:>5}"
            f" {pt:>9.1f}"
            f" {rt:>9.1f}"
        )
        print(row)

    print()


def print_comparison(baselines: dict, currents: dict):
    """Print before/after comparison for each benchmark."""
    names = sorted(set(list(baselines.keys()) + list(currents.keys())))

    header = f"{'Benchmark':<20} {'crossings':>14} {'wire_len':>14} {'bends':>12} {'overlaps':>12} {'align':>12}"
    sep = "-" * len(header)

    print("  " + header)
    print("  " + sep)

    for name in names:
        c = currents.get(name)
        b = baselines.get(name)

        if c is None:
            continue

        parts = [f"  {name:<20}"]

        for key, label in [("crossings", ""), ("wire_length_mm", "mm"),
                           ("bends", ""), ("overlaps", ""), ("alignment", "")]:
            cv = c.get(key, 0)
            bv = b.get(key, 0) if b else None
            if bv is not None and bv != 0:
                pct = (cv - bv) / bv * 100
                arrow = "↓" if pct < 0 else "↑" if pct > 0 else "="
                parts.append(f" {cv:>6.1f}{label} {arrow}{abs(pct):5.1f}%")
            else:
                parts.append(f" {cv:>6.1f}{label}      -")

        print("".join(parts))

    print()


# ── HTML report ───────────────────────────────────────────────────────────


def generate_html(baselines: dict, currents: dict, filename: str | None = None):
    names = sorted(set(list(baselines.keys()) + list(currents.keys())))

    if filename is None:
        filename = f"benchmark_report_{datetime.now():%Y%m%d_%H%M%S}.html"

    rows_html = ""
    for name in names:
        c = currents.get(name)
        b = baselines.get(name)
        if c is None:
            continue

        total_improvement = 0.0
        metrics_html = ""
        for key, label, unit, higher_better in [
            ("crossings", "Crossings", "", False),
            ("wire_length_mm", "Wire Length", "mm", False),
            ("bends", "Bends", "", False),
            ("overlaps", "Overlaps", "", False),
            ("alignment", "Alignment", "", True),
            ("n_dropped", "Dropped Wires", "", False),
            ("placement_time_ms", "Placement Time", "ms", False),
            ("routing_time_ms", "Routing Time", "ms", False),
        ]:
            cv = c.get(key, 0)
            bv = b.get(key, 0) if b else None
            if bv is not None and bv != 0:
                if higher_better:
                    pct = (cv - bv) / bv * 100 * -1  # invert for "improvement"
                else:
                    pct = (bv - cv) / bv * 100
                color = "#22c55e" if pct >= 0 else "#ef4444"
                arrow = "↑" if pct > 0 else "↓" if pct < 0 else "→"
                metric_str = (
                    f"<tr>"
                    f"<td>{label}</td>"
                    f"<td>{bv} {unit}</td>"
                    f"<td>{cv} {unit}</td>"
                    f"<td style='color:{color}'>{arrow} {abs(pct):.1f}%</td>"
                    f"</tr>"
                )
                total_improvement += pct
            else:
                metric_str = (
                    f"<tr>"
                    f"<td>{label}</td>"
                    f"<td>-</td>"
                    f"<td>{cv} {unit}</td>"
                    f"<td>-</td>"
                    f"</tr>"
                )
            metrics_html += metric_str

        avg_improvement = total_improvement / 8.0 if b else 0.0
        summary_color = "#22c55e" if avg_improvement > 5 else (
            "#eab308" if avg_improvement > 0 else "#ef4444"
        )

        rows_html += f"""
        <div class="card">
            <div class="card-header">
                <span class="card-title">{name}</span>
                <span class="badge" style="background:{summary_color}">
                    {avg_improvement:+.1f}% avg
                </span>
            </div>
            <div class="card-body">
                <p style="color:#94a3b8; margin:0 0 12px 0">{c.get('description', '')}</p>
                <table>
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>Baseline</th>
                            <th>Current</th>
                            <th>Change</th>
                        </tr>
                    </thead>
                    <tbody>
                        {metrics_html}
                    </tbody>
                </table>
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CircuitBot Benchmark Report</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 32px;
}}
h1 {{ font-size: 24px; margin-bottom: 8px; }}
h2 {{ font-size: 14px; color: #64748b; margin-bottom: 24px; font-weight: 400; }}
.summary {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 32px;
}}
.stat {{
    background: #1e293b; border-radius: 8px; padding: 16px;
}}
.stat-label {{ font-size: 12px; color: #64748b; }}
.stat-value {{ font-size: 28px; font-weight: 700; color: #3b82f6; }}
.card {{
    background: #1e293b; border-radius: 8px; margin-bottom: 16px; overflow: hidden;
}}
.card-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px 20px; border-bottom: 1px solid #334155;
}}
.card-title {{ font-size: 16px; font-weight: 600; }}
.badge {{
    font-size: 12px; padding: 4px 10px; border-radius: 999px;
    color: white; font-weight: 600;
}}
.card-body {{ padding: 16px 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; font-size: 12px; color: #64748b; text-transform: uppercase;
      padding: 8px 12px; border-bottom: 1px solid #334155; }}
td {{ padding: 8px 12px; font-size: 14px; border-bottom: 1px solid #1e293b; font-variant-numeric: tabular-nums; }}
tr:hover td {{ background: #1a2332; }}
.footer {{ margin-top: 32px; font-size: 12px; color: #475569; }}
</style>
</head>
<body>
<h1>CircuitBot Benchmark Report</h1>
<h2>Mode: {PLACEMENT_MODE}  |  {datetime.now():%Y-%m-%d %H:%M:%S}</h2>
<div class="summary">
    <div class="stat">
        <div class="stat-label">Benchmarks</div>
        <div class="stat-value">{len(names)}</div>
    </div>
    <div class="stat">
        <div class="stat-label">Avg Crossings</div>
        <div class="stat-value">{sum(c.get('crossings',0) for c in currents.values() if c) / max(len(currents),1):.1f}</div>
    </div>
    <div class="stat">
        <div class="stat-label">Avg Wire Length</div>
        <div class="stat-value">{sum(c.get('wire_length_mm',0) for c in currents.values() if c) / max(len(currents),1):.0f}mm</div>
    </div>
    <div class="stat">
        <div class="stat-label">Total Runtime</div>
        <div class="stat-value">{sum(c.get('total_time_ms',0) for c in currents.values() if c) / 1000:.2f}s</div>
    </div>
</div>
{rows_html}
<div class="footer">CircuitBot v0.8.2  |  Benchmark runner</div>
</body>
</html>"""

    report_path = _project_root / filename
    report_path.write_text(html, encoding="utf-8")
    print(f"  [HTML] Report saved to {report_path}")
    return str(report_path)


# ── Main ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="CircuitBot benchmark runner")
    parser.add_argument("--name", help="Run a single benchmark by name")
    parser.add_argument("--html", action="store_true", help="Generate HTML report")
    parser.add_argument("--csv", action="store_true", help="Save results as CSV")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save current results as the new baseline")
    parser.add_argument("--compare", action="store_true",
                        help="Compare against stored baseline")
    args = parser.parse_args()

    from agent.benchmarks import _discover_circuits

    print(f"  CircuitBot Benchmark Runner")
    print(f"  {'=' * 50}")

    # Load baselines
    baselines = load_results()
    has_baseline = bool(baselines)
    if has_baseline:
        print(f"  Loaded {len(baselines)} stored baseline(s)")

    # Run benchmarks
    currents: dict[str, dict] = {}

    if args.name:
        result = run_single(args.name)
        if result:
            currents[args.name] = result
    else:
        for modname, load_fn in _discover_circuits():
            print(f"  Running: {modname}...", end=" ")
            try:
                circuit = load_fn()
                result = run_benchmark(circuit)
                currents[modname] = result
                print(f"OK  ({result['total_time_ms']:.0f}ms, {result['n_components']} components, {result['n_wires']} wires)")
            except Exception as e:
                print(f"FAILED — {e}")
                currents[modname] = {
                    "name": modname, "failed": True,
                    "error": str(e),
                }

    # Print results table
    if args.compare and has_baseline:
        print("\n  Comparison vs baseline:")
        print_comparison(baselines, currents)
    else:
        print_table({}, currents)

    # Save results
    if args.save_baseline:
        save_results(currents)
        print(f"  Saved {len(currents)} result(s) as new baseline")

    if args.csv:
        csv_path = _project_root / f"benchmarks_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "name", "crossings", "wire_length_mm", "bends", "overlaps",
                "alignment", "n_dropped", "placement_time_ms", "routing_time_ms",
            ])
            for name, r in sorted(currents.items()):
                writer.writerow([
                    name, r.get("crossings", 0), r.get("wire_length_mm", 0),
                    r.get("bends", 0), r.get("overlaps", 0),
                    r.get("alignment", 0), r.get("n_dropped", 0),
                    r.get("placement_time_ms", 0), r.get("routing_time_ms", 0),
                ])
        print(f"  [CSV] Saved to {csv_path}")

    if args.html:
        if has_baseline:
            generate_html(baselines, currents)
        else:
            generate_html({}, currents)

    # Summary
    ok = [n for n, r in currents.items() if not r.get("failed")]
    fail = [n for n, r in currents.items() if r.get("failed")]
    total_wires = sum(r.get("n_wires", 0) for r in currents.values() if not r.get("failed"))
    total_dropped = sum(r.get("n_dropped", 0) for r in currents.values() if not r.get("failed"))
    total_crossings = sum(r.get("crossings", 0) for r in currents.values() if not r.get("failed"))

    print(f"  {'=' * 50}")
    print(f"  {len(ok)}/{len(currents)} benchmarks passed")
    if fail:
        print(f"  Failed: {', '.join(fail)}")
    print(f"  Total wires: {total_wires}  |  Dropped: {total_dropped}  |  Crossings: {total_crossings}")
    print()


if __name__ == "__main__":
    main()
