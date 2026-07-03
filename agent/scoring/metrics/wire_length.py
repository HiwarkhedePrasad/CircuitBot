"""Wire length metric — sum of Manhattan distances across all routes."""

from agent.scoring.metrics import placement_metric, routing_metric


def _manhattan(pts: list) -> float:
    total = 0.0
    for i in range(len(pts) - 1):
        if isinstance(pts[i], dict):
            total += abs(pts[i + 1]["x"] - pts[i]["x"]) + abs(pts[i + 1]["y"] - pts[i]["y"])
        else:
            total += abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
    return total


@placement_metric
class EstimatedWireLengthMetric:
    name = "estimated_wire_length"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        total = 0.0
        for conn in netlist:
            sr = conn["source"]
            tr = conn["target"]
            s_pin = pin_matrix.get(sr)
            t_pin = pin_matrix.get(tr)
            s_comp = next((c for c in components if c["ref_des"] == sr.split(":")[0]), None)
            t_comp = next((c for c in components if c["ref_des"] == tr.split(":")[0]), None)
            if s_pin and t_pin and s_comp and t_comp:
                sx = s_comp["x"] + s_pin["x"]
                sy = s_comp["y"] + s_pin["y"]
                tx = t_comp["x"] + t_pin["x"]
                ty = t_comp["y"] + t_pin["y"]
                total += abs(sx - tx) + abs(sy - ty)
        return total


@routing_metric
class ActualWireLengthMetric:
    name = "actual_wire_length"

    def evaluate(self, components, placements, wires, netlist) -> float:
        total = 0.0
        for w in wires:
            pts = w.get("points") or w.get("path", [])
            total += _manhattan(pts)
        return total
