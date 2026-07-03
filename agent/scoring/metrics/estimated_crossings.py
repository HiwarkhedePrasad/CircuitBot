"""Estimated crossing metric — probabilistic crossing count without routing.

Uses relative ordering heuristic: for any two nets (A→B, C→D), if the
relative X order of source/target pairs is inverted, a crossing is likely.
"""

from agent.scoring.metrics import placement_metric


@placement_metric
class EstimatedCrossingsMetric:
    name = "estimated_crossings"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        if len(netlist) < 2:
            return 0.0

        def _pin_pos(pin_key: str) -> tuple[float, float] | None:
            ref = pin_key.split(":")[0]
            c = next((c for c in components if c["ref_des"] == ref), None)
            pin = pin_matrix.get(pin_key)
            if c and pin:
                return (c["x"] + pin["x"], c["y"] + pin["y"])
            return None

        estimates = 0
        conns = list(netlist)
        for i in range(len(conns)):
            s1 = _pin_pos(conns[i]["source"])
            t1 = _pin_pos(conns[i]["target"])
            if not s1 or not t1:
                continue
            for j in range(i + 1, len(conns)):
                s2 = _pin_pos(conns[j]["source"])
                t2 = _pin_pos(conns[j]["target"])
                if not s2 or not t2:
                    continue
                # Check if source-target lines cross (X-order inversion)
                x_order_1 = s1[0] < t1[0]
                x_order_2 = s2[0] < t2[0]
                if x_order_1 != x_order_2:
                    estimates += 1

        return float(estimates)
