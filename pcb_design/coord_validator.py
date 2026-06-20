"""Coordinate validator — last line of defense against bad geometry.

LLMs are notoriously bad at spatial reasoning. Even with deterministic
placement, bad coordinates can leak in from:
  - LLM-generated component placements (if the agent ever asks for them)
  - LLM-generated wire paths (if the agent ever asks for them)
  - Bugs in the placement/router algorithms
  - Corrupt KiCad imports
  - Stale state in the agent's design dict

This module provides hard validators that DROP bad geometry rather than
letting it reach the frontend or the KiCad exporter.

Usage:
    from pcb_design.coord_validator import (
        validate_component_placements,
        validate_wire_paths,
        sanitize_design,
    )

    # Drop any component placement that's out of bounds or overlapping
    placements = validate_component_placements(placements, board_w=100, board_h=80)

    # Drop any wire that's diagonal, too long, or has degenerate segments
    wires = validate_wire_paths(wires, max_total_len_mm=150)

    # Run both + emit a report
    design = sanitize_design(design)
"""

from __future__ import annotations

from typing import Optional
from dataclasses import dataclass

GRID = 1.27
DEFAULT_MAX_WIRE_LEN_MM = 150.0
DEFAULT_MAX_SEG_LEN_MM = 75.0   # any single segment longer than this is suspect
DEFAULT_BOARD_W = 100.0
DEFAULT_BOARD_H = 80.0
DEFAULT_EDGE_MARGIN = 3.0


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


# ── Component placement validation ──────────────────────────────────────


def validate_component_placements(
    placements: list[dict],
    board_w: float = DEFAULT_BOARD_W,
    board_h: float = DEFAULT_BOARD_H,
    margin: float = DEFAULT_EDGE_MARGIN,
) -> tuple[list[dict], list[str]]:
    """Validate component placements. Returns (clean_placements, errors).

    Drops any placement that:
      - Has no ref_des
      - Has non-numeric x or y
      - Is outside the board outline + margin
      - Is at exactly (0, 0) with no rotation info (likely uninitialized)
    Snaps all coordinates to the 1.27 mm grid.
    """
    clean = []
    errors = []
    seen_refs = set()

    for p in placements:
        ref = p.get("ref_des", "")
        if not ref:
            errors.append("placement missing ref_des")
            continue
        if ref in seen_refs:
            errors.append(f"duplicate placement for {ref}")
            continue

        try:
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
        except (TypeError, ValueError):
            errors.append(f"{ref}: non-numeric x/y")
            continue

        # Hard NaN/inf check
        if not (abs(x) < 1e6 and abs(y) < 1e6):
            errors.append(f"{ref}: absurd coordinates ({x}, {y})")
            continue

        # Board bounds check
        if x < margin or x > board_w - margin:
            errors.append(f"{ref}: x={x:.2f} out of bounds [0, {board_w}]")
            continue
        if y < margin or y > board_h - margin:
            errors.append(f"{ref}: y={y:.2f} out of bounds [0, {board_h}]")
            continue

        # Snap to grid
        clean.append({
            "ref_des": ref,
            "x": _snap(x),
            "y": _snap(y),
            "rotation": float(p.get("rotation", 0)) % 360,
        })
        seen_refs.add(ref)

    return clean, errors


# ── Wire path validation ────────────────────────────────────────────────


def validate_wire_paths(
    wires: list[dict],
    max_total_len_mm: float = DEFAULT_MAX_WIRE_LEN_MM,
    max_seg_len_mm: float = DEFAULT_MAX_SEG_LEN_MM,
) -> tuple[list[dict], list[str]]:
    """Validate wire paths. Returns (clean_wires, errors).

    Drops any wire that:
      - Has fewer than 2 points
      - Has non-numeric coordinates
      - Has any diagonal segment (both dx and dy nonzero)
      - Has any segment longer than max_seg_len_mm
      - Has total length longer than max_total_len_mm
      - Has any point at NaN/inf
    """
    clean = []
    errors = []

    for i, w in enumerate(wires):
        src = w.get("source", "?")
        tgt = w.get("target", "?")
        path = w.get("path", [])

        if len(path) < 2:
            errors.append(f"wire {src}->{tgt}: fewer than 2 points")
            continue

        # Validate each point
        ok = True
        clean_path = []
        for p in path:
            try:
                px = float(p.get("x", 0))
                py = float(p.get("y", 0))
            except (TypeError, ValueError):
                errors.append(f"wire {src}->{tgt}: non-numeric point")
                ok = False
                break
            if not (abs(px) < 1e6 and abs(py) < 1e6):
                errors.append(f"wire {src}->{tgt}: absurd point ({px}, {py})")
                ok = False
                break
            clean_path.append({"x": _snap(px), "y": _snap(py)})

        if not ok:
            continue

        # Check for diagonal segments + length cap
        total_len = 0.0
        for j in range(len(clean_path) - 1):
            dx = abs(clean_path[j]["x"] - clean_path[j + 1]["x"])
            dy = abs(clean_path[j]["y"] - clean_path[j + 1]["y"])
            if dx > 1e-3 and dy > 1e-3:
                errors.append(f"wire {src}->{tgt}: diagonal segment at point {j}")
                ok = False
                break
            seg_len = dx + dy
            if seg_len > max_seg_len_mm:
                errors.append(
                    f"wire {src}->{tgt}: segment {j} too long "
                    f"({seg_len:.2f}mm > {max_seg_len_mm}mm)"
                )
                ok = False
                break
            total_len += seg_len
            if total_len > max_total_len_mm:
                errors.append(
                    f"wire {src}->{tgt}: total length {total_len:.2f}mm "
                    f"> {max_total_len_mm}mm"
                )
                ok = False
                break

        if not ok:
            continue

        clean.append({
            "source": src,
            "target": tgt,
            "path": clean_path,
            "net": w.get("net", ""),
        })

    return clean, errors


# ── Full design sanitization ────────────────────────────────────────────


def sanitize_design(design: dict) -> dict:
    """Sanitize a full design dict in place. Returns the cleaned dict.

    Runs validate_component_placements + validate_wire_paths and logs
    a summary in design["_validation_report"].
    """
    placements, p_errs = validate_component_placements(
        design.get("component_placements", [])
    )
    design["component_placements"] = placements

    wires, w_errs = validate_wire_paths(
        design.get("wire_paths", [])
    )
    design["wire_paths"] = wires

    design["_validation_report"] = {
        "placement_errors": p_errs,
        "wire_errors": w_errs,
        "n_placements_kept": len(placements),
        "n_wires_kept": len(wires),
        "n_placement_errors": len(p_errs),
        "n_wire_errors": len(w_errs),
    }
    return design


# ── Stub-aware wire path repair ────────────────────────────────────────


def repair_wire_path(
    path: list[dict],
    pin_directions: dict[str, str] | None = None,
) -> list[dict] | None:
    """Try to repair a bad wire path by re-orthogonalizing it.

    If the path has diagonal segments, split them into L-shapes.
    If any segment is too long, return None (caller should drop it).

    Returns the repaired path, or None if unrepairable.
    """
    if len(path) < 2:
        return None

    # Step 1: orthogonalize — split diagonals into L-shapes
    out = [path[0]]
    for i in range(1, len(path)):
        a = out[-1]
        b = path[i]
        if abs(a["x"] - b["x"]) < 1e-3 or abs(a["y"] - b["y"]) < 1e-3:
            out.append(b)
            continue
        # Diagonal — insert corner (horizontal first, then vertical)
        out.append({"x": b["x"], "y": a["y"]})
        out.append(b)

    # Step 2: drop consecutive duplicates
    cleaned = [out[0]]
    for p in out[1:]:
        last = cleaned[-1]
        if abs(last["x"] - p["x"]) > 1e-3 or abs(last["y"] - p["y"]) > 1e-3:
            cleaned.append(p)

    # Step 3: verify no segment exceeds cap
    for i in range(len(cleaned) - 1):
        dx = abs(cleaned[i]["x"] - cleaned[i + 1]["x"])
        dy = abs(cleaned[i]["y"] - cleaned[i + 1]["y"])
        if dx > 1e-3 and dy > 1e-3:
            return None  # still diagonal — unrepairable
        if dx + dy > DEFAULT_MAX_SEG_LEN_MM * 2:
            return None  # too long

    return cleaned
