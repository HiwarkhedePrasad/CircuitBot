"""Semantic analysis engine.

The analyzer walks the SynthesisGraph and produces a SemanticModel —
reusable metadata about every component's role, power domain, signal
direction, and importance.  Downstream stages consume this model
instead of re-analyzing the raw graph.

Pipeline insertion point:  SynthesisGraph → Semantic Analyzer → Motif Discovery
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import ComponentSemanticInfo, SemanticModel


# ── Controller detection ────────────────────────────────────────────────────


def _find_controller(graph: Any) -> Optional[str]:
    """Identify the main controller IC.

    Strategy:
      1. Look for components with class "microcontroller".
      2. If multiple, pick the one with the most pins (main MCU).
      3. If none, fall back to the largest IC (by pin count) from any digital class.
    """
    candidates: list[tuple[str, int]] = []

    for ref, comp in graph.components.items():
        cls = comp.metadata.get("component_class", "")
        if cls == "microcontroller":
            candidates.append((ref, len(comp.pins)))
        elif cls in ("interface_ic", "amplifier", "comparator"):
            candidates.append((ref, len(comp.pins) * 0.5))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


# ── Regulator and power domain detection ────────────────────────────────────


def _find_regulators(graph: Any) -> list[Any]:
    """Find all voltage regulator components."""
    regulators = []
    for comp in graph.components.values():
        cls = comp.metadata.get("component_class", "")
        if cls in ("linear_regulator", "switching_regulator"):
            regulators.append(comp)
    return regulators


def _net_for_pin(graph: Any, pin_key: str) -> Optional[str]:
    """Return the net name connected to a pin, or None."""
    for net_name, net in graph.nets.items():
        if pin_key in net.pins:
            return net_name
    return None


def _pins_on_net(graph: Any, net_name: str) -> set[str]:
    """Return all pin keys on a given net."""
    net = graph.nets.get(net_name)
    if net is None:
        return set()
    return net.pins


def _trace_power_domain(graph: Any, regulator: Any) -> tuple[str, list[str]]:
    """Trace a regulator's VOUT / VOUT-like net to find powered components.

    Returns (domain_name, list_of_powered_ref_des).
    """
    out_pin_keys = []
    for pk, pin in regulator.pins.items():
        role = getattr(pin, "role", None)
        role_str = str(role.value) if role else ""
        name = getattr(pin, "name", "").upper()
        if role_str == "vout" or name in ("VOUT", "OUT", "VO", "SW"):
            out_pin_keys.append(pk)
        elif role_str == "output":
            out_pin_keys.append(pk)

    if not out_pin_keys:
        return ("", [])

    for out_pk in out_pin_keys:
        net_name = _net_for_pin(graph, out_pk)
        if net_name:
            powered = set()
            for opk in _pins_on_net(graph, net_name):
                if opk == out_pk:
                    continue
                ref = opk.split(":")[0] if ":" in opk else ""
                if ref:
                    powered.add(ref)
            if powered:
                return (net_name, sorted(powered))

    return ("", [])


def _detect_power_domains(
    graph: Any, regulators: list[Any],
) -> dict[str, list[str]]:
    """Build power domain map: domain_name → list of powered ref_des."""
    domains: dict[str, list[str]] = {}

    for reg in regulators:
        domain, powered = _trace_power_domain(graph, reg)
        if domain and powered:
            domains[domain] = powered

    return domains


# ── Connector classification ────────────────────────────────────────────────


def _classify_connector(graph: Any, comp: Any) -> str:
    """Infer a connector's intent from its pin names and connected nets.

    Returns one of:
      "power_usb"     — USB connector with VBUS/GND only (or primary role is power)
      "data_usb"      — USB connector with D+/D- lines
      "programming"   — header with SPI/UART + RESET
      "io_interface"  — general I/O (HDMI, RJ45, audio)
      "power_input"   — DC barrel jack / terminal block for power
      "unknown"       — can't determine intent
    """
    pin_names = []
    for pin in comp.pins.values():
        name = getattr(pin, "name", "").upper()
        if name:
            pin_names.append(name)

    # Check for programming header pattern
    has_reset = any("RESET" in n or "RST" in n for n in pin_names)
    has_spi = any("MOSI" in n or "MISO" in n or "SCK" in n for n in pin_names)
    has_uart = any("TX" in n or "RX" in n or "TXD" in n or "RXD" in n for n in pin_names)

    if has_reset and (has_spi or has_uart):
        return "programming"

    # Check for USB
    has_dp = any(n in ("D+", "DP", "D_P", "USB_DP", "D1+") for n in pin_names)
    has_dn = any(n in ("D-", "DN", "D_N", "USB_DN", "D1-") for n in pin_names)
    has_vbus = any("VBUS" in n or "VCC" == n or "5V" == n for n in pin_names)

    if has_dp or has_dn:
        return "data_usb"
    if has_vbus and not has_dp:
        return "power_usb"

    # Check for DC power jack
    has_vin = any(n in ("VIN", "V+", "DC_IN", "DCIN") for n in pin_names)
    has_gnd = any(n in ("GND", "V-") for n in pin_names)
    if has_vin and has_gnd:
        return "power_input"

    # Check for general connector
    if comp.metadata.get("component_class", "") == "connector":
        return "io_interface"

    return "unknown"


# ── Importance scoring ──────────────────────────────────────────────────────


def _score_importance(graph: Any, ref_des: str, controller: Optional[str]) -> float:
    """Assign 0.0–1.0 importance to a component.

    Factors:
      - Controller IC gets 1.0
      - Regulators get 0.9
      - Components directly connected to controller get 0.7
      - Everything else: pin_count / max_pin_count in circuit
    """
    if ref_des == controller:
        return 1.0

    comp = graph.components.get(ref_des)
    if not comp:
        return 0.1

    cls = comp.metadata.get("component_class", "")
    if cls in ("linear_regulator", "switching_regulator"):
        return 0.9

    # Check connection to controller
    if controller:
        controller_pins = set(graph.components[controller].pins.keys())
        for pk in comp.pins:
            net = _net_for_pin(graph, pk)
            if net:
                shared = _pins_on_net(graph, net) & controller_pins
                if shared:
                    return 0.7

    # Default: normalize by pin count
    max_pins = max((len(c.pins) for c in graph.components.values()), default=1)
    return min(len(comp.pins) / max_pins, 0.5)


# ── Signal direction inference ──────────────────────────────────────────────


def _infer_signal_direction(
    graph: Any, ref_des: str, controller: Optional[str],
) -> str:
    """Infer whether a component's signals flow left→right or top→bottom.

    Heuristics:
      - Controller: "processing" (central hub)
      - Sensors: "input" (toward controller)
      - Actuators/outputs: "output" (away from controller)
      - Power: "top_bottom"
      - Interfaces: "bidirectional"
    """
    comp = graph.components.get(ref_des)
    if not comp:
        return "left_right"

    cls = comp.metadata.get("component_class", "")
    passive = comp.metadata.get("passive_class", "")

    if ref_des == controller:
        return "processing"
    if cls in ("linear_regulator", "switching_regulator"):
        return "top_bottom"
    if passive in ("capacitor", "inductor"):
        return "top_bottom"
    if cls in ("sensor",):
        return "input"
    if cls in ("amplifier", "comparator"):
        return "processing"
    if cls == "connector":
        intent = _classify_connector(graph, comp)
        if intent in ("power_input", "power_usb"):
            return "top_bottom"
        return "bidirectional"
    if cls == "interface_ic":
        return "bidirectional"
    if cls == "transistor":
        return "top_bottom"

    return "left_right"


# ── Placement priority ─────────────────────────────────────────────────────


def _placement_priority(graph: Any, ref_des: str, controller: Optional[str]) -> int:
    """Assign placement priority (0=first, 10=last).

    Ordering:
      0: Controller
      1: Power entry and regulators
      3: Interface connectors
      5: Passive networks
      7: Sensors and actuators
      9: Everything else
    """
    if ref_des == controller:
        return 0

    comp = graph.components.get(ref_des)
    if not comp:
        return 9

    cls = comp.metadata.get("component_class", "")
    if cls in ("linear_regulator", "switching_regulator"):
        return 1
    if cls == "connector":
        return 3
    if cls in ("interface_ic",):
        return 4
    if cls in ("microcontroller",):
        return 0
    if cls in ("sensor",):
        return 7
    passive = comp.metadata.get("passive_class", "")
    if passive in ("resistor", "capacitor", "inductor"):
        return 5
    if passive == "led":
        return 6
    if cls == "transistor":
        return 6

    return 9


# ── Subsystem assignment ────────────────────────────────────────────────────


def _assign_subsystem(
    graph: Any, ref_des: str, controller: Optional[str], power_domains: dict,
) -> str:
    """Assign a subsystem label based on role and connectivity."""
    if ref_des == controller:
        return "main_controller"

    comp = graph.components.get(ref_des)
    if not comp:
        return "unknown"

    cls = comp.metadata.get("component_class", "")
    if cls in ("linear_regulator", "switching_regulator"):
        return "power_supply"

    # Check if this component is on a power domain
    for domain, members in power_domains.items():
        if ref_des in members:
            return f"domain_{domain}"

    # Follow connectivity to controller
    if controller:
        for pk in comp.pins:
            net = _net_for_pin(graph, pk)
            if net:
                ctrl_pins = _pins_on_net(graph, net) & set(
                    graph.components[controller].pins.keys()
                )
                if ctrl_pins:
                    return "main_controller"

    if cls == "connector":
        intent = _classify_connector(graph, comp)
        if intent == "programming":
            return "programming_interface"
        if "usb" in intent:
            return "usb_interface"
        return "io_interface"

    if cls == "sensor":
        return "sensor_frontend"

    return "passive_network"


# ── Main entry point ────────────────────────────────────────────────────────


def analyze_circuit(graph: Any) -> SemanticModel:
    """Run full semantic analysis on a SynthesisGraph.

    1. Find controller IC
    2. Find regulators and trace power domains
    3. Classify connectors by intent
    4. Assign ComponentSemanticInfo to every component
    5. Build and return SemanticModel

    Args:
        graph: A SynthesisGraph instance (with classification already run).

    Returns:
        SemanticModel populated with analysis results.
    """
    model = SemanticModel()

    # 1. Identify controller
    controller = _find_controller(graph)
    model.controller = controller

    # 2. Trace power domains
    regulators = _find_regulators(graph)
    model.power_domains = _detect_power_domains(graph, regulators)
    if controller:
        model.power_domains.setdefault("digital", [])
        model.power_domains["digital"].append(controller)

    # 3. Assign semantic info to every component
    for ref_des, comp in graph.components.items():
        cls = comp.metadata.get("component_class", "")
        passive = comp.metadata.get("passive_class", "")

        role = cls if cls else (passive if passive else "unknown")

        info = ComponentSemanticInfo(
            ref_des=ref_des,
            role=role,
            domain=_find_domain(graph, ref_des, model.power_domains),
            owner=controller or "",
            importance=_score_importance(graph, ref_des, controller),
            signal_direction=_infer_signal_direction(graph, ref_des, controller),
            placement_priority=_placement_priority(graph, ref_des, controller),
            intent=_intent_for(graph, ref_des, controller),
            subsystem=_assign_subsystem(graph, ref_des, controller, model.power_domains),
        )
        model.components[ref_des] = info

    # 4. Infer signal flow
    for ref_des in graph.components:
        model.signal_flow[ref_des] = _infer_signal_direction(
            graph, ref_des, controller,
        )

    return model


# ── Helper: find power domain for a component ───────────────────────────────


def _find_domain(graph: Any, ref_des: str, power_domains: dict) -> str:
    """Find which power domain a component belongs to."""
    for domain, members in power_domains.items():
        if ref_des in members:
            return domain

    comp = graph.components.get(ref_des)
    if not comp:
        return ""

    # Check if any of its pins are on a power net
    for pk in comp.pins:
        net_name = _net_for_pin(graph, pk)
        if net_name:
            net = graph.nets.get(net_name)
            if net and hasattr(net, "role") and str(net.role.value) == "power":
                return net_name

    return ""


# ── Helper: infer intent ────────────────────────────────────────────────────


def _intent_for(graph: Any, ref_des: str, controller: Optional[str]) -> str:
    """Infer a component's specific intent."""
    comp = graph.components.get(ref_des)
    if not comp:
        return ""

    cls = comp.metadata.get("component_class", "")
    passive = comp.metadata.get("passive_class", "")

    if ref_des == controller:
        return "main_processing"
    if cls in ("linear_regulator",):
        return "voltage_regulation"
    if cls in ("switching_regulator",):
        return "dc_dc_conversion"
    if cls == "connector":
        return _classify_connector(graph, comp)
    if cls == "crystal":
        return "clock_source"
    if passive == "led":
        return "status_indicator"
    if cls == "sensor":
        return "environmental_sensing"
    if passive == "crystal":
        return "clock_source"
    if passive == "resistor":
        return "signal_conditioning"

    return ""
