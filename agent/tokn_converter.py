"""
TOKN (Token-Oriented KiCad Notation) Converter.

Reduces verbose KiCad S-Expression schematics into token-dense representations
for LLM repair and audit nodes, reducing token overhead by up to 75-85%.
"""

import re
from typing import Dict, List, Any


def sexpr_to_tokn(sexpr_str: str) -> str:
    """
    Convert a verbose KiCad S-Expression schematic or netlist into compact TOKN format.
    Strips graphical elements (polylines, strokes, effects, UUIDs, exact rendering offsets)
    while preserving components, pins, nets, values, and reference designators.
    """
    lines = sexpr_str.splitlines()
    tokn_lines = []
    
    current_symbol = None
    properties = {}
    pins = []
    wire_nets = []

    for line in lines:
        stripped = line.strip()
        
        # Detect symbol definition
        if '(symbol' in stripped:
            match = re.search(r'\(symbol\s+"([^"]+)"', stripped)
            if match:
                current_symbol = match.group(1)

        # Detect properties (Reference, Value, Footprint)
        if '(property' in stripped:
            prop_matches = re.findall(r'\(property\s+"([^"]+)"\s+"([^"]*)"', stripped)
            for prop_key, prop_val in prop_matches:
                properties[prop_key] = prop_val

        # Detect pins
        elif stripped.startswith('(pin'):
            type_match = re.search(r'\(pin\s+([^\s\()]+)', stripped)
            num_match = re.search(r'\(number\s+"([^"]+)"', stripped)
            name_match = re.search(r'\(name\s+"([^"]+)"', stripped)
            if num_match and current_symbol:
                p_num = num_match.group(1)
                p_name = name_match.group(1) if name_match else ""
                p_type = type_match.group(1) if type_match else "passive"
                pins.append(f"P({p_num}:{p_name}:{p_type})")

        # Detect net wiring
        elif stripped.startswith('(net') or 'net' in stripped:
            match = re.search(r'\(net\s+(\d+)\s+"([^"]+)"\)', stripped)
            if match:
                wire_nets.append(f"NET({match.group(1)}:{match.group(2)})")

    # Format into TOKN string
    ref = properties.get("Reference", "REF?")
    val = properties.get("Value", "VAL?")
    fp = properties.get("Footprint", "")

    tokn_out = []
    if current_symbol:
        tokn_out.append(f"SYM|{ref}|{val}|{current_symbol}|{fp}")
        if pins:
            tokn_out.append("  PINS: " + ", ".join(pins))
    
    if wire_nets:
        tokn_out.append("  NETS: " + ", ".join(wire_nets))

    if not tokn_out:
        # Fallback for structured dicts/S-expressions
        return _structured_to_tokn(sexpr_str)

    return "\n".join(tokn_out)


def _structured_to_tokn(raw_content: str) -> str:
    """Fallback tokenizer for raw netlist text or basic schematic blocks."""
    clean = re.sub(r'\(uuid\s+"[^"]+"\)', '', raw_content)
    clean = re.sub(r'\(effects\s+.*?\)\)', '', clean, flags=re.DOTALL)
    clean = re.sub(r'\(stroke\s+.*?\)\)', '', clean, flags=re.DOTALL)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def tokn_to_net_dict(tokn_str: str) -> Dict[str, Any]:
    """
    Parse TOKN compact format into a structured python dictionary for deterministic processing.
    """
    components = []
    nets = {}

    lines = tokn_str.splitlines()
    current_comp = None

    for line in lines:
        line = line.strip()
        if line.startswith("SYM|"):
            parts = line.split("|")
            if len(parts) >= 4:
                current_comp = {
                    "ref": parts[1],
                    "value": parts[2],
                    "symbol": parts[3],
                    "footprint": parts[4] if len(parts) > 4 else "",
                    "pins": []
                }
                components.append(current_comp)
        elif line.startswith("PINS:") and current_comp:
            pins_raw = line.replace("PINS:", "").split(",")
            for p in pins_raw:
                p_clean = p.strip()
                if p_clean.startswith("P(") and p_clean.endswith(")"):
                    p_info = p_clean[2:-1].split(":")
                    if len(p_info) >= 2:
                        current_comp["pins"].append({
                            "number": p_info[0],
                            "name": p_info[1],
                            "type": p_info[2] if len(p_info) > 2 else "passive"
                        })
        elif line.startswith("NETS:"):
            nets_raw = line.replace("NETS:", "").split(",")
            for n in nets_raw:
                n_clean = n.strip()
                if "NET(" in n_clean:
                    m = re.search(r'NET\((\d+):([^)]+)\)', n_clean)
                    if m:
                        nets[m.group(1)] = m.group(2)

    return {
        "components": components,
        "nets": nets
    }
