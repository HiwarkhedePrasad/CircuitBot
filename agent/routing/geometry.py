from __future__ import annotations

from agent.routing.constants import GRID_SIZE, PIN_STUB_LEN


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _pin_direction(pin: dict) -> str:
    ang = pin.get('angle')
    if ang is None:
        ang = 0
    try:
        ang = int(round(float(ang))) % 360
    except (TypeError, ValueError):
        ang = 0
    exit_ang = (ang + 180) % 360
    if 45 <= exit_ang < 135:
        return 'up'
    if 135 <= exit_ang < 225:
        return 'left'
    if 225 <= exit_ang < 315:
        return 'down'
    return 'right'


def _stub_point(px: float, py: float, direction: str,
                length: float = PIN_STUB_LEN) -> tuple[float, float]:
    if direction == 'left':
        return (_snap(px - length), _snap(py))
    if direction == 'up':
        return (_snap(px), _snap(py + length))
    if direction == 'down':
        return (_snap(px), _snap(py - length))
    return (_snap(px + length), _snap(py))


def _seg_intersects_bbox(p1: tuple[float, float],
                         p2: tuple[float, float],
                         bbox: dict,
                         cx: float, cy: float,
                         margin: float = 0.0) -> bool:
    left   = cx + bbox['x'] - margin
    right  = left + bbox['w'] + 2 * margin
    top    = cy + bbox['y'] - margin
    bottom = top + bbox['h'] + 2 * margin

    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1

    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - left), (dx, right - x1),
                 (-dy, y1 - top),  (dy, bottom - y1)):
        if abs(p) < 1e-9:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return t0 < t1 - 1e-9
