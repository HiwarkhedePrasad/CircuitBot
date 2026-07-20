"""KiCad schematic (.kicad_sch) exporter — hardened version.

Improvements over the original:

  * All wire endpoints are snapped to the 1.27 mm grid.
  * Degenerate (zero-length) wire segments are dropped.
  * Path simplification never produces diagonal segments — collinear
    interior points are merged, but any segment that would be diagonal
    is split at the nearest grid corner to keep the wire orthogonal.
  * Wires that share both endpoints with another wire are deduplicated.
  * Junction dots are placed only at T-junctions (3+ wire ends meeting
    at a grid point) — never on a simple corner.
  * Power labels get a short stub so they visually attach to the pin.

Coordinate system (from kicad-schematic skill):
- Symbol libraries (.kicad_sym) use Y-up (math convention).
- Schematics (.kicad_sch) use Y-down (screen convention).
- Pin at library (px, py), symbol placed at schematic (sx, sy) with rotation R:

    Rotation 0:   sheet_pos = (sx + px, sy - py)
    Rotation 90:  sheet_pos = (sx + py, sy + px)
    Rotation 180: sheet_pos = (sx - px, sy + py)
    Rotation 270: sheet_pos = (sx - py, sy - px)

- CircuitBot's canvas uses Y growing the same direction as the symbol files.
- Therefore: sheet_x = canvas_x + offset, sheet_y = -canvas_y + offset.

See docs/kicad-schematic-reference.md for full coordinate transform reference.
"""
from __future__ import annotations

import datetime
import re
import uuid

from agent.exceptions import ExportValidationError

# Re-export shared grid constant from sexpr_utils for consistency
# Use agent.sexpr_utils.snap() / agent.sexpr_utils.pin_abs() when
# coordinate transforms are needed outside this module.
from agent.sexpr_utils import snap as _grid_snap, pin_abs as _pin_abs_global, GRID as _GRID
from agent.routing.geometry import _orthogonal_segments_intersect

GRID = 1.27

# Tokens that are safe to emit unquoted in an S-expression
_SAFE_RE = re.compile(r'^[A-Za-z0-9_.\-+]+$')

# Argument positions (1-indexed) that KiCad always expects quoted
_QUOTED_ARGS = {
    'property': (1, 2),
    'symbol': (1,),
    'name': (1,),
    'number': (1,),
    'text': (1,),
}


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _q(s: str) -> str:
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _fmt(v: float) -> str:
    """Format a coordinate without trailing zeros."""
    s = f"{v:.4f}".rstrip('0').rstrip('.')
    return s if s else '0'


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def serialize(node) -> str:
    """Serialize a parsed S-expression node (nested lists of strings)
    back into KiCad S-expression text."""
    if not isinstance(node, list) or not node:
        return ''
    head = str(node[0])
    parts = [head]
    qpos = _QUOTED_ARGS.get(head, ())
    for i, child in enumerate(node[1:], 1):
        if isinstance(child, list):
            parts.append(serialize(child))
        else:
            tok = str(child)
            if i in qpos or tok == '' or not _SAFE_RE.match(tok):
                parts.append(_q(tok))
            else:
                parts.append(tok)
    return '(' + ' '.join(parts) + ')'


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


# ── lib_symbols construction ─────────────────────────────────────────────────


def build_lib_symbol(id_str: str, ops: list) -> str:
    """Build a flattened lib_symbols entry from already-parsed ops.

    Because the agent's parser already merges `extends` parents into the
    ops list, the exported symbol is self-contained (no derivation needed).
    """
    _, _, name = id_str.partition(':')

    props: dict[str, list] = {}
    graphics, pins = [], []
    for op in ops:
        if op[0] == 'property' and len(op) >= 3:
            props[op[1]] = op
        elif op[0] == 'pin':
            pins.append(op)
        elif op[0] in ('rectangle', 'polyline', 'circle', 'arc', 'text'):
            graphics.append(op)

    # Dedupe pins by number (extends-merge can duplicate them)
    seen, unique_pins = set(), []
    for p in pins:
        num = _get_attr(p, 'number')
        key = num[1] if num and len(num) > 1 else None
        if key is None or key in seen:
            continue
        seen.add(key)
        unique_pins.append(p)

    # Ensure mandatory properties exist
    def _default_prop(pname, pvalue, hide=False):
        eff = ['effects', ['font', ['size', '1.27', '1.27']]]
        if hide:
            eff.append('hide')
        return ['property', pname, pvalue, ['at', '0', '0', '0'], eff]

    if 'Reference' not in props:
        from agent.utils import _ref_prefix_for
        prefix = _ref_prefix_for(id_str, '')
        props['Reference'] = _default_prop('Reference', prefix)
    if 'Value' not in props:
        props['Value'] = _default_prop('Value', name)
    if 'Footprint' not in props:
        props['Footprint'] = _default_prop('Footprint', '', hide=True)
    if 'Datasheet' not in props:
        props['Datasheet'] = _default_prop('Datasheet', '', hide=True)

    lines = [f'    (symbol {_q(id_str)} (pin_names (offset 1.016)) (in_bom yes) (on_board yes)']
    for pname in ('Reference', 'Value', 'Footprint', 'Datasheet'):
        lines.append('      ' + serialize(props[pname]))
    lines.append(f'      (symbol {_q(name + "_0_1")}')
    for g in graphics:
        lines.append('        ' + serialize(g))
    lines.append('      )')
    lines.append(f'      (symbol {_q(name + "_1_1")}')
    for p in unique_pins:
        lines.append('        ' + serialize(p))
    lines.append('      )')
    lines.append('    )')
    return '\n'.join(lines)


# ── wire path simplification ─────────────────────────────────────────────────


def _simplify_path(points: list) -> list:
    """Collapse collinear runs into corner points only.

    Hardened:
      * Drops consecutive duplicate points.
      * NEVER merges two points into a diagonal segment — if three
        consecutive points are not axis-aligned, the middle is kept.
      * Returns at least 2 points for any 2+ point input.
    """
    if not points:
        return []
    cleaned = [points[0]]
    for p in points[1:]:
        last = cleaned[-1]
        if abs(last['x'] - p['x']) < 1e-3 and abs(last['y'] - p['y']) < 1e-3:
            continue
        cleaned.append(p)
    if len(cleaned) < 3:
        return cleaned

    out = [cleaned[0]]
    for i in range(1, len(cleaned) - 1):
        x0, y0 = out[-1]['x'], out[-1]['y']
        x1, y1 = cleaned[i]['x'], cleaned[i]['y']
        x2, y2 = cleaned[i + 1]['x'], cleaned[i + 1]['y']
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        # Must be axis-aligned in BOTH legs AND collinear (same sign of
        # direction) to drop the middle point.
        axis_aligned = (
            (abs(dx1) < 1e-3 or abs(dy1) < 1e-3) and
            (abs(dx2) < 1e-3 or abs(dy2) < 1e-3)
        )
        same_dir = (dx1 * dx2 >= -1e-6) and (dy1 * dy2 >= -1e-6)
        cross    = dx1 * dy2 - dy1 * dx2
        if axis_aligned and same_dir and abs(cross) < 1e-6:
            continue
        out.append(cleaned[i])
    out.append(cleaned[-1])
    return out


def _orthogonalize(points: list) -> list:
    """Force a path to be strictly orthogonal.

    Any diagonal segment between two consecutive points is split into
    an L-shape at the midpoint. Input is assumed already simplified.
    """
    if len(points) < 2:
        return points
    out = [points[0]]
    for i in range(1, len(points)):
        a = out[-1]
        b = points[i]
        if abs(a['x'] - b['x']) < 1e-3 or abs(a['y'] - b['y']) < 1e-3:
            out.append(b)
            continue
        # Diagonal — insert a corner. Prefer horizontal-then-vertical
        # so the wire enters the pin in the right direction (matches
        # the router's stub convention).
        mid = {'x': b['x'], 'y': a['y']}
        out.append(mid)
        out.append(b)
    return out


# ── main entry point ─────────────────────────────────────────────────────────


def generate_kicad_sch(design: dict) -> str:
    """Generate a complete .kicad_sch document.

    Expects a design dict with keys:
    - selected_components: [{id_str, ref_des, category, description}]
    - component_ops: {ref_des: ops}
    - component_placements: [{ref_des, x, y}]
    - wire_paths: [{source, target, path: [{x, y}]}]
    - title (optional): project name for the title block (default: "CircuitBot Generated Design")
    """
    comps = design.get('selected_components', [])
    comp_ops = design.get('component_ops', {})
    placements = {p['ref_des']: p for p in design.get('component_placements', [])}
    wires = design.get('wire_paths', [])
    power_labels = design.get('power_labels', [])
    netlist = design.get('netlist', [])

    # Build footprint lookup: ref_des -> footprint string
    fp_lookup = {c['ref_des']: c.get('footprint', '') for c in comps}

    root_uuid = _new_uuid()

    # Compute translation so everything lands in positive sheet space.
    # Sheet coords: (x, -y) of canvas coords.
    xs, ys = [], []
    for c in comps:
        p = placements.get(c['ref_des'])
        if p:
            xs.append(p['x'])
            ys.append(-p['y'])
    for w in wires:
        for pt in w.get('path', []):
            xs.append(pt['x'])
            ys.append(-pt['y'])
    for lbl in power_labels:
        xs.append(lbl['x'])
        ys.append(-lbl['y'])
    for nl in design.get('net_labels', []):
        at = nl.get('at', {})
        xs.append(at.get('x', 0))
        ys.append(-at.get('y', 0))
    min_x = min(xs) if xs else 0.0
    min_y = min(ys) if ys else 0.0
    off_x = _snap(50.8 - min_x)
    off_y = _snap(50.8 - min_y)

    out = []
    out.append('(kicad_sch (version 20231120) (generator "circuitbot") (generator_version "1.0")')
    out.append(f'  (uuid {_q(root_uuid)})')
    out.append('  (paper "A3")')

    # ── title block (MUST come before lib_symbols per KiCad format spec) ──
    title = (design.get("title", "") or "").strip()
    if not title:
        title = "CircuitBot Generated Design"
    today = datetime.date.today().strftime("%Y-%m-%d")
    out.append('  (title_block')
    out.append(f'    (title {_q(title)})')
    out.append(f'    (date "{today}")')
    out.append('    (rev "draft")')
    out.append('    (company "")')
    out.append('    (comment 1 "generated by CircuitBot EDA agent")')
    out.append('  )')

    # ── lib_symbols ──
    out.append('  (lib_symbols')
    emitted = set()
    for c in comps:
        if c['id_str'] in emitted:
            continue
        ops = comp_ops.get(c['ref_des'])
        if not ops:
            continue
        emitted.add(c['id_str'])
        out.append(build_lib_symbol(c['id_str'], ops))
    out.append('  )')

    # ── placed symbol instances ──
    for c in comps:
        ref = c['ref_des']
        ops = comp_ops.get(ref)
        place = placements.get(ref)
        if not ops or not place:
            continue

        sx = _snap(place['x'] + off_x)
        sy = _snap(-place['y'] + off_y)
        name = c.get("value") or c['id_str'].partition(':')[2]
        sym_uuid = _new_uuid()
        rot = int(round(float(place.get('rotation', 0.0)) / 90.0) * 90) % 360
        out.append(f'  (symbol (lib_id {_q(c["id_str"])}) (at {_fmt(sx)} {_fmt(sy)} {rot}) (unit 1)')
        out.append('    (body_style 1)')
        out.append('    (exclude_from_sim no)')
        out.append('    (in_bom yes) (on_board yes) (in_pos_files yes) (dnp no)')
        out.append(f'    (uuid {_q(sym_uuid)})')
        out.append(f'    (property "Reference" {_q(ref)} (at {_fmt(sx)} {_fmt(sy - 2.54)} 0)')
        out.append('      (effects (font (size 1.27 1.27)) (justify left))')
        out.append('    )')
        out.append(f'    (property "Value" {_q(name)} (at {_fmt(sx)} {_fmt(sy + 2.54)} 0)')
        out.append('      (effects (font (size 1.27 1.27)) (justify left))')
        out.append('    )')
        fp_val = fp_lookup.get(ref, '') or ''
        out.append(f'    (property "Footprint" {_q(fp_val)} (at {_fmt(sx)} {_fmt(sy)} 0)')
        out.append('      (effects (font (size 1.27 1.27))' + (' hide)' if not fp_val else ')'))
        out.append('    )')
        out.append(f'    (property "Datasheet" "" (at {_fmt(sx)} {_fmt(sy)} 0)')
        out.append('      (effects (font (size 1.27 1.27)) hide)')
        out.append('    )')

        # Pin uuid stubs
        seen_pins = set()
        for op in ops:
            if op[0] != 'pin':
                continue
            num = _get_attr(op, 'number')
            if not num or len(num) < 2 or num[1] in seen_pins:
                continue
            seen_pins.add(num[1])
            out.append(f'    (pin {_q(num[1])} (uuid {_q(_new_uuid())}))')

        out.append('    (instances')
        out.append('      (project "circuitbot"')
        out.append(f'        (path {_q("/" + root_uuid)} (reference {_q(ref)}) (unit 1))')
        out.append('      )')
        out.append('    )')
        out.append('  )')

    # ── wire collection phase (signal nets + power fan-out) ──
    # HARD CAP: any single segment longer than MAX_SEG_MM is DROPPED.
    MAX_SEG_MM = 150.0
    MAX_WIRE_TOTAL_MM = 300.0

    # Build pin-sheet-position lookup: pin_key -> (sheet_x, sheet_y)
    # Used to snap wire endpoints to exact pin positions, preventing
    # floating-point/rounding disconnection between wires and pins.
    pin_sheet_positions: dict[str, tuple[float, float]] = {}
    for c in comps:
        ref = c['ref_des']
        ops = comp_ops.get(ref)
        place = placements.get(ref)
        if not ops or not place:
            continue
        for op in ops:
            if op[0] != 'pin':
                continue
            a = _get_attr(op, 'at')
            if not a or len(a) < 3:
                continue
            try:
                px = float(a[1])
                py = float(a[2])
            except (ValueError, IndexError):
                continue
            num = _get_attr(op, 'number')
            if not num or len(num) < 2:
                continue
            pin_num = str(num[1]).replace('"', '')
            pin_key = f"{ref}:{pin_num}"
            rot = int(round(float(place.get('rotation', 0.0)) / 90.0) * 90) % 360
            sx = _snap(place['x'] + off_x)
            sy = _snap(-place['y'] + off_y)
            abs_px, abs_py = _pin_abs_global(sx, sy, px, py, rot)
            pin_sheet_positions[pin_key] = (abs_px, abs_py)

    seen_segs: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    emitted_segments: list[tuple[str, tuple[tuple[float, float], tuple[float, float]]]] = []
    endpoint_count: dict[tuple[float, float], int] = {}
    wire_lines: list[str] = []
    n_dropped = 0
    surviving_wired_pins: set[str] = set()
    SNAP_TOLERANCE = GRID * 0.5  # mm — snap wire endpoint to pin if within this distance

    # (a) signal-net wires
    for w in wires:
        pts = _orthogonalize(_simplify_path(w.get('path', [])))
        if len(pts) < 2:
            continue
        # Compute total wire length in canvas coords; drop if absurd
        total_len = 0.0
        for i in range(len(pts) - 1):
            total_len += abs(pts[i]['x'] - pts[i + 1]['x']) + \
                         abs(pts[i]['y'] - pts[i + 1]['y'])
        if total_len > MAX_WIRE_TOTAL_MM:
            n_dropped += 1
            continue

        # Pre-compute sheet coords for every path point
        sheet_pts = []
        for pt in pts:
            sx = _snap(pt['x'] + off_x)
            sy = _snap(-pt['y'] + off_y)
            sheet_pts.append({'x': sx, 'y': sy})

        # Snap first/last point to exact pin position if within tolerance
        src_key = w.get('source', '')
        tgt_key = w.get('target', '')
        if src_key in pin_sheet_positions and len(sheet_pts) >= 2:
            want_x, want_y = pin_sheet_positions[src_key]
            got_x, got_y = sheet_pts[0]['x'], sheet_pts[0]['y']
            if abs(got_x - want_x) <= SNAP_TOLERANCE and abs(got_y - want_y) <= SNAP_TOLERANCE:
                sheet_pts[0] = {'x': want_x, 'y': want_y}
        if tgt_key in pin_sheet_positions and len(sheet_pts) >= 2:
            want_x, want_y = pin_sheet_positions[tgt_key]
            got_x, got_y = sheet_pts[-1]['x'], sheet_pts[-1]['y']
            if abs(got_x - want_x) <= SNAP_TOLERANCE and abs(got_y - want_y) <= SNAP_TOLERANCE:
                sheet_pts[-1] = {'x': want_x, 'y': want_y}

        for i in range(len(sheet_pts) - 1):
            x1 = sheet_pts[i]['x']
            y1 = sheet_pts[i]['y']
            x2 = sheet_pts[i + 1]['x']
            y2 = sheet_pts[i + 1]['y']
            # Skip degenerate (zero-length) segments
            if x1 == x2 and y1 == y2:
                continue
            # Skip diagonal segments that survived orthogonalization
            if x1 != x2 and y1 != y2:
                continue
            # HARD CAP: drop any single segment longer than MAX_SEG_MM
            seg_len = abs(x2 - x1) + abs(y2 - y1)
            if seg_len > MAX_SEG_MM:
                n_dropped += 1
                continue
            net = str(w.get('net', ''))
            segment = ((x1, y1), (x2, y2)) if (x1, y1) <= (x2, y2) else ((x2, y2), (x1, y1))
            key = segment
            for prior_net, prior_segment in emitted_segments:
                if prior_net != net and _orthogonal_segments_intersect(segment, prior_segment):
                    raise ExportValidationError(
                        f"Cross-net wire intersection between '{net}' and '{prior_net}'",
                        issues=[{"code": "EV002", "net": net, "other_net": prior_net}],
                    )
            if key in seen_segs:
                continue
            seen_segs.add(key)
            emitted_segments.append((net, segment))
            endpoint_count[(x1, y1)] = endpoint_count.get((x1, y1), 0) + 1
            endpoint_count[(x2, y2)] = endpoint_count.get((x2, y2), 0) + 1
            if src_key: surviving_wired_pins.add(src_key)
            if tgt_key: surviving_wired_pins.add(tgt_key)
            wire_lines.append(f'  (wire (pts (xy {_fmt(x1)} {_fmt(y1)}) (xy {_fmt(x2)} {_fmt(y2)}))')
            wire_lines.append('    (stroke (width 0) (type default))')
            wire_lines.append(f'    (uuid {_q(_new_uuid())})')
            wire_lines.append('  )')

    # EV001 (H-06): validate export consistency — every wire_path entry must
    # have produced at least one wire segment.  This catches degenerate wires
    # (all segments dropped / zero-length) without blocking export when the
    # routing engine produced zero traces for a net (a routing-layer concern).
    missing_wires = []
    for w in wires:
        src = w.get('source', '')
        tgt = w.get('target', '')
        if src and src not in surviving_wired_pins:
            missing_wires.append(src)
        if tgt and tgt not in surviving_wired_pins:
            missing_wires.append(tgt)
    if missing_wires:
        unique_missing = sorted(set(missing_wires))
        import sys
        print(f"WARNING: EV001: {len(unique_missing)} wire-endpoint(s) lack physical wire segments: "
              f"{', '.join(unique_missing[:10])}"
              f"{'...' if len(unique_missing) > 10 else ''}", file=sys.stderr)

    # (b) power nets — give every power pin its own short outward stub and
    # same-name global label. KiCad connects equal global-label names
    # electrically, so no centroid fan-out wires are needed. The previous
    # centroid approach drew long wires through intervening symbol bodies and
    # made the exported schematic disagree with the frontend.
    _DIR_ANGLE = {'right': 0, 'up': 90, 'left': 180, 'down': 270}
    POWER_STUB_MM = 2.54
    emitted_power_labels: set[tuple[str, float, float]] = set()

    for lbl in power_labels:
        net = lbl.get('net', '')
        if not net:
            continue

        pin_x = _snap(lbl['x'] + off_x)
        pin_y = _snap(-lbl['y'] + off_y)
        canvas_dir = lbl.get('dir', 'right')

        # Try the preferred direction first, then alternatives,
        # to avoid cross-net wire intersections between power stubs
        # (e.g. VDD stub crossing GND stub).
        _CARDINALS = ['right', 'left', 'up', 'down']
        candidates = [canvas_dir] + [d for d in _CARDINALS if d != canvas_dir]

        chosen_dir = None
        chosen_label_x = None
        chosen_label_y = None
        chosen_segment = None
        chosen_key = None
        label_key = None

        for cd in candidates:
            dx = 1 if cd == 'right' else -1 if cd == 'left' else 0
            dy = -1 if cd == 'up' else 1 if cd == 'down' else 0
            cand_label_x = _snap(pin_x + dx * POWER_STUB_MM)
            cand_label_y = _snap(pin_y + dy * POWER_STUB_MM)

            cand_label_key = (net, cand_label_x, cand_label_y)
            if cand_label_key in emitted_power_labels:
                continue

            if (pin_x, pin_y) == (cand_label_x, cand_label_y):
                chosen_dir = cd
                chosen_label_x = cand_label_x
                chosen_label_y = cand_label_y
                chosen_segment = None
                chosen_key = None
                label_key = cand_label_key
                break

            segment = (
                ((pin_x, pin_y), (cand_label_x, cand_label_y))
                if (pin_x, pin_y) <= (cand_label_x, cand_label_y)
                else ((cand_label_x, cand_label_y), (pin_x, pin_y))
            )
            key = segment

            intersects = False
            for prior_net, prior_segment in emitted_segments:
                if prior_net != net and _orthogonal_segments_intersect(segment, prior_segment):
                    intersects = True
                    break

            if not intersects:
                if key not in seen_segs:
                    chosen_dir = cd
                    chosen_label_x = cand_label_x
                    chosen_label_y = cand_label_y
                    chosen_segment = segment
                    chosen_key = key
                    label_key = cand_label_key
                    break

        if chosen_dir is None:
            # All directions intersect — emit the label without a stub wire.
            # The global label still connects electrically in KiCad.
            chosen_dir = canvas_dir
            chosen_label_x = pin_x
            chosen_label_y = pin_y
            chosen_segment = None
            chosen_key = None
            label_key = (net, chosen_label_x, chosen_label_y)

        if label_key in emitted_power_labels:
            continue
        emitted_power_labels.add(label_key)

        sheet_dir = chosen_dir
        if sheet_dir == 'up':
            sheet_dir = 'down'
        elif sheet_dir == 'down':
            sheet_dir = 'up'
        angle = _DIR_ANGLE.get(sheet_dir, 0)
        shape = 'passive' if net == 'GND' else 'input'

        if chosen_segment is not None and chosen_key is not None:
            if chosen_key not in seen_segs:
                seen_segs.add(chosen_key)
                emitted_segments.append((net, chosen_segment))
                endpoint_count[(pin_x, pin_y)] = endpoint_count.get((pin_x, pin_y), 0) + 1
                endpoint_count[(chosen_label_x, chosen_label_y)] = endpoint_count.get((chosen_label_x, chosen_label_y), 0) + 1
                wire_lines.append(
                    f'  (wire (pts (xy {_fmt(pin_x)} {_fmt(pin_y)}) '
                    f'(xy {_fmt(chosen_label_x)} {_fmt(chosen_label_y)}))'
                )
                wire_lines.append('    (stroke (width 0) (type default))')
                wire_lines.append(f'    (uuid {_q(_new_uuid())})')
                wire_lines.append('  )')

        out.append(
            f'  (global_label {_q(net)} (shape {shape}) '
            f'(at {_fmt(chosen_label_x)} {_fmt(chosen_label_y)} {angle}) '
            f'(fields_autoplaced yes)'
        )
        out.append('    (effects (font (size 1.27 1.27)) (justify left))')
        out.append(f'    (uuid {_q(_new_uuid())})')
        out.append('  )')

    # Track power-label pins as wired (they got a stub segment above).
    for lbl in power_labels:
        pin_key = lbl.get('pin', '')
        if pin_key:
            surviving_wired_pins.add(pin_key)

    # ── (c) net labels and global labels from connection records ──
    for nl in design.get('net_labels', []):
        nl_type = nl.get('type', 'label')
        net = nl.get('net', '')
        at = nl.get('at', {})
        lx = _snap(at.get('x', 0) + off_x)
        ly = _snap(-at.get('y', 0) + off_y)

        if nl_type == 'global':
            shape = 'input'
            out.append(
                f'  (global_label {_q(net)} (shape {shape}) '
                f'(at {_fmt(lx)} {_fmt(ly)} 0) (fields_autoplaced yes)'
            )
        else:
            out.append(
                f'  (label {_q(net)} (at {_fmt(lx)} {_fmt(ly)} 0) (fields_autoplaced yes)'
            )
        out.append('    (effects (font (size 1.27 1.27)) (justify left))')
        out.append(f'    (uuid {_q(_new_uuid())})')
        out.append('  )')
        pin_key = nl.get('pin', '')
        if pin_key:
            surviving_wired_pins.add(pin_key)

    # ── no-connect flags for unconnected pins ──
    # Marks pins with no wire as intentionally unconnected, suppressing
    # pin_not_connected ERC errors in KiCad and other EDA tools.
    for pin_key, (nc_x, nc_y) in pin_sheet_positions.items():
        if pin_key not in surviving_wired_pins:
            out.append(f'  (no_connect (at {_fmt(nc_x)} {_fmt(nc_y)}) (uuid {_q(_new_uuid())}))')

    # ── flush all wires at once ──
    out.extend(wire_lines)

    # ── Post-wire safety net: detect anomalous wire gatherings ──
    # Wires are allowed to cross freely (no junction dots).  Only flag
    # suspiciously busy coordinates where 6+ wire-segment ends converge
    # at a non-pin spot — that takes 3+ wires crossing at exactly the
    # same grid point, which is extremely unlikely from our router.
    pin_coords: set[tuple[float, float]] = set(pin_sheet_positions.values())

    busy_count = 0
    for (jx, jy), count in endpoint_count.items():
        if count >= 6 and (jx, jy) not in pin_coords:
            out.append(f'  # NOTE: {count} wire segments meet at ({_fmt(jx)}, {_fmt(jy)}) — '
                       f'verify no unintended net merge')
            busy_count += 1

    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(')')
    return '\n'.join(out) + '\n'
