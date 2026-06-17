"""PCB component placement engine — semantic placement.

Replaces netlist-only clustering with a semantic pipeline:
  1. Detect linear arrays (connectors, switches) → edge-aligned
  2. Tag remaining by function (core, satellite)
  3. Hub-and-satellite for core clusters in the center band
  4. Courtyard margins from component type (not just pad bbox)
"""

from __future__ import annotations

import math
import re
from typing import Any

GRID = 1.27
GAP = 2.54
MAX_ROW_W = 140.0
DEFAULT_W = 12.0
DEFAULT_H = 12.0

# ── Helpers ─────────────────────────────────────────────────────────────


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _area(b: dict) -> float:
    return b["w"] * b["h"]


def _default_bbox() -> dict:
    return {"x": -6.0, "y": -6.0, "w": DEFAULT_W, "h": DEFAULT_H}


# ── Semantic tags ───────────────────────────────────────────────────────

EDGE_TOP, EDGE_BOTTOM, CORE, SATELLITE = "EDGE_TOP", "EDGE_BOTTOM", "CORE", "SATELLITE"

# Ref-des prefixes that map to edge placement
_EDGE_TOP_PREFIXES = {"J", "P", "USB", "CN", "TERM", "CONN", "X"}
_EDGE_BOTTOM_PREFIXES = {"SW", "LED", "BTN", "KEY", "D"}

# Categories that indicate a core IC
_CORE_KEYWORDS = {"MCU", "ESP32", "STM32", "PROCESSOR", "FPGA", "DSP",
                  "CPU", "RF_MODULE", "DRIVER", "MOTOR", "POWER"}


def _ref_prefix(ref: str) -> str:
    return re.match(r"^[A-Z]+", ref.upper()).group(0) if re.match(r"^[A-Z]+", ref.upper()) else ""


def _category_keywords(cat: str) -> set[str]:
    return set(cat.upper().replace("_", " ").split())


def _tag_component(comp: dict) -> str:
    ref = comp.get("ref_des", "")
    cat = (comp.get("category") or "").upper()
    prefix = _ref_prefix(ref)
    cat_words = _category_keywords(cat)

    if prefix in _EDGE_TOP_PREFIXES:
        return EDGE_TOP
    if prefix in _EDGE_BOTTOM_PREFIXES:
        return EDGE_BOTTOM
    if cat_words & _CORE_KEYWORDS:
        return CORE
    return SATELLITE


# ── Courtyard / margin per tag ──────────────────────────────────────────

_TAG_MARGIN = {
    CORE: 5.0,
    EDGE_TOP: 2.0,
    EDGE_BOTTOM: 2.0,
    SATELLITE: 0.5,
}


def compute_footprint_bbox(pads: list[dict], tag: str = SATELLITE) -> dict | None:
    """Physical bounding box from footprint pad positions with tag-aware margin.

    *CORE* (MCU, RF):     pad extremes + 5.0 mm
    *EDGE_TOP/BOTTOM*:    pad extremes + 2.0 mm
    *SATELLITE* (passives):  pad extremes + 0.5 mm

    Returns ``{x, y, w, h}`` relative to component origin, or ``None``.
    """
    if not pads:
        return None
    min_x = min(p["x"] - p["sx"] / 2 for p in pads)
    max_x = max(p["x"] + p["sx"] / 2 for p in pads)
    min_y = min(p["y"] - p["sy"] / 2 for p in pads)
    max_y = max(p["y"] + p["sy"] / 2 for p in pads)
    margin = _TAG_MARGIN.get(tag, 1.0)
    return {
        "x": min_x - margin,
        "y": min_y - margin,
        "w": max_x - min_x + margin * 2,
        "h": max_y - min_y + margin * 2,
    }


# ── Linear array detection ──────────────────────────────────────────────


def detect_arrays(comps: list[dict], netlist: list[dict]) -> list[dict]:
    """Find groups of identical-footprint, same-prefix, non-interconnected
    components and return them as ``ArrayCluster`` dicts::

        {refs: [str], footprint: str, prefix: str, orientation: "horizontal"}

    A group must have ≥ 2 members and no netlist edges between them.
    """
    if not comps:
        return []

    # Group by (footprint, prefix)
    groups: dict[tuple[str, str], list[str]] = {}
    for c in comps:
        fp = c.get("footprint", "") or ""
        p = _ref_prefix(c.get("ref_des", ""))
        if not fp or not p:
            continue
        groups.setdefault((fp, p), []).append(c["ref_des"])

    # Build set of direct connections for fast lookup
    connections: set[tuple[str, str]] = set()
    for conn in netlist:
        s = conn["source"].split(":")[0]
        t = conn["target"].split(":")[0]
        if s != t:
            connections.add((s, t))
            connections.add((t, s))

    arrays: list[dict] = []
    for (fp, prefix), refs in groups.items():
        if len(refs) < 2:
            continue
        # Check no interconnections
        if any((a, b) in connections for a in refs for b in refs if a != b):
            continue
        arrays.append({
            "refs": sorted(refs, key=_ref_num),
            "footprint": fp,
            "prefix": prefix,
            "orientation": "horizontal",
        })
    return arrays


def _ref_num(ref: str) -> int:
    m = re.search(r"\d+", ref)
    return int(m.group(0)) if m else 0


# ── Netlist clustering (for non-edge components) ───────────────────────


def cluster_by_netlist(comps: list[dict], netlist: list[dict]) -> list[list[str]]:
    """Greedy modularity clustering on non-array components."""
    import networkx as nx
    from networkx.algorithms.community import greedy_modularity_communities

    G = nx.Graph()
    for c in comps:
        G.add_node(c["ref_des"])
    for conn in netlist:
        src = conn["source"].split(":")[0]
        tgt = conn["target"].split(":")[0]
        if src != tgt:
            cur = G.get_edge_data(src, tgt, {}).get("weight", 0)
            G.add_edge(src, tgt, weight=cur + 1)
    if not G.edges:
        return [[c["ref_des"]] for c in comps]
    communities = list(greedy_modularity_communities(G, weight="weight"))
    return [sorted(c) for c in communities]


def _hub_of(cluster_refs: list[str], netlist: list[dict]) -> str:
    degree: dict[str, int] = {}
    for ref in cluster_refs:
        degree[ref] = 0
    for conn in netlist:
        src = conn["source"].split(":")[0]
        tgt = conn["target"].split(":")[0]
        if src in cluster_refs and tgt in cluster_refs and src != tgt:
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1
    return max(cluster_refs, key=lambda r: (degree.get(r, 0), r))


# ── Hub-and-satellite placement ────────────────────────────────────────


def place_cluster(
    refs: list[str],
    bbox_map: dict[str, dict],
    netlist: list[dict],
    origin: tuple[float, float],
    gap: float = GAP,
    max_row_w: float = MAX_ROW_W,
    try_rotate: bool = False,
) -> list[dict]:
    """Grid-pack a cluster: hub at *(origin)*, satellites in rows to the right.

    When *try_rotate* is ``True``, long‑and‑narrow satellites (w > h × 1.5)
    are tested at 90° rotation and the orientation that minimises the cluster
    width is kept.
    """
    if not refs:
        return []
    hub = _hub_of(refs, netlist)
    hub_b = bbox_map.get(hub, _default_bbox())
    sats = [r for r in refs if r != hub]

    # Place hub
    hub_cx = origin[0] + hub_b["w"] / 2
    hub_cy = origin[1] + hub_b["h"] / 2
    placements: list[dict] = [
        {"ref_des": hub, "x": _snap(hub_cx), "y": _snap(hub_cy), "rotation": 0}
    ]

    if not sats:
        return placements

    # Sort satellites by area descending
    sats.sort(key=lambda r: -_area(bbox_map.get(r, _default_bbox())))

    cur_x = origin[0] + hub_b["w"] + gap
    cur_y = origin[1]
    row_h = 0.0

    for ref in sats:
        b = bbox_map.get(ref, _default_bbox())
        w, h = b["w"], b["h"]
        rot = 0

        # Aspect-ratio driven rotation test
        if try_rotate and w > h * 1.5:
            rot = 90

        if cur_x + w > origin[0] + max_row_w:
            cur_x = origin[0]
            cur_y += row_h + gap
            row_h = 0.0

        cx = cur_x + w / 2
        cy = cur_y + h / 2
        placements.append(
            {"ref_des": ref, "x": _snap(cx), "y": _snap(cy), "rotation": rot}
        )
        cur_x += w + gap
        row_h = max(row_h, h)

    return placements


# ── Main entry point ────────────────────────────────────────────────────


def place_components(comps: list[dict], netlist: list[dict]) -> list[dict]:
    """Semantic, array-aware PCB component placement.

    Pipeline
    --------
    1. Tag every component with a semantic role (EDGE_TOP, EDGE_BOTTOM, CORE, SATELLITE).
    2. Compute courtyard-aware bounding boxes.
    3. Detect linear arrays among edge components.
    4. Place arrays along the board perimeter (top / bottom).
    5. Cluster remaining (CORE + SATELLITE) by netlist connectivity.
    6. Place clustered groups in the center band.
    7. Centre the whole board around (0, 0).
    """
    if not comps:
        return []

    comps_by_ref = {c["ref_des"]: c for c in comps}

    # 1. Tag + bbox
    tags: dict[str, str] = {}
    bbox_map: dict[str, dict] = {}
    for c in comps:
        t = _tag_component(c)
        tags[c["ref_des"]] = t
        pads = c.get("pads", [])
        b = compute_footprint_bbox(pads, tag=t) or _default_bbox()
        bbox_map[c["ref_des"]] = b
        c["bbox_w"] = b["w"]
        c["bbox_h"] = b["h"]

    # 2. Detect arrays
    arrays = detect_arrays(comps, netlist)
    array_refs: set[str] = set()
    for arr in arrays:
        for r in arr["refs"]:
            array_refs.add(r)

    # 3. Place arrays on edges
    all_placements: list[dict] = []
    board_top_y = 0.0
    board_bottom_y = 0.0

    for arr in arrays:
        refs = arr["refs"]
        first_tag = tags.get(refs[0], SATELLITE)
        if first_tag == EDGE_BOTTOM:
            # Bottom edge: align on a common baseline
            max_h = max(bbox_map.get(r, _default_bbox())["h"] for r in refs)
            total_w = sum(bbox_map.get(r, _default_bbox())["w"] for r in refs) + GAP * (len(refs) - 1)
            x = 0.0
            for r in refs:
                b = bbox_map.get(r, _default_bbox())
                cx = x + b["w"] / 2
                cy = max_h / 2  # baseline at bottom
                all_placements.append({"ref_des": r, "x": _snap(cx), "y": _snap(cy), "rotation": 0})
                x += b["w"] + GAP
            board_bottom_y = max(board_bottom_y, max_h)
        else:
            # Top edge: align on top baseline
            max_h = max(bbox_map.get(r, _default_bbox())["h"] for r in refs)
            total_w = sum(bbox_map.get(r, _default_bbox())["w"] for r in refs) + GAP * (len(refs) - 1)
            x = 0.0
            for r in refs:
                b = bbox_map.get(r, _default_bbox())
                cx = x + b["w"] / 2
                cy = board_top_y + b["h"] / 2
                all_placements.append({"ref_des": r, "x": _snap(cx), "y": _snap(cy), "rotation": 0})
                x += b["w"] + GAP
            board_top_y += max_h + GAP * 2

    # 4. Cluster remaining non-array components (CORE + SATELLITE)
    core_comps = [c for c in comps if c["ref_des"] not in array_refs]
    clusters = cluster_by_netlist(core_comps, netlist)

    # 5. Place clusters in the center band
    cluster_origin_y = board_top_y + GAP
    cluster_origin_x = 0.0

    for cluster in clusters:
        placements = place_cluster(
            cluster, bbox_map, netlist,
            (cluster_origin_x, cluster_origin_y),
            try_rotate=True,
        )
        all_placements.extend(placements)
        cluster_max_x = max(
            p["x"] + bbox_map.get(p["ref_des"], _default_bbox())["w"] / 2 + GAP
            for p in placements
        )
        cluster_origin_x = cluster_max_x + GAP

    # 6. Centre the whole board around (0, 0)
    if all_placements:
        min_x = min(p["x"] for p in all_placements)
        min_y = min(p["y"] for p in all_placements)
        for p in all_placements:
            p["x"] = _snap(p["x"] - min_x)
            p["y"] = _snap(p["y"] - min_y)

    return all_placements
