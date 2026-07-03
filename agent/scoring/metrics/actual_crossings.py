"""Actual crossing metric — orientation-based segment intersection count."""

from agent.scoring.metrics import routing_metric


def _count_crossings(routes: list[dict]) -> int:
    segments: list[tuple] = []
    for r in routes:
        pts = r.get("points") or r.get("path", [])
        if pts and isinstance(pts[0], dict):
            pts = [(p["x"], p["y"]) for p in pts]
        for i in range(len(pts) - 1):
            segments.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))

    def _orient(ax, ay, bx, by, cx, cy) -> int:
        v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(v) < 1e-12:
            return 0
        return 1 if v > 0 else -1

    def _on_seg(ax, ay, bx, by, cx, cy) -> bool:
        return (min(ax, bx) <= cx <= max(ax, bx) and
                min(ay, by) <= cy <= max(ay, by))

    count = 0
    for i, seg_a in enumerate(segments):
        ax1, ay1, ax2, ay2 = seg_a
        for seg_b in segments[i + 1:]:
            bx1, by1, bx2, by2 = seg_b
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
    return count


@routing_metric
class ActualCrossingsMetric:
    name = "actual_crossings"

    def evaluate(self, components, placements, wires, netlist) -> float:
        return float(_count_crossings(wires))
