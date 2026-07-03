"""Alignment metric — reward components sharing X or Y with neighbors."""

from agent.scoring.metrics import placement_metric

GRID = 1.27
ALIGNMENT_TOLERANCE = GRID * 0.5


@placement_metric
class AlignmentMetric:
    name = "alignment"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        if len(components) < 2:
            return 0.0

        total_pairs = 0
        aligned_pairs = 0
        for i in range(len(components)):
            for j in range(i + 1, len(components)):
                total_pairs += 1
                a, b = components[i], components[j]
                if abs(a["x"] - b["x"]) < ALIGNMENT_TOLERANCE:
                    aligned_pairs += 1
                elif abs(a["y"] - b["y"]) < ALIGNMENT_TOLERANCE:
                    aligned_pairs += 1

        if total_pairs == 0:
            return 0.0
        # Return misalignment ratio (lower is better)
        return 1.0 - (aligned_pairs / total_pairs)
