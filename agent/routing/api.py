"""Standalone routing API — routes netlist connections between placed components."""

from __future__ import annotations

from typing import Optional

from agent.placement import PLACEMENT_ENGINE as PLACEMENT_MODE
from agent.routing.constants import (
    MAX_WIRE_MANHATTAN, MAX_COLLISIONS, GRID_SIZE,
    MATRIX_SIZE, MATRIX_OFFSET,
)
from agent.routing.geometry import (
    _snap, _pin_direction, _absolute_pin_position,
    _stub_point, _orthogonal_segments_intersect,
)
from agent.routing.path_utils import _clean_path, _is_orthogonal, _path_length
from agent.routing.collision import _path_collisions
from agent.routing.make_path import make_path


def _prune_disconnected_net_islands(traces: list[dict], netlist: list[dict]
                                    ) -> tuple[list[dict], list[tuple[str, str]]]:
    if not traces:
        return traces, []

    expected_refs_by_net: dict[str, set[str]] = {}
    for conn in netlist:
        net = conn.get('net', '')
        if not net:
            continue
        expected_refs_by_net.setdefault(net, set()).update({
            conn.get('source', '').split(':')[0],
            conn.get('target', '').split(':')[0],
        })
    for refs in expected_refs_by_net.values():
        refs.discard('')

    by_net: dict[str, list[dict]] = {}
    for tr in traces:
        net = tr.get('net', '')
        if net:
            by_net.setdefault(net, []).append(tr)

    keep_ids: set[int] = set()
    pruned_pairs: list[tuple[str, str]] = []

    for net, net_traces in by_net.items():
        expected = expected_refs_by_net.get(net, set())
        if len(expected) <= 2:
            for tr in net_traces:
                keep_ids.add(id(tr))
            continue

        adj: dict[str, set[str]] = {ref: set() for ref in expected}
        trace_refs: list[tuple[dict, str, str]] = []
        for tr in net_traces:
            s_ref = tr.get('source', '').split(':')[0]
            t_ref = tr.get('target', '').split(':')[0]
            if not s_ref or not t_ref:
                continue
            trace_refs.append((tr, s_ref, t_ref))
            if s_ref in adj and t_ref in adj:
                adj[s_ref].add(t_ref)
                adj[t_ref].add(s_ref)

        visited: set[str] = set()
        components: list[set[str]] = []
        for ref in expected:
            if ref in visited or not adj.get(ref):
                continue
            stack = [ref]
            comp_set: set[str] = set()
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                comp_set.add(cur)
                for nxt in adj.get(cur, ()):
                    if nxt not in visited:
                        stack.append(nxt)
            if comp_set:
                components.append(comp_set)

        if len(components) <= 1:
            for tr in net_traces:
                keep_ids.add(id(tr))
            continue

        components.sort(key=lambda s: (-len(s), sorted(s)))
        main_comp = components[0]
        for tr, s_ref, t_ref in trace_refs:
            if s_ref in main_comp and t_ref in main_comp:
                keep_ids.add(id(tr))
            else:
                pruned_pairs.append((s_ref, t_ref))

    filtered = []
    for tr in traces:
        net = tr.get('net', '')
        if not net:
            filtered.append(tr)
            continue
        if id(tr) in keep_ids:
            filtered.append(tr)

    return filtered, pruned_pairs


def count_crossings(routes: list[dict]) -> int:
    segments: list[tuple[float, float, float, float]] = []
    for r in routes:
        pts = r.get('points', [])
        for i in range(len(pts) - 1):
            segments.append((pts[i][0], pts[i][1],
                             pts[i + 1][0], pts[i + 1][1]))

    def _orient(ax, ay, bx, by, cx, cy) -> int:
        v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(v) < 1e-12:
            return 0
        return 1 if v > 0 else -1

    def _on_seg(ax, ay, bx, by, cx, cy) -> bool:
        return (min(ax, bx) <= cx <= max(ax, bx) and
                min(ay, by) <= cy <= max(ay, by))

    count = 0
    for i in range(len(segments)):
        ax1, ay1, ax2, ay2 = segments[i]
        for j in range(i + 1, len(segments)):
            bx1, by1, bx2, by2 = segments[j]
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
            elif o1 == 0 and _on_seg(ax1, ay1, ax2, ay2, bx1, by1):
                continue
            elif o2 == 0 and _on_seg(ax1, ay1, ax2, ay2, bx2, by2):
                continue
            elif o3 == 0 and _on_seg(bx1, by1, bx2, by2, ax1, ay1):
                continue
            elif o4 == 0 and _on_seg(bx1, by1, bx2, by2, ax2, ay2):
                continue
    return count


def log_placement_metrics(components: list[dict], routes: list[dict],
                          dropped_pairs: list[tuple[str, str]]) -> dict:
    total_wire_len = 0.0
    pts_routes: list[list] = []
    for r in routes:
        pts = r.get('points') or r.get('path', [])
        if pts and isinstance(pts[0], dict):
            pts = [(p['x'], p['y']) for p in pts]
        pts_routes.append(pts)
        for i in range(len(pts) - 1):
            dx = abs(pts[i + 1][0] - pts[i][0])
            dy = abs(pts[i + 1][1] - pts[i][1])
            total_wire_len += dx + dy

    crossings = count_crossings(
        [{'points': p} for p in pts_routes])

    metrics = {
        'total_wire_length': round(total_wire_len, 2),
        'crossings': crossings,
        'dropped_wires': len(dropped_pairs),
        'n_components': len(components),
    }

    logger = __import__('logging').getLogger(__name__)
    logger.info(
        '[PLACEMENT METRICS]  %s  |  drops=%d  cross=%d  '
        'wire=%.1f  n=%d',
        PLACEMENT_MODE,
        metrics['dropped_wires'],
        metrics['crossings'],
        metrics['total_wire_length'],
        metrics['n_components'],
    )

    return metrics


def repair_placement_for_routing(components: list[dict],
                                 dropped_pairs: list[tuple[str, str]]) -> int:
    if not dropped_pairs:
        return 0
    moved: set[str] = set()

    def _get_comp(ref_des: str):
        for c in components:
            if c['ref_des'] == ref_des:
                return c
        return None

    for src_ref, tgt_ref in dropped_pairs:
        for sat_ref, ic_ref in [(src_ref, tgt_ref), (tgt_ref, src_ref)]:
            sat = _get_comp(sat_ref)
            ic = _get_comp(ic_ref)
            if not sat or not ic:
                continue
            if sat['tier'] != -1 or ic['tier'] < 0:
                continue
            tight_gap = max(GRID_SIZE, 12.70 * 0.3)
            new_x = (ic['x'] + ic['bbox']['x'] + ic['width'] +
                     tight_gap - sat['bbox']['x'])
            icx = ic['x'] + ic['bbox']['x'] + ic['width'] / 2
            icy = ic['y'] + ic['bbox']['y'] + ic['height'] / 2
            new_y = _snap(icy - sat['bbox']['y'] - sat['height'] / 2)
            sat['x'] = _snap(new_x)
            sat['y'] = new_y
            moved.add(sat_ref)

    return len(moved)


def _pin_lookup(pin_matrix: dict) -> dict[str, str]:
    return {str(k).lower(): k for k in pin_matrix}


def _resolve_pin(pin_matrix: dict, pin_lookup: dict[str, str], key: str) -> Optional[dict]:
    pin = pin_matrix.get(key)
    if pin is not None:
        return pin
    alt = pin_lookup.get(str(key).lower())
    if alt is None:
        return None
    return pin_matrix.get(alt)


def _placement_map(component_placements: list[dict]) -> dict[str, dict]:
    return {
        str(comp.get('ref_des', '')): comp
        for comp in component_placements
        if comp.get('ref_des')
    }


def _abs_pin_position(pin_key: str, pin_matrix: dict,
                      component_placements: list[dict]) -> Optional[tuple[float, float]]:
    pin_lookup = _pin_lookup(pin_matrix)
    placements = _placement_map(component_placements)
    ref = str(pin_key or '').split(':')[0]
    if not ref:
        return None
    pin = _resolve_pin(pin_matrix, pin_lookup, pin_key)
    component = placements.get(ref)
    if pin is None or component is None:
        return None
    return _absolute_pin_position(pin, component)


def _pin_exit_direction(pin_key: str, pin_matrix: dict,
                         placements: dict[str, dict]) -> str:
    """Return the cardinal direction a wire should leave a pin anchor.

    In KiCad the pin angle defines which way the pin STUB points
    (0=right, 90=up, 180=left, 270=down).  The wire should exit in
    the SAME direction that the pin stub points — first segment runs
    alongside the pin line so it clears the component body before
    making any turns.
    """
    ref = str(pin_key or '').split(':')[0]
    if not ref:
        return 'right'
    pin_lookup = _pin_lookup(pin_matrix)
    pin = _resolve_pin(pin_matrix, pin_lookup, pin_key)
    if pin is None:
        return 'right'
    ang = float(pin.get('angle', 0))
    comp = placements.get(ref)
    if comp:
        ang = (ang + float(comp.get('rotation', 0))) % 360
    a = int(round(ang)) % 360
    if 45 <= a < 135:
        return 'up'
    if 135 <= a < 225:
        return 'left'
    if 225 <= a < 315:
        return 'down'
    return 'right'


def _route_via_stubs(s_pos: tuple[float, float], s_stub: tuple[float, float],
                     t_pos: tuple[float, float], t_stub: tuple[float, float],
                     ) -> list[dict]:
    """Orthogonal path from s_pos through stub points to t_pos.

    Path topology: s_pos → s_stub → (midpoint routing) → t_stub → t_pos

    This ensures every wire exits the source pin in the correct direction
    and approaches the target pin from the correct direction, with no
    path segments overlapping the component body.
    """
    sa, sb = _snap(s_pos[0]), _snap(s_pos[1])
    ta, tb = _snap(t_pos[0]), _snap(t_pos[1])

    if abs(s_stub[0] - t_stub[0]) < 1e-3 or abs(s_stub[1] - t_stub[1]) < 1e-3:
        points = [(sa, sb), s_stub, t_stub, (ta, tb)]
    else:
        mid_x = _snap((s_stub[0] + t_stub[0]) / 2.0)
        points = [(sa, sb), s_stub, (mid_x, s_stub[1]),
                  (mid_x, t_stub[1]), t_stub, (ta, tb)]
    cleaned = _clean_path(points)
    if len(cleaned) < 2:
        cleaned = [(sa, sb), (ta, tb)]
    return [{'x': p[0], 'y': p[1]} for p in cleaned]


def _canonicalize_wire(wire: dict, pin_matrix: dict,
                       component_placements: list[dict]) -> dict:
    source = wire.get('source', '')
    target = wire.get('target', '')
    start = _abs_pin_position(source, pin_matrix, component_placements)
    end = _abs_pin_position(target, pin_matrix, component_placements)
    if start is None or end is None:
        path = wire.get('path', [])
    else:
        placements = _placement_map(component_placements)
        s_dir = _pin_exit_direction(source, pin_matrix, placements)
        t_dir = _pin_exit_direction(target, pin_matrix, placements)
        s_stub = _stub_point(*start, s_dir)
        t_stub = _stub_point(*end, t_dir)
        path = _route_via_stubs(start, s_stub, end, t_stub)
    out = dict(wire)
    out['path'] = path
    return out


def _canonicalize_wire_paths(wire_paths: list[dict], pin_matrix: dict,
                             component_placements: list[dict]) -> list[dict]:
    if not pin_matrix or not component_placements:
        return wire_paths
    return [_canonicalize_wire(wire, pin_matrix, component_placements) for wire in wire_paths]


def apply_schematic_edit(wire_paths: list[dict], event: dict, netlist: list[dict],
                         pin_matrix: Optional[dict] = None,
                         component_placements: Optional[list[dict]] = None) -> list[dict]:
    """Apply a schematic edit event and return clean, pruned wire paths.

    Caller owns persistence — this function only transforms the wire list.
    """
    etype = event.get('edit_event_type', '')
    pin_matrix = pin_matrix or {}
    component_placements = component_placements or []
    if etype in ('schematic_add_wire', 'add_wire'):
        source = event.get('source') or event.get('source_pin', '')
        target = event.get('target') or event.get('target_pin', '')
        wire_id = event.get('wire_id') or event.get('edit_event_id', '')
        net = event.get('net', '')
        wire_paths = [w for w in wire_paths if w.get('wire_id') != wire_id]
        wire_paths.append(_canonicalize_wire({
            'wire_id': wire_id, 'source': source, 'target': target,
            'net': net, 'path': event.get('path', []), 'manual': True,
        }, pin_matrix, component_placements))
        pruned, _ = _prune_disconnected_net_islands(wire_paths, netlist)
        return pruned
    elif etype in ('schematic_delete_wire', 'delete_wire'):
        wire_id = event.get('wire_id')
        source = event.get('source') or event.get('source_pin', '')
        target = event.get('target') or event.get('target_pin', '')
        if wire_id:
            wire_paths = [w for w in wire_paths if w.get('wire_id') != wire_id]
        elif source and target:
            pair = {source, target}
            wire_paths = [w for w in wire_paths
                          if {w.get('source', ''), w.get('target', '')} != pair]
        pruned, _ = _prune_disconnected_net_islands(wire_paths, netlist)
        return pruned
    elif etype in ('schematic_move_component', 'edit_schematic_component_location'):
        return _canonicalize_wire_paths(wire_paths, pin_matrix, component_placements)
    return wire_paths


def _segment_points(a: tuple[float, float], b: tuple[float, float]) -> set[tuple[float, float]]:
    """Return every grid vertex occupied by an orthogonal wire segment."""
    points = set()
    if abs(a[0] - b[0]) < 1e-6:
        step = GRID_SIZE if b[1] >= a[1] else -GRID_SIZE
        y = a[1]
        while (y <= b[1] + 1e-6 if step > 0 else y >= b[1] - 1e-6):
            points.add((_snap(a[0]), _snap(y)))
            y += step
    elif abs(a[1] - b[1]) < 1e-6:
        step = GRID_SIZE if b[0] >= a[0] else -GRID_SIZE
        x = a[0]
        while (x <= b[0] + 1e-6 if step > 0 else x >= b[0] - 1e-6):
            points.add((_snap(x), _snap(a[1])))
            x += step
    return points


def _path_vertices(path: list[tuple[float, float]]) -> set[tuple[float, float]]:
    vertices = set()
    for a, b in zip(path, path[1:]):
        vertices.update(_segment_points(a, b))
    return vertices


def _path_segments(path: list[tuple[float, float]]):
    return list(zip(path, path[1:]))


def _paths_intersect(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> bool:
    """Different nets may not cross or share any schematic wire geometry."""
    return any(
        _orthogonal_segments_intersect(a, b)
        for a in _path_segments(first)
        for b in _path_segments(second)
    )


def route_traces(components: list[dict], netlist: list, pin_matrix: dict,
                  erc_retries: int = 0, existing_traces: Optional[list[dict]] = None,
                  ) -> tuple[list[dict], list[tuple[str, str]]]:
    components_by_ref = {c['ref_des']: c for c in components}
    traces: list[dict] = []
    dropped_pairs: list[tuple[str, str]] = []

    pin_matrix_lower: dict[str, str] = {}
    for k in pin_matrix:
        pin_matrix_lower[k.lower()] = k

    def _resolve_pin(key: str) -> Optional[dict]:
        pin = pin_matrix.get(key)
        if pin is not None:
            return pin
        alt = pin_matrix_lower.get(key.lower())
        if alt is not None:
            return pin_matrix[alt]
        return None

    def _abs(key: str) -> Optional[tuple[float, float]]:
        ref = key.split(':')[0]
        if not ref:
            return None
        pin = _resolve_pin(key)
        component = components_by_ref.get(ref)
        if pin is None or component is None:
            return None
        return _absolute_pin_position(pin, component)

    def _dir(key: str) -> str:
        pin = dict(_resolve_pin(key) or {})
        ref = key.split(':')[0]
        component = components_by_ref.get(ref, {})
        pin['angle'] = (float(pin.get('angle', 0)) + float(component.get('rotation', 0))) % 360
        return _pin_direction(pin)

    def _mhd(conn) -> float:
        s = _abs(conn['source'])
        t = _abs(conn['target'])
        if not s or not t:
            return float('inf')
        return abs(s[0] - t[0]) + abs(s[1] - t[1])

    # Never trade electrical/geometric safety for retry progress. Retries may
    # search farther, but a route through a symbol remains invalid.
    max_collisions = MAX_COLLISIONS
    max_wire = MAX_WIRE_MANHATTAN * (1.5 + erc_retries * 0.5)

    max_allowed = max_wire
    routable = [c for c in netlist if _mhd(c) <= max_allowed]
    for c in netlist:
        if _mhd(c) > max_allowed:
            dropped_pairs.append((
                c['source'].split(':')[0],
                c['target'].split(':')[0],
            ))

    occupied = list(existing_traces or [])
    for conn in sorted(routable, key=_mhd):
        s_pos = _abs(conn['source'])
        t_pos = _abs(conn['target'])
        if not s_pos or not t_pos:
            src_ref = conn['source'].split(':')[0] if conn.get('source') else '?'
            tgt_ref = conn['target'].split(':')[0] if conn.get('target') else '?'
            dropped_pairs.append((src_ref, tgt_ref))
            continue
        if s_pos == t_pos:
            continue

        s_dir = _dir(conn['source'])
        t_dir = _dir(conn['target'])
        src_ref = conn['source'].split(':')[0]
        tgt_ref = conn['target'].split(':')[0]

        blocked_vertices = set()
        forbidden_segments = []
        for trace in occupied:
            if trace.get('net', '') != conn.get('net', ''):
                prior = [(p['x'], p['y']) for p in trace.get('path', [])]
                blocked_vertices.update(_path_vertices(prior))
                forbidden_segments.extend(_path_segments(prior))
        path = make_path(s_pos, s_dir, t_pos, t_dir, components, src_ref, tgt_ref,
                         blocked_vertices=blocked_vertices,
                         forbidden_segments=forbidden_segments)

        dropped = False
        if not path:
            dropped = True
        elif len(path) < 2:
            dropped = True
        elif not _is_orthogonal(path):
            dropped = True
        elif _path_length(path) > max_wire:
            dropped = True
        elif _path_collisions(path, components, src_ref, tgt_ref) > max_collisions:
            dropped = True
        elif any(
            trace.get('net', '') != conn.get('net', '') and
            _paths_intersect(path, [(p['x'], p['y']) for p in trace.get('path', [])])
            for trace in occupied
        ):
            dropped = True

        if dropped:
            dropped_pairs.append((src_ref, tgt_ref))
            continue

        trace = {
            'source': conn['source'],
            'target': conn['target'],
            'net': conn.get('net', ''),
            'path': [{'x': p[0], 'y': p[1]} for p in path],
        }
        traces.append(trace)
        occupied.append(trace)

    traces, pruned_pairs = _prune_disconnected_net_islands(traces, netlist)
    dropped_pairs.extend(pruned_pairs)

    seen: set[tuple[str, str]] = set()
    deduped = []
    for pair in dropped_pairs:
        key = tuple(sorted(pair))
        if key not in seen:
            seen.add(key)
            deduped.append(pair)
    return traces, deduped
