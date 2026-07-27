"""
tscircuit React/TSX Component & CircuitJSON Exporter.

Converts CircuitBot circuit state (components, nets, coordinates) into valid
tscircuit JSX code (<resistor />, <chip />) and CircuitJSON formats.
"""

import json
from typing import Dict, List, Any
from tscircuit_data.schema import Component, Net, Pin, Symbol, Footprint


def export_to_tscircuit_jsx(components: List[Dict[str, Any]], nets: Dict[str, List[Any]]) -> str:
    """
    Convert components and netlists into clean React/TSX tscircuit code using tscircuit_data.schema.
    """
    jsx_lines = [
        "import React from 'react';",
        "import { Circuit, Resistor, Capacitor, Chip, Net } from '@tscircuit/builder';",
        "",
        "export const GeneratedCircuit = () => (",
        "  <Circuit name='CircuitBot_Design'>",
    ]

    # Render components via tscircuit_data.schema Component
    for comp_dict in components:
        comp_obj = Component.from_dict(comp_dict)
        ref = comp_obj.id_str or comp_dict.get("ref") or comp_dict.get("ref_des") or "U1"
        val = comp_obj.name or comp_dict.get("value", "1k")
        fp = comp_obj.footprint.name if comp_obj.footprint else comp_dict.get("footprint", "0603")
        comp_type = comp_obj.category or _infer_tscircuit_element(ref, val)

        if comp_type in ("resistor", "r"):
            jsx_lines.append(f"    <resistor name='{ref}' value='{val}' footprint='{fp}' />")
        elif comp_type in ("capacitor", "c"):
            jsx_lines.append(f"    <capacitor name='{ref}' capacitance='{val}' footprint='{fp}' />")
        elif comp_type in ("ic", "chip"):
            pins_str = ", ".join([f"'{p.name or p.number}'" for p in comp_obj.pins]) or "'VCC', 'GND', 'IN', 'OUT'"
            jsx_lines.append(f"    <chip name='{ref}' name='{val}' pinLabels={{{{{pins_str}}}}} />")
        else:
            jsx_lines.append(f"    <component name='{ref}' value='{val}' footprint='{fp}' />")

    # Render nets
    for net_name, pin_list in nets.items():
        if len(pin_list) > 1:
            connections = []
            for p in pin_list:
                if isinstance(p, dict):
                    connections.append(f"'{p.get('ref')}.{p.get('pin')}'")
                elif isinstance(p, str):
                    connections.append(f"'{p}'")
            conn_str = ", ".join(connections)
            jsx_lines.append(f"    <net name='{net_name}' connections={{[{conn_str}]}} />")

    jsx_lines.append("  </Circuit>")
    jsx_lines.append(");")
    jsx_lines.append("export default GeneratedCircuit;")

    return "\n".join(jsx_lines)


def export_to_circuit_json(components: List[Dict[str, Any]], nets: Dict[str, List[Any]]) -> str:
    """
    Convert components and nets into standard CircuitJSON specification.
    """
    circuit_items = []

    for comp in components:
        ref = comp.get("ref") or comp.get("ref_des") or "U1"
        circuit_items.append({
            "type": "source_component",
            "source_component_id": f"source_comp_{ref}",
            "name": ref,
            "supplier_part_numbers": {
                "lcsc": comp.get("lcsc_pn", "")
            }
        })
        circuit_items.append({
            "type": "schematic_component",
            "schematic_component_id": f"schematic_comp_{ref}",
            "source_component_id": f"source_comp_{ref}",
            "center": {"x": comp.get("x", 0), "y": comp.get("y", 0)},
            "rotation": comp.get("rotation", 0)
        })

    for net_name, pin_list in nets.items():
        circuit_items.append({
            "type": "source_net",
            "source_net_id": f"net_{net_name}",
            "name": net_name
        })

    return json.dumps(circuit_items, indent=2)


def _infer_tscircuit_element(ref: str, val: str) -> str:
    ref_upper = ref.upper()
    if ref_upper.startswith("R"):
        return "resistor"
    if ref_upper.startswith("C"):
        return "capacitor"
    if ref_upper.startswith("U") or ref_upper.startswith("IC"):
        return "chip"
    if ref_upper.startswith("D") or ref_upper.startswith("LED"):
        return "diode"
    return "component"
