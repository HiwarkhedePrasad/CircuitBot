"""Block-aware placement (blocks_v2 mode).

Places components hierarchically:
    1. Detect functional blocks (Louvain + signal seeds).
    2. Arrange block rectangles (free-moving, non-overlapping).
    3. Place components inside each block (local spring layout + pin-side satellites).

Usage::

    from agent.placement.blocks_v2 import BlocksV2Placer

    placer = BlocksV2Placer()
    placements = placer.place(components, netlist, pin_matrix)
"""

from __future__ import annotations

import math

import networkx as nx

from agent.placement import PLACEMENT_ENGINE
from agent.placement.community import detect_blocks, _BLOCK_ROLE


# ── Module-level constants (mirrored from layout_engine for independence) ──

GRID_SIZE = 1.27
BBOX_PAD = 1.5
BBOX_CLEARANCE = 0.635
TIER_GAP = 10.16
COMP_V_GAP = 5.08
SAT_H_GAP = 6.35
SAT_V_GAP = 2.54
OVERLAP_PULLBACK = 0.30
MAX_COMPS_PER_COLUMN = 4
SMALL_CIRCUIT_MAX_COMPONENTS = 20

_NET_CLASSES: dict[str, str] = {
    "XTAL": "CRYSTAL", "XIN": "CRYSTAL", "XOUT": "CRYSTAL",
    "XI": "CRYSTAL", "XO": "CRYSTAL", "OSC": "CRYSTAL", "CLK": "CRYSTAL",
    "SCL": "I2C", "SDA": "I2C",
    "MOSI": "SPI", "MISO": "SPI", "SCK": "SPI", "CS": "SPI", "SS": "SPI",
    "TX": "UART", "RX": "UART",
    "USB_DP": "USB", "USB_DM": "USB",
    "VCC": "POWER", "VDD": "POWER", "VIN": "POWER",
    "VBUS": "POWER", "3V3": "POWER", "5V": "POWER",
    "GND": "POWER", "VSS": "POWER",
    "RESET": "RESET", "EN": "RESET", "RST": "RESET",
}

_NET_CRITICALITY: dict[str, float] = {
    "CRYSTAL": 5.0, "USB": 4.0, "SPI": 3.0, "UART": 2.5,
    "I2C": 2.0, "POWER": 1.5, "RESET": 1.5,
}

_PAIR_CONSTRAINTS: dict[tuple[str, str], float] = {
    ("MCU", "CRYSTAL"): 10.0,
    ("MCU", "CAPACITOR"): 8.0,
    ("LDO", "CAPACITOR"): 7.0,
    ("USB", "ESD_IC"): 8.0,
    ("MCU", "SENSOR"): 6.0,
    ("MCU", "RF_MODULE"): 5.0,
    ("REGULATOR", "CAPACITOR"): 7.0,
    ("MCU", "RESISTOR"): 3.0,
    ("SENSOR", "RESISTOR"): 4.0,
    ("RF_MODULE", "CAPACITOR"): 4.0,
}

_TIER_RULES: list[tuple[str, int]] = [
    ("CONNECTOR", 0), ("USB", 0), ("BATTERY", 0),
    ("FUSE", 0), ("POLYFUSE", 0), ("SWITCH", 0),
    ("SPST", 0), ("SPDT", 0), ("TACTILE", 0),
    ("PUSHBUTTON", 0), ("DIP", 0), ("ROCKER", 0),
    ("TOGGLE", 0), ("SLIDE", 0),
    ("LDO", 1), ("REGULATOR", 1), ("BUCK", 1),
    ("BOOST", 1), ("CONVERTER", 1),
    ("MCU", 2), ("PROCESSOR", 2), ("ESP32", 2),
    ("STM32", 2), ("FPGA", 2), ("CPU", 2),
    ("RF_MODULE", 2), ("DSP", 2), ("MEMORY", 2),
    ("SENSOR", 3), ("DISPLAY", 3), ("DRIVER", 3),
    ("INDICATOR", 3),
    ("ESD_IC", 0), ("DIODE", 0), ("ZENER", 0),
    ("SW_", -1), ("SWITCH", 0), ("BUTTON", -1),
    ("LED", -1), ("CAPACITOR", -1), ("RESISTOR", -1),
]

_IDSTR_HINTS: dict[str, str] = {
    "C_Small": "CAPACITOR", "C_Small_US": "CAPACITOR",
    "C_Polarized": "CAPACITOR",
    "R_Small": "RESISTOR", "R": "RESISTOR",
    "Polyfuse": "FUSE", "LED": "LED",
    "D_Small": "DIODE", "Zener": "ZENER",
    "ATmega": "MCU", "ATtiny": "MCU", "AT90": "MCU",
    "ATxmega": "MCU", "AVR128DA": "MCU", "AVR128DB": "MCU",
    "AVR64DA": "MCU", "AVR64DD": "MCU",
    "AMS1117": "LDO", "DS18B20": "SENSOR",
    "TPD6S300A": "ESD_IC", "USBLC6": "ESD_IC",
    "OLED": "DISPLAY", "SSD1306": "DISPLAY",
}


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _sem_type(category: str, id_str: str = "") -> str:
    id_name = id_str.split(":")[-1] if ":" in id_str else id_str
    id_up = id_name.upper()
    for key, typ in _IDSTR_HINTS.items():
        key_up = key.upper()
        if key_up == "R":
            if id_up == "R" or id_up.startswith("R_") or id_up.startswith("R-"):
                return typ
        elif key_up == "LED":
            if "OLED" not in id_up and "LED" in id_up:
                return typ
        elif key_up in id_up:
            return typ
    return category.upper().replace(" ", "_")


def _tier(category: str, id_str: str = "") -> int:
    sem = _sem_type(category, id_str)
    for kw, t in _TIER_RULES:
        if kw in sem:
            return t
    id_name = id_str.split(":")[-1] if ":" in id_str else id_str
    id_up = id_name.upper().replace(" ", "_")
    for kw, t in _TIER_RULES:
        if kw.upper() in id_up:
            return t
    return 2


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


def calculate_ops_bbox(ops: list) -> dict:
    mn_x = mn_y = float("inf")
    mx_x = mx_y = -float("inf")

    def upd(x, y):
        nonlocal mn_x, mn_y, mx_x, mx_y
        if x < mn_x: mn_x = x
        if x > mx_x: mx_x = x
        if y < mn_y: mn_y = y
        if y > mx_y: mx_y = y

    has_graphics = False
    for op in ops:
        t = op[0]
        if t == "rectangle":
            has_graphics = True
            s = _get_attr(op, "start"); e = _get_attr(op, "end")
            if s: upd(float(s[1]), float(s[2]))
            if e: upd(float(e[1]), float(e[2]))
        elif t == "polyline":
            has_graphics = True
            pts = _get_attr(op, "pts")
            if pts:
                for i in range(1, len(pts)):
                    if pts[i][0] == "xy": upd(float(pts[i][1]), float(pts[i][2]))
        elif t == "circle":
            has_graphics = True
            c = _get_attr(op, "center"); r = _get_attr(op, "radius")
            if c and r:
                cx, cy, rv = float(c[1]), float(c[2]), float(r[1])
                upd(cx - rv, cy - rv)
                upd(cx + rv, cy + rv)
        elif t == "arc":
            has_graphics = True
            for name in ("start", "mid", "end"):
                point = _get_attr(op, name)
                if point:
                    upd(float(point[1]), float(point[2]))

    for op in ops:
        if op[0] == "pin":
            a = _get_attr(op, "at")
            length = _get_attr(op, "length")
            if a:
                x, y = float(a[1]), float(a[2])
                upd(x, y)
                if length:
                    import math
                    angle = math.radians(float(a[3]) if len(a) > 3 else 0.0)
                    pin_len = float(length[1])
                    upd(x + math.cos(angle) * pin_len,
                        y + math.sin(angle) * pin_len)

    if mn_x == float("inf"):
        return {"x": -5.0, "y": -5.0, "w": 10.0, "h": 10.0}
    return {
        "x": mn_x - BBOX_PAD,
        "y": mn_y - BBOX_PAD,
        "w": mx_x - mn_x + BBOX_PAD * 2,
        "h": mx_y - mn_y + BBOX_PAD * 2,
    }


# ── Placement logic ────────────────────────────────────────────────────────


def _build_weighted_graph(components: list, netlist: list, pin_matrix: dict) -> nx.Graph:
    """Build a weighted connectivity graph from the netlist."""
    raw_weights: dict[tuple[str, str], float] = {}
    for conn in netlist:
        sr = conn["source"].split(":")[0]
        tr = conn["target"].split(":")[0]
        if sr == tr:
            continue
        pin_key = conn.get("source", "")
        pin_name = pin_key.split(":")[-1] if ":" in pin_key else pin_key
        pin_up = pin_name.upper().replace(" ", "_")
        net_cls = "GPIO"
        for kw, cls in _NET_CLASSES.items():
            if kw in pin_up:
                net_cls = cls
                break
        weight = _NET_CRITICALITY.get(net_cls, 1.0)
        key = (sr, tr) if sr <= tr else (tr, sr)
        raw_weights[key] = raw_weights.get(key, 0.0) + weight

    g = nx.Graph()
    for c in components:
        ref = c["ref_des"]
        sem = _sem_type(c.get("category", ""), c.get("id_str", ""))
        g.add_node(ref, sem=sem, tier=c.get("tier", 2),
                   bbox_area=c.get("width", 10) * c.get("height", 10))

    for (a, b), w in raw_weights.items():
        if not g.has_node(a) or not g.has_node(b):
            continue
        sem_a = g.nodes[a].get("sem", "")
        sem_b = g.nodes[b].get("sem", "")
        bonus = _PAIR_CONSTRAINTS.get((sem_a, sem_b), 0.0) or \
                _PAIR_CONSTRAINTS.get((sem_b, sem_a), 0.0)
        effective = w + bonus
        if g.has_edge(a, b):
            g[a][b]["weight"] += effective
        else:
            g.add_edge(a, b, weight=effective)

    for node in g.nodes:
        g.nodes[node]["degree"] = g.degree(node, weight="weight")

    return g


def _pin_side(comp_ref: str, parent_ref: str, pin_matrix: dict, netlist: list) -> str:
    """Determine which side of the parent IC the component connects to."""
    angles: list[float] = []
    for conn in netlist:
        sr = conn["source"].split(":")[0]
        tr = conn["target"].split(":")[0]
        if {sr, tr} != {comp_ref, parent_ref}:
            continue
        parent_pin_key = None
        if sr == parent_ref:
            parent_pin_key = conn["source"]
        else:
            parent_pin_key = conn["target"]
        pin_info = pin_matrix.get(parent_pin_key)
        if pin_info:
            angles.append(float(pin_info.get("angle", 0)))
    if not angles:
        return "right"
    avg = (sum(angles) / len(angles)) % 360
    if 45 <= avg < 135:
        return "top"
    elif 135 <= avg < 225:
        return "left"
    elif 225 <= avg < 315:
        return "bottom"
    return "right"


def _block_grid_layout(blocks: dict[str, list[str]]) -> dict[str, dict]:
    """Assign a target grid cell to each block based on its role."""
    if set(blocks) == {"SMALL_CIRCUIT_BLOCK"}:
        span_factor = math.sqrt(len(blocks["SMALL_CIRCUIT_BLOCK"]))
        return {
            "SMALL_CIRCUIT_BLOCK": {
                "x": 0.0, "y": 0.0,
                "width": max(80.0, span_factor * 25.0),
                "height": max(60.0, span_factor * 20.0),
            }
        }

    cell_w = 80.0
    cell_h = 60.0
    grid_map: dict[str, tuple[int, int]] = {}
    peripheral_count = 0
    for block_name in blocks:
        role = _BLOCK_ROLE.get(block_name, "peripheral")
        if role == "mcu":
            grid_map[block_name] = (1, 1)
        elif role == "power":
            grid_map[block_name] = (0, 0)
        elif role == "regulator":
            grid_map[block_name] = (1, 0)
        else:
            grid_map[block_name] = (3, peripheral_count)
            peripheral_count += 1

    result: dict[str, dict] = {}
    for block_name, (gx, gy) in grid_map.items():
        result[block_name] = {
            "x": gx * cell_w,
            "y": gy * cell_h,
            "width": cell_w,
            "height": cell_h,
        }
    return result


def _place_block(
    block_refs: list[str], block_bbox: dict,
    parent_map: dict, pin_matrix: dict,
    netlist: list, graph: nx.Graph,
    all_placed: set[str],
    components: list[dict],
) -> None:
    """Place all components in a single block."""
    mains = [r for r in block_refs
             if (_get_comp_ref(r, components) or {}).get("tier", -1) >= 0]
    sats = [r for r in block_refs
            if (_get_comp_ref(r, components) or {}).get("tier", -1) == -1]

    bx = block_bbox["x"]
    by = block_bbox["y"]
    bw = block_bbox["width"]
    bh = block_bbox["height"]
    margin = 20.0
    inner_w = bw - 2 * margin
    inner_h = bh - 2 * margin

    if len(mains) >= 2:
        sub = graph.subgraph(mains).copy()
        if sub.number_of_nodes() >= 2:
            pos = nx.spring_layout(sub, weight="weight", iterations=50,
                                   k=0.8, seed=42)
            px_vals = [p[0] for p in pos.values()]
            py_vals = [p[1] for p in pos.values()]
            rng_x = max(max(px_vals) - min(px_vals), 1.0)
            rng_y = max(max(py_vals) - min(py_vals), 1.0)
            for ref, (lx, ly) in pos.items():
                comp = _get_comp_ref(ref, components)
                if comp:
                    sx = bx + margin + (lx - min(px_vals)) / rng_x * inner_w
                    sy = by + margin + (ly - min(py_vals)) / rng_y * inner_h
                    comp["x"] = _snap(sx)
                    comp["y"] = _snap(sy)
            all_placed.update(mains)
    elif len(mains) == 1:
        ref = mains[0]
        comp = _get_comp_ref(ref, components)
        if comp:
            comp["x"] = _snap(bx + bw / 2 - comp.get("width", 0) / 2)
            comp["y"] = _snap(by + bh / 2 - comp.get("height", 0) / 2)
            all_placed.add(ref)

    # Satellites: pin-side-aware orbit
    side_counts: dict[str, int] = {}
    for sat_ref in sats:
        if sat_ref in all_placed:
            continue
        par_ref = parent_map.get(sat_ref)
        if not par_ref:
            continue
        par_c = _get_comp_ref(par_ref, components)
        sat_c = _get_comp_ref(sat_ref, components)
        if not par_c or not sat_c:
            continue

        side = _pin_side(sat_ref, par_ref, pin_matrix, netlist)
        gap = SAT_H_GAP
        idx = side_counts.get(side, 0)
        side_counts[side] = idx + 1

        pcx = par_c["x"] + par_c["bbox"]["x"]
        pcy = par_c["y"] + par_c["bbox"]["y"]
        pcw = par_c.get("width", 0)
        pch = par_c.get("height", 0)
        scx = sat_c["bbox"]["x"]
        scy = sat_c["bbox"]["y"]
        scw = sat_c.get("width", 0)
        sch = sat_c.get("height", 0)

        v_offset = idx * (sch + SAT_V_GAP)

        if side == "right":
            sx = pcx + pcw + gap - scx
            sy = pcy + pch / 2 - scy - sch / 2 + v_offset
        elif side == "left":
            sx = pcx - gap - scw - scx
            sy = pcy + pch / 2 - scy - sch / 2 + v_offset
        elif side == "top":
            sx = pcx + pcw / 2 - scx - scw / 2
            sy = pcy - gap - sch - scy + v_offset
        else:
            sx = pcx + pcw / 2 - scx - scw / 2
            sy = pcy + pch + gap - scy + v_offset

        sat_c["x"] = _snap(sx)
        sat_c["y"] = _snap(sy)
        all_placed.add(sat_ref)

    # Fallback: satellites whose parent is outside the block
    for sat_ref in sats:
        if sat_ref in all_placed:
            continue
        par_ref = parent_map.get(sat_ref)
        if not par_ref:
            continue
        sat_c = _get_comp_ref(sat_ref, components)
        par_c = _get_comp_ref(par_ref, components)
        if not sat_c or not par_c:
            continue

        side = _pin_side(sat_ref, par_ref, pin_matrix, netlist)
        gap = SAT_H_GAP
        idx = side_counts.get(side, 0)
        side_counts[side] = idx + 1
        v_offset = idx * (sat_c.get("height", 0) + SAT_V_GAP)

        pcx = par_c["x"] + par_c["bbox"]["x"]
        pcy = par_c["y"] + par_c["bbox"]["y"]
        pcw = par_c.get("width", 0)
        pch = par_c.get("height", 0)
        scx = sat_c["bbox"]["x"]
        scy = sat_c["bbox"]["y"]
        scw = sat_c.get("width", 0)

        if side == "right":
            sx = pcx + pcw + gap - scx
            sy = pcy + pch / 2 - scy - sat_c.get("height", 0) / 2 + v_offset
        elif side == "left":
            sx = pcx - gap - scw - scx
            sy = pcy + pch / 2 - scy - sat_c.get("height", 0) / 2 + v_offset
        elif side == "top":
            sx = pcx + pcw / 2 - scx - scw / 2
            sy = pcy - gap - sat_c.get("height", 0) - scy + v_offset
        else:
            sx = pcx + pcw / 2 - scx - scw / 2
            sy = pcy + pch + gap - scy + v_offset

        sat_c["x"] = _snap(sx)
        sat_c["y"] = _snap(sy)
        all_placed.add(sat_ref)


def _remove_overlaps(components: list[dict], max_iters: int = 100) -> int:
    """Push apart overlapping component bounding boxes."""
    if len(components) < 2:
        return 0

    def bounds(comp):
        bbox = comp.get("bbox", {})
        return (
            comp["x"] + bbox.get("x", 0) - BBOX_CLEARANCE,
            comp["y"] + bbox.get("y", 0) - BBOX_CLEARANCE,
            comp["x"] + bbox.get("x", 0) + bbox.get("w", 0) + BBOX_CLEARANCE,
            comp["y"] + bbox.get("y", 0) + bbox.get("h", 0) + BBOX_CLEARANCE,
        )

    def count_overlaps():
        count = 0
        for i in range(len(components)):
            ax1, ay1, ax2, ay2 = bounds(components[i])
            for j in range(i + 1, len(components)):
                bx1, by1, bx2, by2 = bounds(components[j])
                if ax2 > bx1 and ax1 < bx2 and ay2 > by1 and ay1 < by2:
                    count += 1
        return count

    for _ in range(max_iters):
        remaining = 0
        for i in range(len(components)):
            for j in range(i + 1, len(components)):
                a = components[i]
                b = components[j]
                ax1, ay1, ax2, ay2 = bounds(a)
                bx1, by1, bx2, by2 = bounds(b)
                if ax2 <= bx1 or ax1 >= bx2 or ay2 <= by1 or ay1 >= by2:
                    continue
                remaining += 1
                ox = min(ax2, bx2) - max(ax1, bx1)
                oy = min(ay2, by2) - max(ay1, by1)
                if ox <= 0 and oy <= 0:
                    continue
                if ox < oy or (ox == oy and ox == 0):
                    push = (ox + GRID_SIZE) / 2
                    if a["x"] <= b["x"]:
                        a["x"] -= push
                        b["x"] += push
                    else:
                        a["x"] += push
                        b["x"] -= push
                else:
                    push = (oy + GRID_SIZE) / 2
                    if a["y"] <= b["y"]:
                        a["y"] -= push
                        b["y"] += push
                    else:
                        a["y"] += push
                        b["y"] -= push
        if remaining == 0:
            break

    for component in components:
        component["x"] = _snap(component["x"])
        component["y"] = _snap(component["y"])

    return count_overlaps()


def _get_comp_ref(ref_des: str, components: list[dict]):
    for c in components:
        if c["ref_des"] == ref_des:
            return c
    return None


def _prepare_components(components: list[dict]) -> list[dict]:
    """Ensure every component dict has bbox, width, height, tier, sem, x, y."""
    result = []
    for c in components:
        d = dict(c)
        if "bbox" not in d:
            d["bbox"] = calculate_ops_bbox(d.get("ops", []))
        if "width" not in d:
            d["width"] = d["bbox"]["w"]
        if "height" not in d:
            d["height"] = d["bbox"]["h"]
        if "tier" not in d:
            d["tier"] = _tier(d.get("category", ""), d.get("id_str", ""))
        if "sem" not in d:
            d["sem"] = _sem_type(d.get("category", ""), d.get("id_str", ""))
        if "x" not in d:
            d["x"] = 0.0
        if "y" not in d:
            d["y"] = 0.0
        if "rotation" not in d:
            d["rotation"] = d.get("rotation", 0.0)
        if "for_component" not in d:
            d["for_component"] = d.get("for_component", "")
        result.append(d)
    return result


class BlocksV2Placer:
    """Block-aware placement engine (blocks_v2 mode)."""

    def place(self, components: list[dict], netlist: list, pin_matrix: dict) -> list:
        components = _prepare_components(components)
        netlist = netlist or []
        pin_matrix = pin_matrix or {}

        # Build parent map
        parent_map: dict[str, str] = self._build_parent_map(components, netlist)
        for c in components:
            if c.get("tier", -1) != -1:
                continue
            if c["ref_des"] in parent_map:
                continue
            fc = c.get("for_component", "")
            if fc and _get_comp_ref(fc, components):
                parent_map[c["ref_des"]] = fc

        # Build graph
        graph = _build_weighted_graph(components, netlist, pin_matrix)

        # Detect blocks
        block_of = detect_blocks(graph, netlist)

        # Group by block
        blocks: dict[str, list[str]] = {}
        for c in components:
            bid = block_of.get(c["ref_des"], "ORPHAN_BLOCK")
            blocks.setdefault(bid, []).append(c["ref_des"])

        # Assign grid positions
        grid_cells = _block_grid_layout(blocks)

        # Place each block
        all_placed: set[str] = set()
        block_order = sorted(blocks.keys(), key=lambda b: (
            0 if _BLOCK_ROLE.get(b, "") == "mcu" else
            1 if _BLOCK_ROLE.get(b, "") == "power" else
            2 if _BLOCK_ROLE.get(b, "peripheral") == "power" else 3, b
        ))
        for bid in block_order:
            refs = blocks[bid]
            bbox = grid_cells.get(bid, {
                "x": 0, "y": len(all_placed) * 200.0,
                "width": 200.0, "height": 150.0,
            })
            _place_block(refs, bbox, parent_map, pin_matrix,
                         netlist, graph, all_placed, components)

        # Store spring positions for overlap removal
        self._spring_pos = {
            c["ref_des"]: (c["x"], c["y"])
            for c in components
            if c["ref_des"] in all_placed
        }

        # Centre everything around (0, 0)
        xs = [c["x"] for c in components]
        ys = [c["y"] for c in components]
        if xs:
            ox = _snap((max(xs) + min(xs)) / 2)
            oy = _snap((max(ys) + min(ys)) / 2)
            for c in components:
                c["x"] = _snap(c["x"] - ox)
                c["y"] = _snap(c["y"] - oy)

        self._spring_pos = {
            c["ref_des"]: (c["x"], c["y"]) for c in components
        }

        # Overlap removal
        _remove_overlaps(components)

        # Convert to symbol origin
        for c in components:
            bbox = c.get("bbox", {})
            c["x"] = _snap(c["x"] - bbox.get("x", 0))
            c["y"] = _snap(c["y"] - bbox.get("y", 0))

        return [{"ref_des": c["ref_des"], "x": c["x"], "y": c["y"],
                 "rotation": c.get("rotation", 0.0)} for c in components]

    def _build_parent_map(self, components: list, netlist: list) -> dict[str, str]:
        scores: dict[tuple[str, str], int] = {}
        for conn in netlist:
            sr = conn["source"].split(":")[0]
            tr = conn["target"].split(":")[0]
            sc = _get_comp_ref(sr, components)
            tc = _get_comp_ref(tr, components)
            if not sc or not tc:
                continue
            for sat_c, ic_c in [(sc, tc), (tc, sc)]:
                if sat_c.get("tier", -1) == -1 and ic_c.get("tier", -1) >= 0:
                    key = (sat_c["ref_des"], ic_c["ref_des"])
                    scores[key] = scores.get(key, 0) + 1

        parent: dict[str, str] = {}
        for (sat, ic), _ in sorted(scores.items(), key=lambda kv: -kv[1]):
            if sat not in parent:
                parent[sat] = ic
        return parent
