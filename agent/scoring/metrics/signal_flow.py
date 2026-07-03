"""Signal flow metric — penalize components far from their ideal signal-flow tier."""

import math
from agent.scoring.metrics import placement_metric

_TIER_RULES: list[tuple[str, int]] = [
    ("CONNECTOR", 0), ("USB", 0), ("BATTERY", 0),
    ("LDO", 1), ("REGULATOR", 1), ("BUCK", 1), ("BOOST", 1), ("CONVERTER", 1),
    ("MCU", 2), ("PROCESSOR", 2), ("ESP32", 2), ("STM32", 2), ("FPGA", 2), ("CPU", 2),
    ("RF_MODULE", 2), ("DSP", 2), ("MEMORY", 2),
    ("SENSOR", 3), ("DISPLAY", 3), ("DRIVER", 3),
]


def _tier_of(category: str, id_str: str = "") -> int:
    id_up = id_str.upper().replace(" ", "_")
    cat_up = category.upper().replace(" ", "_")
    for kw, t in _TIER_RULES:
        if kw in cat_up or kw in id_up:
            return t
    return 2


@placement_metric
class SignalFlowMetric:
    name = "signal_flow"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        if not components:
            return 0.0

        penalty = 0.0
        n = len(components)
        xs = [c["x"] for c in components]
        if not xs:
            return 0.0
        min_x, max_x = min(xs), max(xs)
        span = max(max_x - min_x, 1.0)

        for c in components:
            ideal_tier = _tier_of(c.get("category", ""), c.get("id_str", ""))
            # Normalize position to 0..3 range
            pos_tier = (c["x"] - min_x) / span * 3.0
            deviation = abs(pos_tier - ideal_tier)
            penalty += deviation

        return penalty / n
