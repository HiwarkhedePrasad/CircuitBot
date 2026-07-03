"""Pin direction metric — penalize wires that exit the wrong side of a symbol.

Estimates pin-side violations without running actual routing, using the
relative position of connected components.
"""

from agent.scoring.metrics import placement_metric


@placement_metric
class PinDirectionMetric:
    name = "pin_direction"

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        if not netlist:
            return 0.0

        violations = 0
        total = 0

        def _comp_pos(ref: str) -> tuple[float, float] | None:
            c = next((c for c in components if c["ref_des"] == ref), None)
            if c:
                return (c["x"], c["y"])
            return None

        for conn in netlist:
            sr = conn["source"].split(":")[0]
            tr = conn["target"].split(":")[0]
            src_pos = _comp_pos(sr)
            tgt_pos = _comp_pos(tr)
            if not src_pos or not tgt_pos:
                continue

            # For each pin key, check if the connected component is on
            # the *exit* side of the pin
            for side_key, side_ref, side_pos in [
                ("source", sr, src_pos),
                ("target", tr, tgt_pos),
            ]:
                pin_key = conn.get(side_key, "")
                pin_info = pin_matrix.get(pin_key)
                if not pin_info:
                    continue
                angle = float(pin_info.get("angle", 0)) % 360
                dx = side_pos[0] - tgt_pos[0]
                dy = side_pos[1] - tgt_pos[1]
                exit_ang = (angle + 180) % 360
                expected_dir: str | None = None
                if 45 <= exit_ang < 135:
                    expected_dir = "up"
                elif 135 <= exit_ang < 225:
                    expected_dir = "left"
                elif 225 <= exit_ang < 315:
                    expected_dir = "down"
                else:
                    expected_dir = "right"

                actual_dir = (
                    "right" if dx > 0 else "left" if dx < 0 else
                    "up" if dy > 0 else "down"
                )
                if actual_dir != expected_dir:
                    violations += 1
                total += 1

        return float(violations) / max(total, 1)
