"""KiCad pcbnew worker — runs under KiCad's bundled Python, NOT the project venv.

Receives JSON via stdin, creates a board via pcbnew API, returns result JSON
via stdout. Fully self-contained: no imports from the project package.
"""

import json
import math
import os
import sys
import tempfile

import pcbnew


# ── Constants ────────────────────────────────────────────────────────────

GRID_MM = 0.254
BOARD_MARGIN_MM = 3.0

POWER_NETS = {
    "VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
    "+5V", "+3.3V", "3.3V", "5V", "3V3", "5V",
    "GND", "GROUND", "AGND", "DGND",
}
POWER_TRACE_WIDTH = 0.5
SIGNAL_TRACE_WIDTH = 0.254


# ── Helpers ──────────────────────────────────────────────────────────────


def _mm(v: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I_MM(v, v)


def _vec(x: float, y: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I_MM(x, y)


def _layer_id(name: str) -> int:
    return {
        "F.Cu": pcbnew.F_Cu,
        "B.Cu": pcbnew.B_Cu,
        "Edge.Cuts": pcbnew.Edge_Cuts,
        "F.SilkS": pcbnew.F_SilkS,
        "B.SilkS": pcbnew.B_SilkS,
        "F.Mask": pcbnew.F_Mask,
        "B.Mask": pcbnew.B_Mask,
    }.get(name, pcbnew.F_Cu)


# ── Simple orthogonal A* router (no external deps) ───────────────────────


def _orthogonal_astar(start, end, obstacles, grid_size_mm=GRID_MM,
                      max_steps=5000):
    """A* on a grid. start/end are (x_mm, y_mm) tuples.
    obstacles is a set of (col, row) grid cells that are blocked.
    Returns list of (x_mm, y_mm) waypoints or [] if no path.
    """
    origin_x = min(start[0], end[0]) - 50.0
    origin_y = min(start[1], end[1]) - 50.0
    size = 100.0  # search window in mm

    def _to_grid(mx, my):
        return (int(round((mx - origin_x) / grid_size_mm)),
                int(round((my - origin_y) / grid_size_mm)))

    def _to_mm(gx, gy):
        return (origin_x + gx * grid_size_mm,
                origin_y + gy * grid_size_mm)

    gs, gt = _to_grid(*start)
    es, et = _to_grid(*end)

    blocked = set()
    for ox, oy in obstacles:
        blocked.add(_to_grid(ox, oy))

    w = int(size / grid_size_mm)
    h = int(size / grid_size_mm)

    def _neighbors(gx, gy):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = gx + dx, gy + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in blocked:
                yield nx, ny

    def _heuristic(gx, gy):
        return abs(gx - es) + abs(gy - et)

    open_set = { (gs, gt) }
    came_from = {}
    g_score = { (gs, gt): 0 }
    f_score = { (gs, gt): _heuristic(gs, gt) }

    for _ in range(max_steps):
        if not open_set:
            break
        current = min(open_set, key=lambda n: f_score.get(n, float('inf')))
        if current == (es, et):
            # Reconstruct path
            path_grid = []
            while current in came_from:
                path_grid.append(current)
                current = came_from[current]
            path_grid.append((gs, gt))
            path_grid.reverse()
            return [_to_mm(gx, gy) for gx, gy in path_grid]

        open_set.remove(current)
        for neighbor in _neighbors(*current):
            tentative = g_score[current] + 1
            if tentative < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f_score[neighbor] = tentative + _heuristic(*neighbor)
                open_set.add(neighbor)

    return []  # no path found


# ── Board builder ────────────────────────────────────────────────────────


def _create_board(payload: dict) -> dict:
    model = payload["model"]
    netlist = payload["netlist"]
    comps = model.get("components", [])

    board = pcbnew.NewBoard("")

    # ── 1. Build net index from net names, not component refs ──────
    net_names: set[str] = set()
    for conn in netlist:
        net_name = conn.get("net", "")
        if net_name:
            net_names.add(net_name)

    # Add GND as net 1 always
    net_names.discard("GND")
    ordered_nets = ["", "GND"] + sorted(net_names)
    net_index = {}
    for i, name in enumerate(ordered_nets):
        net_item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net_item)
        net_index[name] = i

    # ── 2. Place footprints ────────────────────────────────────────
    all_pad_centers = []  # for obstacle grid
    for comp in comps:
        fp_path = comp.get("footprint_path", "")
        if not fp_path or not os.path.isfile(fp_path):
            continue

        lib_dir = os.path.dirname(fp_path)
        fp_name = os.path.splitext(os.path.basename(fp_path))[0]
        footprint = pcbnew.FootprintLoad(lib_dir, fp_name)

        ref = comp.get("ref", comp.get("ref_des", ""))
        footprint.SetReference(ref)
        footprint.SetPosition(_vec(comp.get("x", 0), comp.get("y", 0)))
        footprint.SetOrientationDegrees(comp.get("rotation", 0))
        board.Add(footprint)

        # Collect pad positions for obstacle grid
        for pad in footprint.Pads():
            pos = pad.GetPosition()
            all_pad_centers.append((
                pcbnew.ToMM(pos.x),
                pcbnew.ToMM(pos.y),
            ))

    # ── 3. Route traces ─────────────────────────────────────────────
    traces_out = []
    vias_out = []

    obstacles = set()
    for px, py in all_pad_centers:
        gx = round(px / GRID_MM)
        gy = round(py / GRID_MM)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                obstacles.add((gx + dx, gy + dy))

    # Build pin-to-net mapping
    pin_to_net = {}
    for conn in netlist:
        net_name = conn.get("net", "")
        if not net_name:
            ref = conn["source"].split(":")[0] if ":" in conn["source"] else conn["source"]
            net_name = ref
        pin_to_net[conn["source"]] = net_name
        pin_to_net[conn["target"]] = net_name

    # Route power nets first with wider traces
    def _route_priority(conn):
        s = conn["source"].split(":")[0]
        t = conn["target"].split(":")[0]
        if s in POWER_NETS or t in POWER_NETS:
            return 0
        return 1

    routed_connections = 0
    for conn in sorted(netlist, key=_route_priority):
        src = conn["source"]
        tgt = conn["target"]
        net_name = pin_to_net.get(src, "")

        # Find the pads on the board
        src_pad = _find_pad(board, src)
        tgt_pad = _find_pad(board, tgt)
        if not src_pad or not tgt_pad:
            continue

        src_pos = (pcbnew.ToMM(src_pad.GetPosition().x),
                   pcbnew.ToMM(src_pad.GetPosition().y))
        tgt_pos = (pcbnew.ToMM(tgt_pad.GetPosition().x),
                   pcbnew.ToMM(tgt_pad.GetPosition().y))

        if src_pos == tgt_pos:
            continue

        waypoints = _orthogonal_astar(src_pos, tgt_pos, obstacles)
        if not waypoints:
            continue

        is_power = net_name.upper() in {n.upper() for n in POWER_NETS}
        width = POWER_TRACE_WIDTH if is_power else SIGNAL_TRACE_WIDTH

        # Create track segments
        track_path = []
        for i in range(len(waypoints) - 1):
            x1, y1 = waypoints[i]
            x2, y2 = waypoints[i + 1]
            if abs(x1 - x2) < 0.001 and abs(y1 - y2) < 0.001:
                continue
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(_vec(x1, y1))
            track.SetEnd(_vec(x2, y2))
            track.SetWidth(pcbnew.FromMM(width))
            track.SetLayer(pcbnew.F_Cu)
            net_id = net_index.get(net_name, 0)
            net_info = board.FindNet(net_id) if net_id > 0 else None
            if net_info:
                track.SetNet(net_info)
            board.Add(track)
            track_path.append((x1, y1))

        if track_path:
            track_path.append(waypoints[-1])
            traces_out.append({
                "net": net_name,
                "layer": "F.Cu",
                "width": width,
                "path": track_path,
            })
            routed_connections += 1

        # Mark used cells as obstacles for subsequent routes
        for wx, wy in waypoints:
            obstacles.add((round(wx / GRID_MM), round(wy / GRID_MM)))

    # ── 4. Add GND copper zone ──────────────────────────────────────
    _add_gnd_pour(board, net_index)

    # ── 5. Save to temp file and read back ──────────────────────────
    tmp = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False,
                                       mode="w", encoding="utf-8")
    tmp.close()
    try:
        board.Save(tmp.name)
        with open(tmp.name, "r", encoding="utf-8") as f:
            pcb_content = f.read()
    finally:
        os.unlink(tmp.name)

    return {
        "status": "ok",
        "kicad_pcb": pcb_content,
        "traces": traces_out,
        "vias": vias_out,
    }


def _find_pad(board, pin_key):
    """Find a pad on the board matching a pin_key like 'U1:5'."""
    ref, _, pad_num = pin_key.partition(":")
    for footprint in board.GetFootprints():
        if footprint.GetReference() == ref:
            for pad in footprint.Pads():
                if pad.GetNumber() == pad_num:
                    return pad
    return None


def _add_gnd_pour(board, net_index):
    """Add a GND copper pour on F.Cu and B.Cu."""
    from pcbnew import ZONE_SETTINGS, ZONE

    gnd_id = net_index.get("GND", 0)
    if gnd_id == 0:
        return

    gnd_net = board.FindNet(gnd_id)
    if not gnd_net:
        return

    # Determine board outline bounds from footprints
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    for fp in board.GetFootprints():
        pos = fp.GetPosition()
        mx = pcbnew.ToMM(pos.x)
        my = pcbnew.ToMM(pos.y)
        bbox = fp.GetBoundingBox()
        bw = pcbnew.ToMM(bbox.GetWidth()) / 2
        bh = pcbnew.ToMM(bbox.GetHeight()) / 2
        min_x = min(min_x, mx - bw - 5)
        min_y = min(min_y, my - bh - 5)
        max_x = max(max_x, mx + bw + 5)
        max_y = max(max_y, my + bh + 5)

    if min_x == float('inf'):
        return

    outline = pcbnew.SHAPE_LINE_CHAIN()
    outline.Append(_vec(min_x, min_y))
    outline.Append(_vec(max_x, min_y))
    outline.Append(_vec(max_x, max_y))
    outline.Append(_vec(min_x, max_y))
    outline.SetClosed(True)

    for layer_name in ("F.Cu", "B.Cu"):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(_layer_id(layer_name))
        zone.SetNet(gnd_net)
        zone.SetIslandRemovalMode(True)
        zone.SetMinIslandArea(1000000)  # 1 mm² in nm²

        from pcbnew import ZONE_CONNECTION_THERMAL
        if hasattr(zone, "GetZoneSettings"):
            settings = zone.GetZoneSettings()
            settings.SetPadConnection(ZONE_CONNECTION_THERMAL)
            # Defensive: pcbnew renamed these across versions. A missing
            # setter must not abort the whole routing run.
            try:
                settings.SetThermalReliefGap(pcbnew.FromMM(0.254))
            except AttributeError:
                pass
            try:
                settings.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.254))
            except AttributeError:
                pass
            zone.SetZoneSettings(settings)
        else:
            zone.SetPadConnection(ZONE_CONNECTION_THERMAL)
            try:
                zone.SetThermalReliefGap(pcbnew.FromMM(0.254))
            except AttributeError:
                pass
            try:
                zone.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.254))
            except AttributeError:
                pass

        # KiCad 8+/10 removed OutlinePushBack; use SetOutline via
        # SHAPE_POLY_SET instead.  Try the old path first for older KiCad.
        from pcbnew import SHAPE_POLY_SET
        poly = SHAPE_POLY_SET()
        poly.AddOutline(outline)
        try:
            zone.OutlinePushBack(outline)
        except AttributeError:
            pass
        try:
            zone.SetOutline(poly)
        except AttributeError:
            pass
        try:
            zone.SetPolygon(poly)
        except AttributeError:
            pass

        board.Add(zone)


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    payload = json.loads(sys.stdin.read())
    result = _create_board(payload)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
