"""Overlap metric — count intersecting component bounding boxes."""

from agent.scoring.metrics import placement_metric


@placement_metric
class OverlapMetric:
    name = "overlap"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        count = 0
        for i in range(len(components)):
            a = components[i]
            ab = a.get("bbox", {})
            ax1 = a["x"] + ab.get("x", 0)
            ay1 = a["y"] + ab.get("y", 0)
            ax2 = ax1 + ab.get("w", 0)
            ay2 = ay1 + ab.get("h", 0)
            for j in range(i + 1, len(components)):
                b = components[j]
                bb = b.get("bbox", {})
                bx1 = b["x"] + bb.get("x", 0)
                by1 = b["y"] + bb.get("y", 0)
                bx2 = bx1 + bb.get("w", 0)
                by2 = by1 + bb.get("h", 0)
                if ax2 > bx1 and ax1 < bx2 and ay2 > by1 and ay1 < by2:
                    count += 1
        return float(count)
