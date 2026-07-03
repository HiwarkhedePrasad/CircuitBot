"""Disconnected-pins metric — count pins with no wire and no connected net.

High weight (100000) ensures the optimizer prioritises fixing
disconnected pins over all other quality metrics.
"""

from __future__ import annotations

from agent.scoring.metrics import routing_metric


@routing_metric
class DisconnectedPinsMetric:
    name = "disconnected_pins"

    def evaluate(self, components, placements, wires, netlist) -> float:
        if not placements or not netlist:
            return 0.0

        # Collect all pins that have a physical wire
        wired_pins: set[str] = set()
        for w in wires:
            src = w.get("source", "")
            tgt = w.get("target", "")
            if src:
                wired_pins.add(src)
            if tgt:
                wired_pins.add(tgt)

        # Collect all pins mentioned in the netlist
        netlist_pins: set[str] = set()
        for conn in netlist:
            s = conn.get("source", "")
            t = conn.get("target", "")
            if s:
                netlist_pins.add(s)
            if t:
                netlist_pins.add(t)

        # Also consider placement components' pins
        placed_refs: set[str] = {p.get("ref_des", "") for p in placements}
        component_pins: set[str] = set()
        for conn in netlist:
            s = conn.get("source", "")
            t = conn.get("target", "")
            for key in (s, t):
                if key:
                    ref = key.split(":")[0] if ":" in key else key
                    if ref in placed_refs:
                        component_pins.add(key)

        wired_or_netlisted = wired_pins | netlist_pins
        disconnected = component_pins - wired_or_netlisted
        return float(len(disconnected))
