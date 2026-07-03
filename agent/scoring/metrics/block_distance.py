"""Block distance metric — penalize satellites far from their parent IC."""

from agent.scoring.metrics import placement_metric

MAX_SAT_DISTANCE = 30.0


@placement_metric
class BlockDistanceMetric:
    name = "block_distance"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        # Build parent map from for_component
        parent_of: dict[str, str] = {}
        for c in components:
            fc = c.get("for_component", "")
            if fc:
                parent_of[c["ref_des"]] = fc

        if not parent_of:
            return 0.0

        total_excess = 0.0
        for ref, par_ref in parent_of.items():
            sat = next((c for c in components if c["ref_des"] == ref), None)
            par = next((c for c in components if c["ref_des"] == par_ref), None)
            if not sat or not par:
                continue

            dist = abs(sat["x"] - par["x"]) + abs(sat["y"] - par["y"])
            if dist > MAX_SAT_DISTANCE:
                total_excess += dist - MAX_SAT_DISTANCE

        return total_excess
