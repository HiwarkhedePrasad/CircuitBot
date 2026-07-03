"""Wire bend metric — total number of direction changes across all routes."""

from agent.scoring.metrics import routing_metric


def _count_bends(routes: list[dict]) -> int:
    bends = 0
    for r in routes:
        pts = r.get("points") or r.get("path", [])
        if pts and isinstance(pts[0], dict):
            pts = [(p["x"], p["y"]) for p in pts]
        for i in range(1, len(pts) - 1):
            dx1 = pts[i][0] - pts[i - 1][0]
            dy1 = pts[i][1] - pts[i - 1][1]
            dx2 = pts[i + 1][0] - pts[i][0]
            dy2 = pts[i + 1][1] - pts[i][1]
            if abs(dx1 - dx2) > 1e-3 or abs(dy1 - dy2) > 1e-3:
                bends += 1
    return bends


@routing_metric
class WireBendsMetric:
    name = "wire_bends"

    def evaluate(self, components, placements, wires, netlist) -> float:
        return float(_count_bends(wires))
