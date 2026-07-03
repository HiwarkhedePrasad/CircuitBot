from __future__ import annotations

from agent.routing.constants import BBOX_CLEARANCE, GRID_SIZE
from agent.routing.geometry import _snap


def _candidate_straight(s_pos, s_stub, t_pos, t_stub):
    if abs(s_stub[0] - t_stub[0]) < 1e-3 or abs(s_stub[1] - t_stub[1]) < 1e-3:
        return [[s_pos, s_stub, t_stub, t_pos]]
    return []


def _candidate_L(s_pos, s_stub, t_pos, t_stub):
    cands = []
    cands.append([s_pos, s_stub, (t_stub[0], s_stub[1]), t_stub, t_pos])
    cands.append([s_pos, s_stub, (s_stub[0], t_stub[1]), t_stub, t_pos])
    return cands


def _candidate_Z(s_pos, s_stub, t_pos, t_stub, components):
    cands = []
    x_levels = {s_stub[0], t_stub[0], _snap((s_stub[0] + t_stub[0]) / 2)}
    y_levels = {s_stub[1], t_stub[1], _snap((s_stub[1] + t_stub[1]) / 2)}

    for c in components:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            continue
        cx, cy = c['x'], c['y']
        x_left = _snap(cx + bbox['x'] - BBOX_CLEARANCE - 1.27)
        x_right = _snap(cx + bbox['x'] + bbox['w'] + BBOX_CLEARANCE + 1.27)
        x_levels.add(x_left)
        x_levels.add(x_right)
        y_bottom = _snap(cy + bbox['y'] - BBOX_CLEARANCE - 1.27)
        y_top = _snap(cy + bbox['y'] + bbox['h'] + BBOX_CLEARANCE + 1.27)
        y_levels.add(y_bottom)
        y_levels.add(y_top)

    for mid_x in x_levels:
        cands.append([s_pos, s_stub, (mid_x, s_stub[1]),
                      (mid_x, t_stub[1]), t_stub, t_pos])
    for mid_y in y_levels:
        cands.append([s_pos, s_stub, (s_stub[0], mid_y),
                      (t_stub[0], mid_y), t_stub, t_pos])
    return cands


def _candidate_U(s_pos, s_stub, t_pos, t_stub, components):
    cands = []
    x_levels = {_snap(s_stub[0]), _snap(t_stub[0])}
    y_levels = {_snap(s_stub[1]), _snap(t_stub[1])}

    for c in components:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            continue
        cx, cy = c['x'], c['y']
        x_levels.add(_snap(cx + bbox['x'] - BBOX_CLEARANCE - 2.54))
        x_levels.add(_snap(cx + bbox['x'] + bbox['w'] + BBOX_CLEARANCE + 2.54))
        y_levels.add(_snap(cy + bbox['y'] - BBOX_CLEARANCE - 2.54))
        y_levels.add(_snap(cy + bbox['y'] + bbox['h'] + BBOX_CLEARANCE + 2.54))

    for bypass_x in x_levels:
        for bypass_y in y_levels:
            cands.append([s_pos, s_stub,
                          (s_stub[0], bypass_y), (bypass_x, bypass_y),
                          (bypass_x, t_stub[1]), t_stub, t_pos])
            cands.append([s_pos, s_stub,
                          (bypass_x, s_stub[1]), (bypass_x, bypass_y),
                          (t_stub[0], bypass_y), t_stub, t_pos])
    return cands
