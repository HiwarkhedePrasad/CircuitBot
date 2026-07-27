from __future__ import annotations

from agent.routing.constants import GRID_SIZE, PIN_STUB_LEN


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _rotation(value: float | int | None) -> int:
    """Normalize a schematic symbol rotation to a cardinal angle."""
    try:
        return int(round(float(value or 0) / 90.0) * 90) % 360
    except (TypeError, ValueError):
        return 0


def _rotate_point(x: float, y: float, rotation: float | int = 0) -> tuple[float, float]:
    """Rotate a library-space point into CircuitBot canvas space."""
    angle = _rotation(rotation)
    if angle == 90:
        return (y, -x)
    if angle == 180:
        return (-x, -y)
    if angle == 270:
        return (-y, x)
    return (x, y)


def _absolute_pin_position(pin: dict, component: dict) -> tuple[float, float]:
    """Return the rendered canvas position of a placed symbol pin.

    This is the single transform used by routing and editing.  It matches the
    renderer's symbol coordinate system, where raw symbol geometry is Y-up.
    """
    dx, dy = _rotate_point(
        float(pin.get('x', 0.0)),
        float(pin.get('y', 0.0)),
        component.get('rotation', 0),
    )
    return (_snap(float(component.get('x', 0.0)) + dx),
            _snap(float(component.get('y', 0.0)) + dy))


def _rotated_bbox(component: dict) -> dict | None:
    """Return a conservative axis-aligned keep-out for a rotated symbol."""
    bbox = component.get('bbox') or component.get('geom_bbox')
    if not bbox:
        return None
    corners = (
        (float(bbox['x']), float(bbox['y'])),
        (float(bbox['x']) + float(bbox['w']), float(bbox['y'])),
        (float(bbox['x']), float(bbox['y']) + float(bbox['h'])),
        (float(bbox['x']) + float(bbox['w']), float(bbox['y']) + float(bbox['h'])),
    )
    rotated = [_rotate_point(x, y, component.get('rotation', 0)) for x, y in corners]
    xs, ys = zip(*rotated)
    return {
        'x': min(xs),
        'y': min(ys),
        'w': max(xs) - min(xs),
        'h': max(ys) - min(ys),
    }


def _orthogonal_segments_intersect(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
    tolerance: float = 1e-6,
) -> bool:
    """Return whether two horizontal/vertical segments touch or overlap."""
    (ax1, ay1), (ax2, ay2) = first
    (bx1, by1), (bx2, by2) = second
    a_vertical = abs(ax1 - ax2) <= tolerance
    b_vertical = abs(bx1 - bx2) <= tolerance

    def _contains(value, start, end):
        return min(start, end) - tolerance <= value <= max(start, end) + tolerance

    if a_vertical and b_vertical:
        return abs(ax1 - bx1) <= tolerance and not (
            max(ay1, ay2) < min(by1, by2) - tolerance or
            max(by1, by2) < min(ay1, ay2) - tolerance
        )
    if not a_vertical and not b_vertical:
        return abs(ay1 - by1) <= tolerance and not (
            max(ax1, ax2) < min(bx1, bx2) - tolerance or
            max(bx1, bx2) < min(ax1, ax2) - tolerance
        )
    if a_vertical:
        return _contains(ax1, bx1, bx2) and _contains(by1, ay1, ay2)
    return _contains(bx1, ax1, ax2) and _contains(ay1, by1, by2)


def _pin_direction(pin: dict) -> str:
    """Return the cardinal direction a wire should leave a pin.

    The pin angle defines which way the pin STUB points in KiCad
    (0=right, 90=up, 180=left, 270=down).  The wire should exit in
    the SAME direction that the pin stub points so the first segment
    clears the component body before turning.
    """
    ang = pin.get('angle')
    if ang is None:
        ang = 0
    try:
        ang = int(round(float(ang))) % 360
    except (TypeError, ValueError):
        ang = 0
    if 45 <= ang < 135:
        return 'up'
    if 135 <= ang < 225:
        return 'left'
    if 225 <= ang < 315:
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
