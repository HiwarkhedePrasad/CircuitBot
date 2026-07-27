"""Design Twin — live engineering state for every design object.

Every component, net, and trace has a computed operating state that captures
not just position and connection data, but electrical characteristics.

Computed fresh from projections on every query (no stale cache).
Thread-safe: all reads are lock-free (consistent snapshots from projections).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentTwin:
    """Live engineering state for a component."""
    component_id: str = ""
    ref_des: str = ""

    # Static attributes (from library)
    manufacturer: str = ""
    mpn: str = ""
    datasheet_url: str = ""
    footprint: str = ""

    # Operating state (computed from design context)
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    junction_temp: float = 0.0
    efficiency: float = 0.0

    # Lifecycle state
    lifecycle_status: str = "unknown"  # "active", "NRND", "obsolete", "unknown"
    lead_time_days: int = 0
    unit_cost_cents: float = 0.0

    # Compliance
    rohs_compliant: bool = True
    reach_compliant: bool = True

    # Reliability
    derating_margin: float = 1.0  # actual_stress / rated_stress
    mtbf_hours: float = 0.0

    # Status
    locked: bool = False

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "ref_des": self.ref_des,
            "manufacturer": self.manufacturer,
            "mpn": self.mpn,
            "footprint": self.footprint,
            "voltage": round(self.voltage, 3),
            "current": round(self.current, 4),
            "power": round(self.power, 4),
            "junction_temp": round(self.junction_temp, 1),
            "efficiency": round(self.efficiency, 3),
            "lifecycle_status": self.lifecycle_status,
            "derating_margin": round(self.derating_margin, 3),
            "locked": self.locked,
        }


@dataclass
class NetTwin:
    """Live engineering state for a net."""
    net_id: str = ""
    name: str = ""
    classification: str = ""  # POWER, GROUND, SIGNAL, CLOCK, etc.

    # Electrical characteristics
    voltage: float = 0.0
    estimated_current: float = 0.0
    trace_width_required: float = 0.0
    actual_trace_width: float = 0.0

    # Signal integrity
    impedance_target: float = 0.0
    impedance_actual: float = 0.0
    is_differential: bool = False
    diff_pair_skew_ps: float = 0.0

    # Timing
    propagation_delay_ns: float = 0.0

    def to_dict(self) -> dict:
        return {
            "net_id": self.net_id,
            "name": self.name,
            "classification": self.classification,
            "voltage": round(self.voltage, 3),
            "estimated_current": round(self.estimated_current, 4),
            "trace_width_required": round(self.trace_width_required, 3),
            "actual_trace_width": round(self.actual_trace_width, 3),
            "impedance_target": round(self.impedance_target, 1),
            "impedance_actual": round(self.impedance_actual, 1),
            "is_differential": self.is_differential,
        }


@dataclass
class TraceTwin:
    """Live engineering state for a trace."""
    trace_id: str = ""
    net: str = ""
    layer: str = ""
    width: float = 0.0
    length: float = 0.0

    # Electrical
    impedance: float = 0.0
    current_capacity: float = 0.0
    dc_resistance: float = 0.0
    voltage_drop: float = 0.0

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "net": self.net,
            "layer": self.layer,
            "width": round(self.width, 3),
            "length": round(self.length, 2),
            "impedance": round(self.impedance, 1),
            "current_capacity": round(self.current_capacity, 3),
            "dc_resistance": round(self.dc_resistance, 4),
            "voltage_drop": round(self.voltage_drop, 4),
        }


class DesignTwin:
    """Computes live engineering state for all design objects.

    No caching — computed fresh from projections every time.
    Thread-safe: reads from projections dict (immutable snapshot).
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}
        self._lock = threading.Lock()

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def component_twin(self, ref_des: str) -> ComponentTwin | None:
        """Compute live state for a component."""
        design = self._projections.get("design", {})
        comps = design.get("selected_components", [])
        comp = next((c for c in comps if c.get("ref_des") == ref_des), None)
        if not comp:
            return None

        twin = ComponentTwin(
            component_id=comp.get("id_str", ""),
            ref_des=ref_des,
            footprint=comp.get("footprint", ""),
        )

        # Compute voltage from connected nets
        pin_matrix = design.get("pin_matrix", {})
        nets = design.get("nets", [])
        power_pins = design.get("power_pins", [])

        for pp in power_pins:
            if pp.get("pin", "").startswith(ref_des + ":"):
                net_name = pp.get("net", "")
                twin.voltage = self._estimate_net_voltage(net_name)
                break

        # Estimate power from device knowledge
        from agent.component_knowledge import lookup_device
        device = lookup_device(comp.get("id_str", ""), comp.get("description", ""))
        if device:
            if "voltage" in device:
                try:
                    twin.voltage = float(device["voltage"])
                except (ValueError, TypeError):
                    pass
            if "vin" in device and "vout" in device:
                try:
                    vin = float(device["vin"])
                    vout = float(device["vout"])
                    if vin > 0:
                        twin.efficiency = vout / vin
                except (ValueError, TypeError):
                    pass

        # Estimate current from load (rough: assume 10mA per digital pin)
        comp_pins = [k for k in pin_matrix if k.startswith(ref_des + ":")]
        digital_pins = sum(1 for k in comp_pins
                          if pin_matrix.get(k, {}).get("etype", "") in ("output", "bidirectional"))
        twin.current = digital_pins * 0.01  # 10mA per output pin (rough)

        twin.power = twin.voltage * twin.current

        # Derating margin (assume 80% rated for now)
        twin.derating_margin = 0.8

        return twin

    def net_twin(self, net_name: str) -> NetTwin | None:
        """Compute live state for a net."""
        design = self._projections.get("design", {})
        nets = design.get("nets", [])
        net = next((n for n in nets if n.get("net") == net_name), None)
        if not net:
            return None

        from agent.synthesis.graph import NetRole
        role = NetRole.from_net_name(net_name)

        twin = NetTwin(
            net_id=net_name,
            name=net_name,
            classification=role.value,
            voltage=self._estimate_net_voltage(net_name),
        )

        # Count connected pins
        pins = net.get("pins", [])
        twin.estimated_current = len(pins) * 0.005  # rough estimate

        # Check for differential pair
        twin.is_differential = any(
            other in net_name.upper()
            for other in ["_P", "_N", "_DP", "_DN", "_PLUS", "_MINUS"]
        )

        return twin

    def trace_twin(self, net_name: str, layer: str = "F.Cu") -> TraceTwin | None:
        """Compute live state for a trace."""
        design = self._projections.get("design", {})
        board_model = design.get("board_model", {})
        traces = board_model.get("traces", [])

        trace = next((t for t in traces if t.get("net") == net_name), None)
        if not trace:
            return None

        width = trace.get("width", 0.254)
        path = trace.get("path", [])

        # Calculate length
        length = 0.0
        for i in range(1, len(path)):
            dx = path[i][0] - path[i-1][0]
            dy = path[i][1] - path[i-1][1]
            length += (dx*dx + dy*dy) ** 0.5

        twin = TraceTwin(
            net=net_name,
            layer=trace.get("layer", layer),
            width=width,
            length=length,
        )

        # Calculate impedance
        try:
            from agent.tools import calculate_microstrip_impedance
            twin.impedance = calculate_microstrip_impedance(width, 0.035, 1.5, 4.5)
        except Exception:
            pass

        # Calculate current capacity
        try:
            from agent.tools import calculate_max_current
            twin.current_capacity = calculate_max_current(width, 0.035, 25.0)
        except Exception:
            pass

        return twin

    def all_component_twins(self) -> list[ComponentTwin]:
        """Get twins for all components."""
        design = self._projections.get("design", {})
        comps = design.get("selected_components", [])
        twins = []
        for c in comps:
            twin = self.component_twin(c["ref_des"])
            if twin:
                twins.append(twin)
        return twins

    def all_net_twins(self) -> list[NetTwin]:
        """Get twins for all nets."""
        design = self._projections.get("design", {})
        nets = design.get("nets", [])
        twins = []
        for n in nets:
            twin = self.net_twin(n.get("net", ""))
            if twin:
                twins.append(twin)
        return twins

    def overstressed_components(self, power_threshold: float = 0.5) -> list[ComponentTwin]:
        """Find components with power dissipation above threshold."""
        return [t for t in self.all_component_twins() if t.power > power_threshold]

    def _estimate_net_voltage(self, net_name: str) -> float:
        """Estimate voltage for a net from power labels or device knowledge."""
        design = self._projections.get("design", {})
        power_labels = design.get("power_labels", [])
        for label in power_labels:
            if label.get("net", "").upper() == net_name.upper():
                try:
                    return float(label.get("voltage", 0))
                except (ValueError, TypeError):
                    pass

        # Heuristic from net name
        name_upper = net_name.upper()
        if "5V" in name_upper or "VBUS" in name_upper:
            return 5.0
        if "3V3" in name_upper or "3.3" in name_upper:
            return 3.3
        if "1V8" in name_upper or "1.8" in name_upper:
            return 1.8
        if "GND" in name_upper:
            return 0.0
        return 0.0
