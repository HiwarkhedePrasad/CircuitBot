"""KiCad schematic (.kicad_sch) exporter.

Converts the agent's design state (selected components, parsed symbol ops,
placements and routed wire paths) into a KiCad 8 compatible schematic file.

Coordinate system note:
- CircuitBot's canvas uses Y growing the same direction as the symbol files.
- KiCad schematic sheets use Y growing DOWNWARD, while symbol-local
  coordinates grow UPWARD. A pin at local (px, py) of a symbol placed at
  (X, Y) lands at sheet position (X + px, Y - py).
- Therefore: sheet_x = canvas_x + offset, sheet_y = -canvas_y + offset.
"""
from __future__ import annotations

import re
import uuid

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
        props['Reference'] = _default_prop('Reference', 'U')
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
    """Collapse collinear runs of grid cells into corner points only."""
    if len(points) < 3:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = out[-1]['x'], out[-1]['y']
        x1, y1 = points[i]['x'], points[i]['y']
        x2, y2 = points[i + 1]['x'], points[i + 1]['y']
        # Keep the point only if direction changes
        if (x1 - x0) * (y2 - y1) != (y1 - y0) * (x2 - x1):
            out.append(points[i])
    out.append(points[-1])
    return out


# ── main entry point ─────────────────────────────────────────────────────────


def generate_kicad_sch(design: dict) -> str:
    """Generate a complete .kicad_sch document.

    Expects a design dict with keys:
    - selected_components: [{id_str, ref_des, category, description}]
    - component_ops: {ref_des: ops}
    - component_placements: [{ref_des, x, y}]
    - wire_paths: [{source, target, path: [{x, y}]}]
    """
    comps = design.get('selected_components', [])
    comp_ops = design.get('component_ops', {})
    placements = {p['ref_des']: p for p in design.get('component_placements', [])}
    wires = design.get('wire_paths', [])

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
    min_x = min(xs) if xs else 0.0
    min_y = min(ys) if ys else 0.0
    off_x = _snap(50.8 - min_x)
    off_y = _snap(50.8 - min_y)

    out = []
    out.append('(kicad_sch (version 20231120) (generator "circuitbot") (generator_version "1.0")')
    out.append(f'  (uuid {_q(root_uuid)})')
    out.append('  (paper "A3")')

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
        name = c['id_str'].partition(':')[2]
        sym_uuid = _new_uuid()

        out.append(f'  (symbol (lib_id {_q(c["id_str"])}) (at {_fmt(sx)} {_fmt(sy)} 0) (unit 1)')
        out.append('    (in_bom yes) (on_board yes) (dnp no)')
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

    # ── wires (signal nets only) ──
    # Collect segment endpoints to detect junctions (3+ wires meeting at a point)
    endpoint_count = {}
    wire_lines = []
    for w in wires:
        pts = _simplify_path(w.get('path', []))
        for i in range(len(pts) - 1):
            x1 = _snap(pts[i]['x'] + off_x)
            y1 = _snap(-pts[i]['y'] + off_y)
            x2 = _snap(pts[i + 1]['x'] + off_x)
            y2 = _snap(-pts[i + 1]['y'] + off_y)
            if x1 == x2 and y1 == y2:
                continue
            endpoint_count[(x1, y1)] = endpoint_count.get((x1, y1), 0) + 1
            endpoint_count[(x2, y2)] = endpoint_count.get((x2, y2), 0) + 1
            wire_lines.append(f'  (wire (pts (xy {_fmt(x1)} {_fmt(y1)}) (xy {_fmt(x2)} {_fmt(y2)}))')
            wire_lines.append('    (stroke (width 0) (type default))')
            wire_lines.append(f'    (uuid {_q(_new_uuid())})')
            wire_lines.append('  )')
    out.extend(wire_lines)

    # ── junction dots where 3+ wire ends meet ──
    for (jx, jy), count in endpoint_count.items():
        if count >= 3:
            out.append(f'  (junction (at {_fmt(jx)} {_fmt(jy)}) (diameter 0) (color 0 0 0 0)')
            out.append(f'    (uuid {_q(_new_uuid())})')
            out.append('  )')

    # ── power / GND global labels (proper named nets, no routed wires) ──
    _DIR_ANGLE = {'right': 0, 'up': 90, 'left': 180, 'down': 270}
    for lbl in design.get('power_labels', []):
        lx = _snap(lbl['x'] + off_x)
        ly = _snap(-lbl['y'] + off_y)
        # Canvas "up" becomes sheet "down" after the Y flip
        d = lbl.get('dir', 'right')
        if d == 'up':
            d = 'down'
        elif d == 'down':
            d = 'up'
        angle = _DIR_ANGLE.get(d, 0)
        shape = 'passive' if lbl['net'] == 'GND' else 'input'
        out.append(f'  (global_label {_q(lbl["net"])} (shape {shape}) (at {_fmt(lx)} {_fmt(ly)} {angle}) (fields_autoplaced yes)')
        out.append('    (effects (font (size 1.27 1.27)) (justify left))')
        out.append(f'    (uuid {_q(_new_uuid())})')
        out.append('  )')

    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(')')
    return '\n'.join(out) + '\n'