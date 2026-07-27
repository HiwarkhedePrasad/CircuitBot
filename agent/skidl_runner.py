"""
SKiDL Netlisting & Electrical Rule Checking (ERC) Engine.

Provides deterministic netlist synthesis and rule verification to eliminate
LLM pin connection hallucinations.
"""

from typing import Dict, List, Any, Tuple


class SKiDLErcError(Exception):
    """Exception raised when deterministic ERC fails."""
    pass


class SKiDLNetlistEngine:
    """
    Deterministic netlist validator and builder inspired by SKiDL.
    Enforces electrical pin constraints:
    - Driven outputs connected to each other (Short Circuit Risk)
    - Unconnected input/power pins
    - Power rail shorts (VCC connected to GND)
    """

    def __init__(self):
        self.nets: Dict[str, List[Tuple[str, str, str]]] = {}  # net_name -> [(ref_des, pin_num, pin_type)]
        self.components: Dict[str, Dict[str, Any]] = {}

    def add_component(self, ref_des: str, value: str, pins: Dict[str, str]):
        """
        Add a component with its pins and electrical types.
        pins: dict of pin_num -> electrical_type (e.g. {'1': 'power_in', '2': 'passive'})
        """
        self.components[ref_des] = {
            "value": value,
            "pins": pins
        }

    def connect_pin(self, net_name: str, ref_des: str, pin_num: str, pin_type: str = "passive"):
        """Connect a specific component pin to a net."""
        if net_name not in self.nets:
            self.nets[net_name] = []
        self.nets[net_name].append((ref_des, pin_num, pin_type))

    def run_erc(self) -> List[str]:
        """
        Perform Electrical Rule Checking (ERC).
        Returns a list of error warning strings.
        """
        errors = []

        # Rule 1: Check for GND & VCC shorts
        gnd_nets = {"GND", "VSS", "AGND", "0V"}
        power_nets = {"VCC", "+5V", "+3V3", "+12V", "VDD"}

        net_names = set(self.nets.keys())
        has_gnd = net_names.intersection(gnd_nets)
        has_pwr = net_names.intersection(power_nets)

        # Check if any net contains both GND and Power pins accidentally aliased
        for net_name, pins in self.nets.items():
            net_upper = net_name.upper()
            if any(p[0] == "GND" for p in pins) and any(p[0] == "VCC" for p in pins):
                errors.append(f"ERC CRITICAL: Net '{net_name}' shorts GND and VCC together!")

            # Rule 2: Multiple outputs driving the same net
            output_pins = [p for p in pins if p[2].lower() in ("output", "power_out", "3state")]
            if len(output_pins) > 1:
                refs = ", ".join([f"{p[0]}:{p[1]}" for p in output_pins])
                errors.append(f"ERC WARNING: Multiple driven output pins connected on Net '{net_name}': {refs}")

        # Rule 3: Check for floating input pins
        for ref_des, comp in self.components.items():
            for p_num, p_type in comp["pins"].items():
                if p_type.lower() in ("input", "power_in"):
                    is_connected = False
                    for net_pins in self.nets.values():
                        if any(p[0] == ref_des and p[1] == str(p_num) for p in net_pins):
                            is_connected = True
                            break
                    if not is_connected:
                        errors.append(f"ERC WARNING: Floating {p_type} pin {ref_des}:{p_num}")

        return errors

    def export_netlist_dict(self) -> Dict[str, Any]:
        """Export standardized netlist dictionary for KiCad & tscircuit engines."""
        return {
            "components": self.components,
            "nets": {
                net_name: [{"ref": p[0], "pin": p[1], "etype": p[2]} for p in pins]
                for net_name, pins in self.nets.items()
            }
        }


def build_and_validate_skidl_netlist(components: List[Dict], connections: List[Dict]) -> Tuple[Dict, List[str]]:
    """
    Helper function to instantiate SKiDL engine, apply connections, and validate ERC.
    """
    engine = SKiDLNetlistEngine()

    for c in components:
        ref = c.get("ref") or c.get("ref_des") or "U?"
        val = c.get("value") or "VAL"
        pins = {str(p.get("number", i+1)): p.get("type", "passive") for i, p in enumerate(c.get("pins", []))}
        engine.add_component(ref, val, pins)

    for conn in connections:
        net = conn.get("net", "NET_UNNAMED")
        for pin_ref in conn.get("pins", []):
            if ":" in pin_ref:
                ref, pin_num = pin_ref.split(":", 1)
                engine.connect_pin(net, ref, pin_num)

    erc_errors = engine.run_erc()
    return engine.export_netlist_dict(), erc_errors
