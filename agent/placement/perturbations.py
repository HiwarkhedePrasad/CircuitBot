"""Perturbation functions for simulated annealing placement optimization."""

from __future__ import annotations

import math
import random

from agent.placement.blocks_v2 import _snap, _get_comp_ref, GRID_SIZE
from agent.placement.community import detect_blocks, _BLOCK_ROLE


# ── Component-level perturbations ─────────────────────────────────────────


def nudge(components: list[dict], netlist: list,
          step: float = GRID_SIZE * 2) -> dict | None:
    """Move a random component by a small random offset."""
    candidates = [c for c in components if c.get("tier", -1) >= 0]
    if not candidates:
        return None
    comp = random.choice(candidates)
    dx = random.choice([-step, -step / 2, 0, step / 2, step])
    dy = random.choice([-step, -step / 2, 0, step / 2, step])
    if dx == 0 and dy == 0:
        dy = step
    old = comp["x"], comp["y"]
    comp["x"] = _snap(comp["x"] + dx)
    comp["y"] = _snap(comp["y"] + dy)
    return {"type": "nudge", "ref": comp["ref_des"], "old": old, "new": (comp["x"], comp["y"])}


def swap_components(components: list[dict], netlist: list) -> dict | None:
    """Swap positions of two random main (tier >= 0) components."""
    mains = [c for c in components if c.get("tier", -1) >= 0]
    if len(mains) < 2:
        return None
    a, b = random.sample(mains, 2)
    old_a = a["x"], a["y"]
    old_b = b["x"], b["y"]
    a["x"], a["y"] = old_b
    b["x"], b["y"] = old_a
    return {"type": "swap", "refs": (a["ref_des"], b["ref_des"]),
            "old": (old_a, old_b), "new": ((a["x"], a["y"]), (b["x"], b["y"]))}


def mirror(components: list[dict], netlist: list) -> dict | None:
    """Flip a component relative to the board centre (horizontal or vertical)."""
    mains = [c for c in components if c.get("tier", -1) >= 0]
    if not mains:
        return None
    comp = random.choice(mains)
    xs = [c["x"] for c in components]
    ys = [c["y"] for c in components]
    cx = (max(xs) + min(xs)) / 2 if xs else 0
    cy = (max(ys) + min(ys)) / 2 if ys else 0

    old = comp["x"], comp["y"]
    if random.random() < 0.5:
        comp["x"] = _snap(cx + (cx - comp["x"]))
    else:
        comp["y"] = _snap(cy + (cy - comp["y"]))
    return {"type": "mirror", "ref": comp["ref_des"], "old": old, "new": (comp["x"], comp["y"])}


def reparent(components: list[dict], netlist: list) -> dict | None:
    """Move a satellite to a different random parent IC."""
    sats = [c for c in components if c.get("tier", -1) == -1]
    mains = [c for c in components if c.get("tier", -1) >= 0]
    if not sats or len(mains) < 2:
        return None
    sat = random.choice(sats)
    old_par = None
    # Try to find nearby IC as new parent
    new_par = random.choice(mains)
    if sat.get("for_component") and _get_comp_ref(sat["for_component"], components):
        old_par = _get_comp_ref(sat["for_component"], components)
    if old_par and old_par["ref_des"] == new_par["ref_des"]:
        new_par = random.choice([m for m in mains if m["ref_des"] != old_par["ref_des"]])
        if not new_par:
            return None

    old = sat["x"], sat["y"], sat.get("for_component", "")
    sat["for_component"] = new_par["ref_des"]
    # Place satellite just outside the new parent's right edge
    gap = GRID_SIZE * 2
    sat["x"] = _snap(new_par["x"] + new_par.get("width", 0) + gap - sat["bbox"]["x"])
    sat["y"] = _snap(new_par["y"] + new_par["bbox"]["y"] + new_par.get("height", 0) / 2
                     - sat["bbox"]["y"] - sat.get("height", 0) / 2)
    return {"type": "reparent", "ref": sat["ref_des"],
            "old": old, "new": (sat["x"], sat["y"], sat["for_component"])}


# ── Block-level perturbations ─────────────────────────────────────────────


def _get_block_map(components: list[dict], netlist: list) -> dict[str, list[str]]:
    """Build block -> [ref_des] from community detection."""
    from agent.placement.blocks_v2 import _build_weighted_graph
    graph = _build_weighted_graph(components, netlist, {})
    block_of = detect_blocks(graph, netlist)
    blocks: dict[str, list[str]] = {}
    for c in components:
        bid = block_of.get(c["ref_des"], "ORPHAN_BLOCK")
        blocks.setdefault(bid, []).append(c["ref_des"])
    return blocks


def shift_block(components: list[dict], netlist: list) -> dict | None:
    """Move an entire block by a random offset."""
    blocks = _get_block_map(components, netlist)
    bids = [b for b in blocks if len(b) >= 2]
    if not bids:
        return None
    bid = random.choice(bids)
    refs = blocks[bid]
    dx = random.choice([-10, -5, 5, 10])
    dy = random.choice([-10, -5, 5, 10])
    old_positions = {}
    for c in components:
        if c["ref_des"] in refs:
            old_positions[c["ref_des"]] = (c["x"], c["y"])
            c["x"] = _snap(c["x"] + dx)
            c["y"] = _snap(c["y"] + dy)
    return {"type": "shift_block", "block": bid, "refs": refs,
            "old": old_positions, "dx": dx, "dy": dy}


def swap_blocks(components: list[dict], netlist: list) -> dict | None:
    """Swap positions of two blocks."""
    blocks = _get_block_map(components, netlist)
    bids = [b for b in blocks if len(blocks[b]) >= 1]
    if len(bids) < 2:
        return None
    a, b = random.sample(bids, 2)
    refs_a = set(blocks[a])
    refs_b = set(blocks[b])

    def _block_centre(refs: set[str]) -> tuple[float, float]:
        xs = [c["x"] for c in components if c["ref_des"] in refs]
        ys = [c["y"] for c in components if c["ref_des"] in refs]
        return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else (0, 0)

    ca = _block_centre(refs_a)
    cb = _block_centre(refs_b)
    da = (cb[0] - ca[0], cb[1] - ca[1])
    db = (ca[0] - cb[0], ca[1] - cb[1])

    old_positions = {}
    for c in components:
        if c["ref_des"] in refs_a:
            old_positions[c["ref_des"]] = (c["x"], c["y"])
            c["x"] = _snap(c["x"] + da[0])
            c["y"] = _snap(c["y"] + da[1])
        elif c["ref_des"] in refs_b:
            old_positions[c["ref_des"]] = (c["x"], c["y"])
            c["x"] = _snap(c["x"] + db[0])
            c["y"] = _snap(c["y"] + db[1])

    return {"type": "swap_blocks", "blocks": (a, b),
            "old": old_positions}
