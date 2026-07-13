"""Graph-driven PCB placement — no LLM, no zones, no autorouting.

Pipeline:
  1. Build weighted connectivity graph from netlist.
  2. Community detection (greedy modularity) → clusters.
  3. Cluster-level spring placement with anchoring.
  4. Component-center spring placement (coarse).
  5. Rotation optimization (4 angles, min weighted HPWL).
  6. Overlap removal.
  7. Pin-level HPWL refinement (multi-objective score).
  8. Board shrink to tight bounding box.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Optional

GRID = 1.27
GAP = 2.54

# Board defaults (mm) — will be tightened after placement
DEFAULT_BOARD_W = 100.0
DEFAULT_BOARD_H = 80.0
DEFAULT_EDGE_MARGIN = 3.0

# Force-directed params
SPRING_K = 0.15
REPULSION_K = 3.0
DAMPING = 0.85
MAX_DISPLACEMENT = 5.0
EQUILIBRIUM_EPS = 0.05
MAX_ITERATIONS = 60
PIN_REFINE_MAX_SHIFT = 2.0
PIN_REFINE_ITERATIONS = 30
OVERLAP_MAX_PASSES = 20

POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
                   "+5V", "+3.3V", "3.3V", "5V", "3V3"}
GND_NET_NAMES = {"GND", "GROUND", "AGND", "DGND", "0V"}


NET_CLASSES = {
    "power":   {"weight": 10, "anchor": "edge"},
    "ground":  {"weight": 10, "anchor": None},
    "clock":   {"weight": 9,  "anchor": None},
    "usb":     {"weight": 8,  "anchor": "edge"},
    "spi":     {"weight": 7,  "anchor": None},
    "i2c":     {"weight": 6,  "anchor": None},
    "uart":    {"weight": 5,  "anchor": None},
    "gpio":    {"weight": 2,  "anchor": None},
    "led":     {"weight": 1,  "anchor": "edge"},
    "default": {"weight": 3,  "anchor": None},
}


# ── Helpers ─────────────────────────────────────────────────────────────


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _ref_prefix(ref: str) -> str:
    m = re.match(r"^[A-Z]+", ref.upper())
    return m.group(0) if m else ""


def _ref_num(ref: str) -> int:
    m = re.search(r"\d+", ref)
    return int(m.group(0)) if m else 0


def _default_bbox() -> dict:
    return {"x": -6.0, "y": -6.0, "w": 12.0, "h": 12.0}


def _net_class(net_name: str) -> str:
    name = (net_name or "").strip().upper()
    if name in GND_NET_NAMES:
        return "ground"
    if name in POWER_NET_NAMES:
        return "power"
    if name in {"D+", "D-", "DP", "DM", "USB_DP", "USB_DM"}:
        return "usb"
    if name in {"SCL", "SDA"}:
        return "i2c"
    if name in {"MOSI", "MISO", "SCK", "CS", "SS", "CE"}:
        return "spi"
    if name in {"TX", "RX", "TXD", "RXD", "TX0", "RX0"}:
        return "uart"
    if name in {"XTAL_IN", "XTAL_OUT", "OSC_IN", "OSC_OUT", "XIN", "XOUT"}:
        return "clock"
    if "LED" in name:
        return "led"
    if "GPIO" in name or name.startswith("GP"):
        return "gpio"
    return "default"


# ── Classification ──────────────────────────────────────────────────────


POWER_SOURCE, REGULATOR, PROTECTION, CORE_IC, CRYSTAL, STORAGE, \
    SENSOR, DISPLAY, INTERFACE, PASSIVE, DECOUPLING = range(11)

_ZONE_NAMES = {
    POWER_SOURCE: "POWER_SOURCE",
    REGULATOR:    "REGULATOR",
    PROTECTION:   "PROTECTION",
    CORE_IC:      "CORE_IC",
    CRYSTAL:      "CRYSTAL",
    STORAGE:      "STORAGE",
    SENSOR:       "SENSOR",
    DISPLAY:      "DISPLAY",
    INTERFACE:    "INTERFACE",
    PASSIVE:      "PASSIVE",
    DECOUPLING:   "DECOUPLING",
}


def _classify(comp: dict) -> int:
    ref = comp.get("ref_des", "")
    cat = (comp.get("category") or "").upper()
    id_str = (comp.get("id_str") or "").upper()
    prefix = _ref_prefix(ref)
    if prefix in {"J", "P", "USB", "CN", "CONN"}:
        return POWER_SOURCE
    if prefix == "Y":
        return CRYSTAL
    if prefix in {"SW", "BTN", "KEY"}:
        return INTERFACE
    if prefix == "D":
        if "ESD" in id_str or "TVS" in id_str:
            return PROTECTION
        return INTERFACE
    if prefix == "LED":
        return INTERFACE
    if prefix == "C":
        return PASSIVE
    if prefix == "R":
        return PASSIVE
    if prefix == "L":
        return PASSIVE
    if prefix == "F":
        return PROTECTION
    cat_words = set(cat.replace("_", " ").split())
    if cat_words & {"MCU", "ESP32", "STM32", "PROCESSOR", "FPGA", "DSP",
                    "CPU", "RF_MODULE", "DRIVER", "MOTOR"}:
        return CORE_IC
    if "REGULATOR" in cat or "LDO" in cat_words or "BUCK" in cat_words or "BOOST" in cat_words:
        return REGULATOR
    if "SENSOR" in cat_words or "TEMPERATURE" in cat_words:
        return SENSOR
    if "DISPLAY" in cat_words or "OLED" in cat_words or "SSD1306" in id_str:
        return DISPLAY
    if "MEMORY" in cat_words or "FLASH" in cat_words or "EEPROM" in cat_words:
        return STORAGE
    if "ESD" in cat_words or "PROTECTION" in cat_words:
        return PROTECTION
    if "CRYSTAL" in cat_words or "OSCILLATOR" in cat_words:
        return CRYSTAL
    return PASSIVE


# ── Decoupling cap detection ────────────────────────────────────────────


def _detect_decoupling_caps(comps: list[dict], netlist: list[dict],
                             pin_matrix: dict) -> dict[str, str]:
    """Return {cap_ref: parent_ic_ref} for decoupling caps."""
    if not pin_matrix:
        return {}
    pin_to_net: dict[str, str] = {}
    for conn in netlist:
        net = conn.get("net", "")
        pin_to_net.setdefault(conn.get("source", ""), net)
        pin_to_net.setdefault(conn.get("target", ""), net)

    core_refs = {c["ref_des"] for c in comps if _classify(c) == CORE_IC}
    core_power: dict[str, set] = {r: set() for r in core_refs}
    core_gnd: dict[str, set] = {r: set() for r in core_refs}
    for pin_key, net in pin_to_net.items():
        ref = pin_key.split(":")[0]
        if ref not in core_refs:
            continue
        nu = (net or "").upper().lstrip("+")
        if nu in POWER_NET_NAMES:
            core_power[ref].add(nu)
        if nu in GND_NET_NAMES:
            core_gnd[ref].add(nu)

    decoupling: dict[str, str] = {}
    for c in comps:
        ref = c["ref_des"]
        if _ref_prefix(ref) != "C":
            continue
        cap_pin_keys = [k for k in pin_to_net if k.startswith(f"{ref}:")]
        if len(cap_pin_keys) < 2:
            continue
        cap_nets = [(pk, pin_to_net[pk].upper().lstrip("+")) for pk in cap_pin_keys]
        for core_ref in core_refs:
            if not core_power[core_ref] or not core_gnd[core_ref]:
                continue
            for i, (pk_a, net_a) in enumerate(cap_nets):
                for j, (pk_b, net_b) in enumerate(cap_nets):
                    if i == j:
                        continue
                    if net_a in POWER_NET_NAMES and net_b in GND_NET_NAMES:
                        decoupling[ref] = core_ref
                        break
                if ref in decoupling:
                    break
            if ref in decoupling:
                break
    return decoupling


# ── Bounding boxes ──────────────────────────────────────────────────────


def _compute_bbox(pads: list[dict], tag: int) -> dict:
    if not pads:
        return _default_bbox()
    min_x = min(p["x"] - p.get("sx", p.get("width", 1)) / 2 for p in pads)
    max_x = max(p["x"] + p.get("sx", p.get("width", 1)) / 2 for p in pads)
    min_y = min(p["y"] - p.get("sy", p.get("height", 1)) / 2 for p in pads)
    max_y = max(p["y"] + p.get("sy", p.get("height", 1)) / 2 for p in pads)
    margin = {
        CORE_IC: 5.0, REGULATOR: 3.0, POWER_SOURCE: 2.0,
        PROTECTION: 1.0, CRYSTAL: 1.5, SENSOR: 2.0, DISPLAY: 3.0,
        STORAGE: 2.0, INTERFACE: 1.5, PASSIVE: 0.5, DECOUPLING: 0.5,
    }.get(tag, 1.0)
    return {
        "x": min_x - margin, "y": min_y - margin,
        "w": max_x - min_x + margin * 2,
        "h": max_y - min_y + margin * 2,
    }


# ── Graph building ─────────────────────────────────────────────────────


def _build_graph(comps: list[dict], netlist: list[dict],
                 pin_matrix: dict) -> tuple["nx.Graph", dict[str, str]]:
    """Build weighted NetworkX graph from netlist.

    Returns (graph, net_class_map).
    """
    import networkx as nx
    G = nx.Graph()
    for c in comps:
        G.add_node(c["ref_des"])

    net_class_map: dict[str, str] = {}
    for conn in netlist:
        src = conn.get("source", "")
        tgt = conn.get("target", "")
        net = conn.get("net", "")
        if not net:
            net = pin_matrix.get(src, {}).get("name", "") or pin_matrix.get(tgt, {}).get("name", "")
        if not net:
            continue
        cls = _net_class(net)
        net_class_map[net] = cls
        w = NET_CLASSES[cls]["weight"]
        s_ref = src.split(":")[0]
        t_ref = tgt.split(":")[0]
        if s_ref == t_ref or not G.has_node(s_ref) or not G.has_node(t_ref):
            continue
        if G.has_edge(s_ref, t_ref):
            G[s_ref][t_ref]["weight"] += w
        else:
            G.add_edge(s_ref, t_ref, weight=w)
    return G, net_class_map


# ── Community detection ────────────────────────────────────────────────


def _detect_clusters(G: "nx.Graph") -> list[set[str]]:
    """Detect clusters using greedy modularity maximisation."""
    if G.number_of_nodes() == 0:
        return []
    import networkx as nx
    from networkx.algorithms.community import greedy_modularity_communities
    try:
        communities = list(greedy_modularity_communities(G))
        return [set(c) for c in communities]
    except Exception:
        # Fallback: every component is its own cluster
        return [{n} for n in G.nodes()]


def _dominant_tag(cluster: set[str], tags: dict[str, int]) -> int:
    """Return the most common component tag in a cluster."""
    counts: dict[int, int] = defaultdict(int)
    for r in cluster:
        counts[tags.get(r, PASSIVE)] += 1
    return max(counts, key=counts.get)


# ── Cluster-level placement ────────────────────────────────────────────


def _cluster_origin(cluster: set[str], clusters: list[set[str]],
                    tags: dict[str, int], board_w: float, board_h: float) -> tuple[float, float]:
    """Assign a starting region for a cluster based on its dominant tag."""
    dom = _dominant_tag(cluster, tags)
    idx = clusters.index(cluster)
    n = len(clusters)
    # Spread clusters left-to-right by tag priority
    priority = {POWER_SOURCE: 0, REGULATOR: 1, PROTECTION: 2,
                CORE_IC: 3, CRYSTAL: 4, STORAGE: 5,
                SENSOR: 6, DISPLAY: 7, INTERFACE: 8, PASSIVE: 9, DECOUPLING: 9}
    p = priority.get(dom, 9)
    col = p % 3
    row = p // 3
    third_w = board_w / 3
    third_h = board_h / 3
    return (col * third_w + DEFAULT_EDGE_MARGIN, row * third_h + DEFAULT_EDGE_MARGIN)


def _place_clusters(clusters: list[set[str]], G: "nx.Graph",
                     bbox_map: dict, tags: dict[str, int],
                     board_w: float, board_h: float) -> dict[str, tuple[float, float]]:
    """Place cluster centroids, then return component positions inside each."""
    if len(clusters) <= 1:
        return {}

    import networkx as nx

    # Build inter-cluster graph
    CG = nx.Graph()
    for i, c in enumerate(clusters):
        # Estimate cluster bbox
        cw = sum(bbox_map.get(r, _default_bbox())["w"] for r in c)
        ch = sum(bbox_map.get(r, _default_bbox())["h"] for r in c)
        CG.add_node(i, size=math.sqrt(max(cw, ch)))

    for a, b, data in G.edges(data=True):
        ca = _find_cluster(a, clusters)
        cb = _find_cluster(b, clusters)
        if ca is not None and cb is not None and ca != cb:
            w = data.get("weight", 1)
            if CG.has_edge(ca, cb):
                CG[ca][cb]["weight"] += w
            else:
                CG.add_edge(ca, cb, weight=w)

    if CG.number_of_nodes() == 0:
        return {}

    # Place cluster centroids with simple force-directed
    cpos: dict[int, tuple[float, float]] = {}
    for i in CG.nodes():
        ox, oy = _cluster_origin(clusters[i], clusters, tags, board_w, board_h)
        cpos[i] = (ox, oy)

    for _ in range(MAX_ITERATIONS):
        max_disp = 0.0
        forces: dict[int, tuple[float, float]] = {i: (0.0, 0.0) for i in CG.nodes()}

        for i, j, data in CG.edges(data=True):
            w = data.get("weight", 1)
            dx = cpos[j][0] - cpos[i][0]
            dy = cpos[j][1] - cpos[i][1]
            dist = math.hypot(dx, dy) + 1e-6
            f = SPRING_K * w * dist * 2  # cluster-level spring is stronger
            fx, fy = f * dx / dist, f * dy / dist
            fi_x, fi_y = forces[i]
            fj_x, fj_y = forces[j]
            forces[i] = (fi_x + fx, fi_y + fy)
            forces[j] = (fj_x - fx, fj_y - fy)

        # Repulsion between all cluster pairs
        c_items = list(CG.nodes())
        for idx, i in enumerate(c_items):
            ri = CG.nodes[i].get("size", 10)
            for j in c_items[idx + 1:]:
                rj = CG.nodes[j].get("size", 10)
                dx = cpos[j][0] - cpos[i][0]
                dy = cpos[j][1] - cpos[i][1]
                dist = math.hypot(dx, dy) + 1e-6
                min_dist = (ri + rj) * 0.5
                if dist < min_dist:
                    f = REPULSION_K * (min_dist - dist) / dist
                else:
                    f = REPULSION_K / (dist * dist)
                fx, fy = f * dx / dist, f * dy / dist
                fi_x, fi_y = forces[i]
                fj_x, fj_y = forces[j]
                forces[i] = (fi_x - fx, fi_y - fy)
                forces[j] = (fj_x + fx, fj_y + fy)

        for i in CG.nodes():
            fx, fy = forces[i]
            vx = fx * DAMPING * 0.5
            vy = fy * DAMPING * 0.5
            speed = math.hypot(vx, vy)
            if speed > MAX_DISPLACEMENT * 2:
                vx = vx * MAX_DISPLACEMENT * 2 / speed
                vy = vy * MAX_DISPLACEMENT * 2 / speed
            nx_ = cpos[i][0] + vx
            ny_ = cpos[i][1] + vy
            nx_ = max(DEFAULT_EDGE_MARGIN, min(board_w - DEFAULT_EDGE_MARGIN, nx_))
            ny_ = max(DEFAULT_EDGE_MARGIN, min(board_h - DEFAULT_EDGE_MARGIN, ny_))
            disp = math.hypot(nx_ - cpos[i][0], ny_ - cpos[i][1])
            if disp > max_disp:
                max_disp = disp
            cpos[i] = (nx_, ny_)

        if max_disp < EQUILIBRIUM_EPS:
            break

    # Return component positions: place each component within its cluster, offset from cluster centroid
    pos: dict[str, tuple[float, float]] = {}
    for i, cluster in enumerate(clusters):
        cx, cy = cpos[i]
        # Grid pack within cluster
        refs = sorted(cluster, key=_ref_num)
        xc, yc = cx, cy
        max_row_h = 0.0
        col = 0
        for ref in refs:
            b = bbox_map.get(ref, _default_bbox())
            pos[ref] = (xc + b["w"] / 2, yc + b["h"] / 2)
            xc += b["w"] + GAP
            max_row_h = max(max_row_h, b["h"])
            col += 1
            if col >= 3:
                xc = cx
                yc += max_row_h + GAP
                max_row_h = 0.0
                col = 0
    return pos


def _find_cluster(ref: str, clusters: list[set[str]]) -> Optional[int]:
    for i, c in enumerate(clusters):
        if ref in c:
            return i
    return None


# ── Component-level spring (coarse) ────────────────────────────────────


def _place_components(
    pos: dict[str, tuple[float, float]],
    comps: list[dict], G: "nx.Graph",
    clusters: list[set[str]], bbox_map: dict,
    board_w: float, board_h: float, tags: dict[str, int],
) -> dict[str, tuple[float, float]]:
    """Component-center spring placement (coarse refinement)."""
    if not pos:
        # Fallback: start from origin
        pos = {}
        for i, c in enumerate(comps):
            pos[c["ref_des"]] = (board_w / 2 + (i % 4) * GAP * 2,
                                 board_h / 2 + (i // 4) * GAP * 2)

    vel: dict[str, list[float]] = {r: [0.0, 0.0] for r in pos}
    fixed_refs: set[str] = set()

    for _ in range(MAX_ITERATIONS):
        max_disp = 0.0
        force: dict[str, list[float]] = {r: [0.0, 0.0] for r in pos}

        # Spring attraction along graph edges
        for a, b, data in G.edges(data=True):
            if a not in pos or b not in pos:
                continue
            w = data.get("weight", 1)
            dx = pos[b][0] - pos[a][0]
            dy = pos[b][1] - pos[a][1]
            dist = math.hypot(dx, dy) + 1e-6
            f = SPRING_K * w * dist
            fx, fy = f * dx / dist, f * dy / dist
            force[a][0] += fx
            force[a][1] += fy
            force[b][0] -= fx
            force[b][1] -= fy

        # Repulsion between all pairs
        refs = list(pos.keys())
        for idx, a in enumerate(refs):
            ba = bbox_map.get(a, _default_bbox())
            ra = max(ba["w"], ba["h"]) / 2 + 0.5
            for b in refs[idx + 1:]:
                bb = bbox_map.get(b, _default_bbox())
                rb = max(bb["w"], bb["h"]) / 2 + 0.5
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy) + 1e-6
                min_dist = ra + rb
                if dist < min_dist:
                    f = REPULSION_K * (min_dist - dist) / dist
                else:
                    f = REPULSION_K / (dist * dist)
                fx, fy = f * dx / dist, f * dy / dist
                force[a][0] -= fx
                force[a][1] -= fy
                force[b][0] += fx
                force[b][1] += fy

        for r in pos:
            if r in fixed_refs:
                continue
            vx, vy = vel[r]
            fx, fy = force[r]
            vx = DAMPING * vx + fx
            vy = DAMPING * vy + fy
            speed = math.hypot(vx, vy)
            if speed > MAX_DISPLACEMENT:
                vx = vx * MAX_DISPLACEMENT / speed
                vy = vy * MAX_DISPLACEMENT / speed
            vel[r] = [vx, vy]
            new_x = pos[r][0] + vx
            new_y = pos[r][1] + vy
            ba = bbox_map.get(r, _default_bbox())
            new_x = max(ba["w"] / 2 + DEFAULT_EDGE_MARGIN,
                        min(board_w - ba["w"] / 2 - DEFAULT_EDGE_MARGIN, new_x))
            new_y = max(ba["h"] / 2 + DEFAULT_EDGE_MARGIN,
                        min(board_h - ba["h"] / 2 - DEFAULT_EDGE_MARGIN, new_y))
            disp = math.hypot(new_x - pos[r][0], new_y - pos[r][1])
            if disp > max_disp:
                max_disp = disp
            pos[r] = (new_x, new_y)

        if max_disp < EQUILIBRIUM_EPS:
            break

    return pos


# ── Decoupling cap snapping ────────────────────────────────────────────


def _snap_decoupling_caps(
    pos: dict[str, tuple[float, float]],
    decoupling_map: dict[str, str],
    bbox_map: dict[str, dict],
    netlist: list[dict],
    pin_matrix: dict,
) -> None:
    """Move each decoupling cap to within 2 mm of its parent IC's power pin."""
    if not decoupling_map or not pin_matrix:
        return
    parent_power_pin: dict[str, tuple[float, float]] = {}
    for conn in netlist:
        net = (conn.get("net", "") or "").upper().lstrip("+")
        if net not in POWER_NET_NAMES:
            continue
        for pin_key in (conn.get("source", ""), conn.get("target", "")):
            ref = pin_key.split(":")[0]
            if ref in decoupling_map.values() and ref not in parent_power_pin:
                pin = pin_matrix.get(pin_key)
                if pin:
                    parent_power_pin[ref] = (pin.get("x", 0), pin.get("y", 0))

    for cap_ref, parent_ref in decoupling_map.items():
        if cap_ref not in pos or parent_ref not in pos:
            continue
        if parent_ref not in parent_power_pin:
            continue
        par_x, par_y = pos[parent_ref]
        px, py = parent_power_pin[parent_ref]
        abs_px = par_x + px
        abs_py = par_y + py
        cap_b = bbox_map.get(cap_ref, _default_bbox())
        offset = max(cap_b["w"] / 2 + 1.5, 2.5)
        pos[cap_ref] = (_snap(abs_px + offset), _snap(abs_py))


# ── HPWL computation ───────────────────────────────────────────────────


def _weighted_hpwl(pos: dict[str, tuple[float, float]],
                   G: "nx.Graph") -> float:
    """Compute weighted half-perimeter wire length (component-centre)."""
    total = 0.0
    for a, b, data in G.edges(data=True):
        w = data.get("weight", 1)
        if a not in pos or b not in pos:
            continue
        dx = pos[b][0] - pos[a][0]
        dy = pos[b][1] - pos[a][1]
        total += w * (abs(dx) + abs(dy))
    return total


def _pin_hpwl(pos: dict[str, tuple[float, float]],
              netlist: list[dict], pin_matrix: dict,
              bbox_map: dict | None = None) -> tuple[float, float, float]:
    """Compute pin-level HPWL, overlap count, and crossings estimate.

    Returns (hpwl, overlap_count, crossing_estimate).
    """
    if bbox_map is None:
        bbox_map = {}
    hpwl = 0.0
    crossings = 0
    segments: list[tuple[float, float, float, float, float]] = []
    for conn in netlist:
        src = conn.get("source", "")
        tgt = conn.get("target", "")
        net = conn.get("net", "")
        s_ref = src.split(":")[0]
        t_ref = tgt.split(":")[0]
        if s_ref not in pos or t_ref not in pos:
            continue
        cls = _net_class(net)
        w = NET_CLASSES[cls]["weight"]
        sx = pin_matrix.get(src, {}).get("x", 0) if src in pin_matrix else 0
        sy = pin_matrix.get(src, {}).get("y", 0) if src in pin_matrix else 0
        tx = pin_matrix.get(tgt, {}).get("x", 0) if tgt in pin_matrix else 0
        ty = pin_matrix.get(tgt, {}).get("y", 0) if tgt in pin_matrix else 0
        x1 = pos[s_ref][0] + sx
        y1 = pos[s_ref][1] + sy
        x2 = pos[t_ref][0] + tx
        y2 = pos[t_ref][1] + ty
        hpwl += w * (abs(x2 - x1) + abs(y2 - y1))
        segments.append((x1, y1, x2, y2, w))

    # Estimate crossings: count pairs of segments that intersect
    for i in range(len(segments)):
        x1, y1, x2, y2, w1 = segments[i]
        for j in range(i + 1, len(segments)):
            x3, y3, x4, y4, w2 = segments[j]
            if _segments_intersect(x1, y1, x2, y2, x3, y3, x4, y4):
                crossings += 1

    # Overlap count — use actual bounding boxes, not defaults
    overlaps = _count_overlaps(pos, bbox_map)
    return hpwl, overlaps, crossings


def _count_overlaps(pos: dict[str, tuple[float, float]],
                    bbox_map: dict) -> int:
    """Count overlapping component pairs."""
    refs = list(pos.keys())
    count = 0
    for i, a in enumerate(refs):
        ba = bbox_map.get(a, _default_bbox())
        ax1 = pos[a][0] + ba["x"]
        ax2 = ax1 + ba["w"]
        ay1 = pos[a][1] + ba["y"]
        ay2 = ay1 + ba["h"]
        for b in refs[i + 1:]:
            bb = bbox_map.get(b, _default_bbox())
            bx1 = pos[b][0] + bb["x"]
            bx2 = bx1 + bb["w"]
            by1 = pos[b][1] + bb["y"]
            by2 = by1 + bb["h"]
            if not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1):
                count += 1
    return count


def _segments_intersect(x1, y1, x2, y2, x3, y3, x4, y4) -> bool:
    """Check if two line segments intersect (excluding shared endpoints)."""
    def _ccw(ax, ay, bx, by, cx, cy):
        return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)
    # Check if segments share an endpoint
    if (abs(x1 - x3) < 0.01 and abs(y1 - y3) < 0.01) or \
       (abs(x1 - x4) < 0.01 and abs(y1 - y4) < 0.01) or \
       (abs(x2 - x3) < 0.01 and abs(y2 - y3) < 0.01) or \
       (abs(x2 - x4) < 0.01 and abs(y2 - y4) < 0.01):
        return False
    return _ccw(x1, y1, x3, y3, x4, y4) != _ccw(x2, y2, x3, y3, x4, y4) and \
           _ccw(x1, y1, x2, y2, x3, y3) != _ccw(x1, y1, x2, y2, x4, y4)


# ── Rotation optimization ──────────────────────────────────────────────


def _optimize_rotation(pos: dict[str, tuple[float, float]],
                        comps: list[dict], G: "nx.Graph",
                        bbox_map: dict, pin_matrix: dict,
                        netlist: list[dict]) -> dict[str, float]:
    """Try 0/90/180/270° for each component, pick minimum weighted HPWL.

    Returns {ref: best_rotation_in_degrees}.
    """
    rotations: dict[str, float] = {}
    for c in comps:
        ref = c["ref_des"]
        best_r = 0.0
        best_score = float("inf")
        for angle in (0, 90, 180, 270):
            # Temporarily apply rotation and compute score
            saved_x, saved_y = pos[ref]
            # Rotation doesn't change position, only pin offsets
            # We can skip full computation and just estimate HPWL change
            hpwl = _weighted_hpwl(pos, G)
            # For rotation estimate, use pin-level HPWL with rotated pins
            rotated_pin_hpwl = _estimate_rotated_hpwl(ref, angle, pos, netlist, pin_matrix)
            score = rotated_pin_hpwl
            if score < best_score:
                best_score = score
                best_r = angle
        rotations[ref] = best_r
    return rotations


def _estimate_rotated_hpwl(
    ref: str, angle: float,
    pos: dict[str, tuple[float, float]],
    netlist: list[dict], pin_matrix: dict,
) -> float:
    """Estimate pin-level HPWL contribution for a single component at *angle*."""
    total = 0.0
    angle_rad = math.radians(angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    for conn in netlist:
        src = conn.get("source", "")
        tgt = conn.get("target", "")
        net = conn.get("net", "")
        cls = _net_class(net)
        w = NET_CLASSES[cls]["weight"]
        # Check if this connection involves *ref*
        other_key = None
        is_src = False
        if src.startswith(f"{ref}:"):
            other_key = tgt
            is_src = True
        elif tgt.startswith(f"{ref}:"):
            other_key = src
        else:
            continue
        o_ref = other_key.split(":")[0]
        if o_ref not in pos:
            continue
        # Pin offset for this ref (rotated)
        pin_key = src if is_src else tgt
        sx = pin_matrix.get(pin_key, {}).get("x", 0) if pin_key in pin_matrix else 0
        sy = pin_matrix.get(pin_key, {}).get("y", 0) if pin_key in pin_matrix else 0
        rx = sx * cos_a - sy * sin_a
        ry = sx * sin_a + sy * cos_a
        ax = pos[ref][0] + rx
        ay = pos[ref][1] + ry
        # Other component pin (not rotated)
        ox = pin_matrix.get(other_key, {}).get("x", 0) if other_key in pin_matrix else 0
        oy = pin_matrix.get(other_key, {}).get("y", 0) if other_key in pin_matrix else 0
        bx = pos[o_ref][0] + ox
        by = pos[o_ref][1] + oy
        total += w * (abs(bx - ax) + abs(by - ay))
    return total


# ── Overlap removal ────────────────────────────────────────────────────


def _remove_overlaps(pos: dict[str, tuple[float, float]],
                      bbox_map: dict) -> dict[str, tuple[float, float]]:
    """Push overlapping components apart. Mutates and returns pos."""
    if not bbox_map:
        return pos
    for _ in range(OVERLAP_MAX_PASSES):
        any_overlap = False
        refs = list(pos.keys())
        for i, a in enumerate(refs):
            ba = bbox_map.get(a, _default_bbox())
            ax1 = pos[a][0] + ba["x"]
            ax2 = ax1 + ba["w"]
            ay1 = pos[a][1] + ba["y"]
            ay2 = ay1 + ba["h"]
            for b in refs[i + 1:]:
                bb = bbox_map.get(b, _default_bbox())
                bx1 = pos[b][0] + bb["x"]
                bx2 = bx1 + bb["w"]
                by1 = pos[b][1] + bb["y"]
                by2 = by1 + bb["h"]
                if ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1:
                    continue
                any_overlap = True
                # Push apart
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                if abs(dx) < 0.01 and abs(dy) < 0.01:
                    dx, dy = 0.5, 0.5
                dist = math.hypot(dx, dy)
                overlap_x = max(0, (min(ax2, bx2) - max(ax1, bx1)) / 2 + 0.1)
                overlap_y = max(0, (min(ay2, by2) - max(ay1, by1)) / 2 + 0.1)
                push = math.hypot(overlap_x, overlap_y) + 0.5
                dx /= dist
                dy /= dist
                pos[a] = (pos[a][0] - dx * push / 2, pos[a][1] - dy * push / 2)
                pos[b] = (pos[b][0] + dx * push / 2, pos[b][1] + dy * push / 2)
        if not any_overlap:
            break
    return pos


# ── Pin-level HPWL refinement ──────────────────────────────────────────


def _optimize_hpwl(pos: dict[str, tuple[float, float]],
                    comps: list[dict], G: "nx.Graph",
                    pin_matrix: dict, bbox_map: dict,
                    netlist: list[dict]) -> dict[str, tuple[float, float]]:
    """Multi-objective local refinement using pin-level springs.

    Each component gets nudged in the direction that reduces its
    weighted HPWL contribution, clamped to PIN_REFINE_MAX_SHIFT.
    """
    if not pin_matrix:
        return pos

    for _ in range(PIN_REFINE_ITERATIONS):
        before = _pin_hpwl(pos, netlist, pin_matrix, bbox_map)
        if before[0] < 0.01:
            break

        # For each component, try nudging in 4 directions
        for c in comps:
            ref = c["ref_des"]
            best_dx, best_dy = 0.0, 0.0
            best_score = float("inf")

            for ndx, ndy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                             (0.5, 0.5), (-0.5, 0.5), (0.5, -0.5), (-0.5, -0.5)]:
                saved = pos[ref]
                pos[ref] = (saved[0] + ndx * 0.25, saved[1] + ndy * 0.25)
                hpwl, overlaps, crossings = _pin_hpwl(pos, netlist, pin_matrix, bbox_map)
                xs = [pos[r][0] for r in pos]
                ys = [pos[r][1] for r in pos]
                area = (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else 0
                score = hpwl + overlaps * 50.0 + crossings * 10.0 + area * 0.1
                if score < best_score:
                    best_score = score
                    best_dx = ndx * 0.25
                    best_dy = ndy * 0.25
                pos[ref] = saved  # restore

            if best_dx != 0 or best_dy != 0:
                old = pos[ref]
                new_x = old[0] + best_dx
                new_y = old[1] + best_dy
                # Clamp shift
                shift = math.hypot(new_x - old[0], new_y - old[1])
                if shift > PIN_REFINE_MAX_SHIFT:
                    ratio = PIN_REFINE_MAX_SHIFT / shift
                    new_x = old[0] + (new_x - old[0]) * ratio
                    new_y = old[1] + (new_y - old[1]) * ratio
                pos[ref] = (new_x, new_y)

        after = _pin_hpwl(pos, netlist, pin_matrix, bbox_map)
        if abs(before[0] - after[0]) < 0.01:
            break

    return pos


# ── Board shrink ────────────────────────────────────────────────────────


def _shrink_board(pos: dict[str, tuple[float, float]],
                   bbox_map: dict) -> tuple[float, float, float, float]:
    """Compute tight bounding box + margin around all components.

    Returns (min_x, min_y, max_x, max_y).
    """
    xs: list[float] = []
    ys: list[float] = []
    for r, (x, y) in pos.items():
        b = bbox_map.get(r, _default_bbox())
        xs.append(x + b["x"])
        xs.append(x + b["x"] + b["w"])
        ys.append(y + b["y"])
        ys.append(y + b["y"] + b["h"])
    if not xs:
        return (-30, -20, 30, 20)
    margin = 5.0
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


# ── Main entry point ────────────────────────────────────────────────────


def place_components_deterministic(
    comps: list[dict],
    netlist: list[dict],
    pin_matrix: dict | None = None,
    board_w: float | None = None,
    board_h: float | None = None,
) -> list[dict]:
    """Fully deterministic graph-driven placement — no LLM, no zones.

    Returns: list of {"ref_des", "x", "y", "rotation"} dicts.
    """
    if not comps:
        return []
    pin_matrix = pin_matrix or {}

    # 1. Build connectivity graph
    G, net_class_map = _build_graph(comps, netlist, pin_matrix)

    # 2. Detect clusters
    clusters = _detect_clusters(G)
    if not clusters:
        clusters = [{c["ref_des"]} for c in comps]

    # 3. Classify components
    tags: dict[str, int] = {c["ref_des"]: _classify(c) for c in comps}

    # 4. Detect decoupling caps
    decoupling = _detect_decoupling_caps(comps, netlist, pin_matrix)
    for cap_ref in decoupling:
        tags[cap_ref] = DECOUPLING

    # 5. Compute bboxes
    bbox_map: dict[str, dict] = {}
    for c in comps:
        bbox_map[c["ref_des"]] = _compute_bbox(c.get("pads", []), tags[c["ref_des"]])

    # 6. Determine board size
    if board_w is None or board_h is None:
        total_area = sum(b["w"] * b["h"] for b in bbox_map.values())
        side = math.sqrt(total_area) * 2.0
        board_w = max(board_w or side, 60)
        board_h = max(board_h or side * 0.75, 40)

    # 7. Cluster-level placement
    pos = _place_clusters(clusters, G, bbox_map, tags, board_w, board_h)

    # 8. Coarse component placement
    pos = _place_components(pos, comps, G, clusters, bbox_map, board_w, board_h, tags)

    # 9. Snap decoupling caps to parent power pin
    _snap_decoupling_caps(pos, decoupling, bbox_map, netlist, pin_matrix)

    # 10. Optimize rotation
    rotations = _optimize_rotation(pos, comps, G, bbox_map, pin_matrix, netlist)

    # 11. Remove overlaps
    pos = _remove_overlaps(pos, bbox_map)

    # 12. Pin-level HPWL refinement
    pos = _optimize_hpwl(pos, comps, G, pin_matrix, bbox_map, netlist)

    # 13. Snap to grid
    for r in pos:
        pos[r] = (_snap(pos[r][0]), _snap(pos[r][1]))

    # 14. Board shrink (informational — outline computed later by layout node)
    # Remove overlaps again after snap
    pos = _remove_overlaps(pos, bbox_map)

    # 15. Emit
    return [
        {
            "ref_des": r,
            "x": pos[r][0],
            "y": pos[r][1],
            "rotation": rotations.get(r, 0),
        }
        for r in sorted(pos, key=_ref_num)
    ]


place_components = place_components_deterministic
