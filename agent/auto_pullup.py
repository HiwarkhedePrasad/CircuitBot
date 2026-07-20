"""Auto-detect missing pull-up resistors on open-drain buses.

Scans signal nets after valid_nets construction and flags buses
(I2C, 1-Wire, etc.) that lack a pull-up resistor to a power rail.
"""

from __future__ import annotations

import re

_OPEN_DRAIN_PATTERNS = re.compile(
    r'\b(SDA|SCL|I2C_SDA|I2C_SCL|ONEWIRE|OW_DQ|DQ|1WIRE)\b',
    re.IGNORECASE,
)


def _ref_of(pin_key: str) -> str:
    return pin_key.split(":")[0] if ":" in pin_key else pin_key


def find_missing_pullups(
    valid_nets: list[dict],
    pin_matrix: dict,
    comps: list,
) -> list[dict]:
    """Check each open-drain signal net for a pull-up resistor to a power rail.

    Returns a list of issue dicts, one per missing pull-up:
        {"code": "PUL001", "net": net_name, "message": "..."}
    """
    issues: list[dict] = []

    # Build a set of resistor refs from the component list
    resistor_refs: set[str] = set()
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        if "RESISTOR" in id_str or "R_SMALL" in id_str or "R_" in id_str:
            resistor_refs.add(c["ref_des"])

    # Build a lookup: ref -> set of net names its pins belong to
    ref_nets: dict[str, set[str]] = {}
    for net in valid_nets:
        name = net.get("net", "")
        for p in net.get("pins", []):
            ref = _ref_of(p)
            ref_nets.setdefault(ref, set()).add(name)

    # Build set of power-rail net names (non-GND)
    from agent.utils import _is_power_net, _is_gnd_net
    power_rails: set[str] = set()
    for net in valid_nets:
        name = net.get("net", "")
        if (_is_power_net(name) or _is_gnd_net(name)) and not _is_gnd_net(name):
            power_rails.add(name)
            power_rails.add(name.upper())
            power_rails.add(name.lower())

    for net in valid_nets:
        name = net.get("net", "")
        if _is_gnd_net(name) or _is_power_net(name):
            continue
        if not _OPEN_DRAIN_PATTERNS.search(name):
            continue

        pin_list = net.get("pins", [])
        if len(pin_list) < 1:
            continue

        # Check if any pin on this net belongs to a resistor
        has_resistor = False
        for p in pin_list:
            ref = _ref_of(p)
            if ref in resistor_refs:
                has_resistor = True
                break

        if has_resistor:
            continue

        # Check if any resistor in the design straddles this net + a power rail
        # (edge case: resistor was already matched upstream)
        found_straddle = False
        for r_ref in resistor_refs:
            r_nets = ref_nets.get(r_ref, set())
            if name in r_nets and r_nets & power_rails:
                found_straddle = True
                break
        if found_straddle:
            continue

        issues.append({
            "code": "PUL001",
            "net": name,
            "severity": "warning",
            "stage": "netlist",
            "message": (
                f"Net '{name}' appears to be an open-drain bus but has no "
                f"pull-up resistor to a power rail (VCC/3V3/VDD). "
                f"A 4.7k\u03A9 pull-up is recommended."
            ),
        })

    return issues
