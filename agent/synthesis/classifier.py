"""Pin-role and component-class classification.

Classification priority:
  1. Symbol metadata (KiCad pin electrical types + pin names)
  2. Pin name normalization (structured rules, not regex)
  3. Electrical type fallback

PinRole is assigned ONCE per pin.  Downstream code uses pin.role
directly — never string-matches pin names or electrical types.
"""

from __future__ import annotations

from agent.synthesis.graph import ComponentNode, PinNode, PinRole, SynthesisGraph


# ── Component classification ───────────────────────────────────────────────

# Component classes — derived from library + pin analysis, never from
# hardcoded component names.
_COMPONENT_CLASSES: dict[str, set[str]] = {
    "linear_regulator": {
        "Regulator_Linear",
    },
    "switching_regulator": {
        "Regulator_Switching",
    },
    "microcontroller": {
        "MCU_", "Module_",
    },
    "sensor": {
        "Sensor",
    },
    "interface_ic": {
        "Interface",
    },
    "amplifier": {
        "Amplifier",
    },
    "comparator": {
        "Comparator",
    },
    "connector": {
        "Connector",
    },
    "led": {
        "Device:LED",
    },
    "crystal": {
        "Crystal",
    },
    "transistor": {
        "Transistor", "FET",
    },
}


def classify_component(comp: ComponentNode) -> str | None:
    """Return the component class, or None if unknown."""
    lib = comp.library
    for cls_name, prefixes in _COMPONENT_CLASSES.items():
        for p in prefixes:
            if lib.startswith(p):
                return cls_name
            if f":{p}" in comp.id_str.upper():
                return cls_name
    return None


def classify_passive(comp: ComponentNode) -> str | None:
    """Classify passive components (R, C, L, etc.) by library prefix.

    Order matters: check longer / more specific prefixes first.
    """
    id_upper = comp.id_str.upper()
    if id_upper.startswith("DEVICE:LED"):
        return "led"
    if id_upper.startswith("DEVICE:R"):
        return "resistor"
    if id_upper.startswith("DEVICE:C"):
        return "capacitor"
    if id_upper.startswith("DEVICE:L"):
        return "inductor"
    if id_upper.startswith("DEVICE:D"):
        return "diode"
    return None


# ── Pin classification ─────────────────────────────────────────────────────


def classify_pins(graph: SynthesisGraph):
    """Assign PinRole to every pin in the graph.

    Idempotent — safe to call multiple times.
    """
    for comp in graph.components.values():
        pin_count = len(comp.pins)
        other_names = {p.name.upper() for p in comp.pins.values()}
        for pin_key, pin in comp.pins.items():
            if pin.role != PinRole.UNUSED:
                continue
            pin.role = PinRole.from_pin_name(
                pin.name, pin.etype, pin_count, other_names,
            )


def classify_all(graph: SynthesisGraph):
    """Run full classification on the graph (components + pins)."""
    classify_pins(graph)
    for comp in graph.components.values():
        cls = classify_component(comp)
        if cls:
            comp.metadata["component_class"] = cls
        passive_cls = classify_passive(comp)
        if passive_cls:
            comp.metadata["passive_class"] = passive_cls
