"""Deterministic bus topology checker — post-LLM netlist validation.

Plugged into netlist_node after the LLM batch-wiring loop, before the
name-match fallback.  Catches topology errors the LLM is prone to:

  1. Power net isolation     — signal-type pins on GND/power nets
  2. I2C bus merge           — scattered SDA/SCL aliases merged into one
  3. UART cross-check        — two TX pins on the same net
  4. Same-component short    — two signal pins from the same IC on one net
  5. Crystal load caps       — crystal signal pins missing load capacitor to GND
  6. Power flag required     — power rail net has no power_out pin / PWR_FLAG
  7. Auto-named nets         — net name looks auto-generated (NET-* / N_*)

Each rule is purely deterministic (regex + set logic), requires zero LLM
calls, and runs in O(nets × pins).
"""

import re
from typing import Optional

from agent.power_domains import POWER_NETS as _CANONICAL_POWER_NETS, GND_NETS as _CANONICAL_GND_NETS
from agent.utils import (
    _is_gnd_net, _is_power_net, _merge_net,
    _canonical_signal_name, PIN_ALIASES,
)

# Pin electrical types that should NEVER appear on power/GND nets
_SIGNAL_ETYPES = frozenset({
    "input", "output", "bidirectional", "tri_state",
    "open_collector", "open_emitter", "passive",
})

# Pin electrical types that ARE acceptable on power nets
_POWER_SAFE_ETYPES = frozenset({
    "power_in", "power_out", "passive",
})

# Net-name patterns that look like I2C buses
_I2C_CANONICAL = frozenset({"SDA", "SCL"})

# Net names that are definitely power rails (canonical source: power_domains.py)
_HARD_POWER_NETS = frozenset(_CANONICAL_POWER_NETS | _CANONICAL_GND_NETS)


# ── Rule helpers ────────────────────────────────────────────────────────────


def _pin_etype(pin_key: str, pin_matrix: dict) -> str:
    pin = pin_matrix.get(pin_key, {})
    if isinstance(pin, dict):
        return pin.get("etype", "") or ""
    return ""


def _pin_name(pin_key: str, pin_matrix: dict) -> str:
    pin = pin_matrix.get(pin_key, {})
    if isinstance(pin, dict):
        return pin.get("name", "") or ""
    return ""


def _ref_of(pin_key: str) -> str:
    return pin_key.split(":")[0] if ":" in pin_key else pin_key


def _is_passive_component(ref: str, comps: list) -> bool:
    for c in comps:
        if c["ref_des"] == ref:
            id_str = c.get("id_str", "")
            return id_str.startswith("Device:") or c.get("category", "").upper() in ("DEVICE", "RESISTOR", "CAPACITOR")
    return False


# ── Rule 1: Power net isolation ─────────────────────────────────────────


def _check_power_isolation(
    nets: list[dict],
    pin_matrix: dict,
    comps: list,
    warnings: list,
) -> tuple[list[dict], set[str]]:
    """Remove signal-type pins from power/GND nets.

    Returns (corrected_nets, removed_pins) where removed_pins is the set
    of pin keys ejected from power/GND nets.  Callers MUST remove these
    from their ``assigned`` set so the fallback can recover them.
    """
    corrected = []
    removed_pins: set[str] = set()
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if not (_is_gnd_net(name) or _is_power_net(name) or name.upper() in _HARD_POWER_NETS):
            corrected.append(net)
            continue
        clean = []
        for p in pins:
            etype = _pin_etype(p, pin_matrix)
            if not etype or etype in _POWER_SAFE_ETYPES:
                clean.append(p)
                continue
            ref = _ref_of(p)
            if _is_passive_component(ref, comps):
                clean.append(p)
                continue
            warnings.append(
                f"  Bus check — power isolation: removed {p} ({_pin_name(p, pin_matrix)}, etype={etype}) "
                f"from net '{name}' (signal pin on power rail)"
            )
            removed_pins.add(p)
        if clean:
            corrected.append({"net": name, "pins": clean})
        elif len(pins) == 0:
            corrected.append({"net": name, "pins": clean})
    return corrected, removed_pins


# ── Rule 2: I2C bus merge ──────────────────────────────────────────────


def _is_i2c_net(name: str) -> str | None:
    """Return 'SDA' or 'SCL' if the net name is an I2C bus alias, else None.

    Uses PIN_ALIASES for exact matches and a broader heuristics for
    common LLM-generated variants like 'I2C_SDA', 'SDA_1', 'I2C_BUS'.
    """
    upper = name.upper().strip()
    canon = _canonical_signal_name(upper)
    if canon in ("SDA", "SCL"):
        return canon
    # Broader heuristic: match SDA/SCL as tokens (allow _ - / as separators)
    if re.search(r'(?:^|[_\-/\s])SDA(?:\d*|$|[_\-/\s])', upper):
        return "SDA"
    if re.search(r'(?:^|[_\-/\s])SCL(?:\d*|$|[_\-/\s])', upper):
        return "SCL"
    return None


def _check_i2c_merge(
    nets: list[dict],
    pin_matrix: dict,
    warnings: list,
) -> list[dict]:
    """Merge scattered SDA/SCL aliases into canonical I2C bus nets.

    The LLM often creates separate nets like 'I2C_SDA', 'SDA_1', 'SDA'
    when all SDA pins should share a single bus.  This rule merges them
    by their canonical name (SDA or SCL).
    """
    sda_nets: list[dict] = []
    scl_nets: list[dict] = []
    other_nets: list[dict] = []

    for net in nets:
        name = net.get("net", "")
        tag = _is_i2c_net(name)
        if tag == "SDA":
            sda_nets.append(net)
        elif tag == "SCL":
            scl_nets.append(net)
        else:
            other_nets.append(net)

    merged = list(other_nets)

    for canon_name, group in [("SDA", sda_nets), ("SCL", scl_nets)]:
        if len(group) <= 1:
            merged.extend(group)
            continue
        all_pins: list[str] = []
        for g in group:
            all_pins.extend(g.get("pins", []))
        merged.append({"net": canon_name, "pins": all_pins})
        count = len(group)
        merged_names = [g["net"] for g in group]
        warnings.append(
            f"  Bus check — I2C merge: merged {count} nets ({', '.join(merged_names)}) "
            f"into canonical '{canon_name}' ({len(all_pins)} pins)"
        )

    return merged


# ── Rule 3: UART cross-check ───────────────────────────────────────────


def _check_uart_cross(
    nets: list[dict],
    pin_matrix: dict,
    warnings: list,
) -> list[dict]:
    """Flag nets where two TX-type pins from different components are wired.

    Two transmitters on the same line is an electrical conflict.
    """
    _TX_PATTERN = re.compile(r'\b(TX|TXD|TX_OUT|TX_1|TX0)\b', re.IGNORECASE)
    _RX_PATTERN = re.compile(r'\b(RX|RXD|RX_IN|RX_1|RX0)\b', re.IGNORECASE)

    corrected = []
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if _is_gnd_net(name) or _is_power_net(name) or len(pins) < 2:
            corrected.append(net)
            continue

        # Find all TX and RX pin keys
        tx_refs: set[str] = set()
        rx_refs: set[str] = set()
        for p in pins:
            pname = _pin_name(p, pin_matrix)
            etype = _pin_etype(p, pin_matrix)
            if etype == "bidirectional":
                continue
            if _TX_PATTERN.search(pname):
                tx_refs.add(_ref_of(p))
            if _RX_PATTERN.search(pname):
                rx_refs.add(_ref_of(p))

        # Two different components each have a TX on the same net → conflict
        if len(tx_refs) >= 2:
            warnings.append(
                f"  Bus check — UART conflict: net '{name}' has TX pins "
                f"from {', '.join(sorted(tx_refs))}"
            )

        corrected.append(net)
    return corrected


# ── Rule 4: Same-component signal short ────────────────────────────────


def _check_same_component_short(
    nets: list[dict],
    pin_matrix: dict,
    comps: list,
    warnings: list,
) -> list[dict]:
    """Flag signal nets with two or more pins from the same non-passive IC.

    Two signal pins of the same IC should rarely share a single net
    (exceptions: GND/VCC pins for that IC which are handled upstream).
    """
    corrected = []
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if _is_gnd_net(name) or _is_power_net(name) or len(pins) < 2:
            corrected.append(net)
            continue

        by_ref: dict[str, list[str]] = {}
        for p in pins:
            by_ref.setdefault(_ref_of(p), []).append(p)

        for ref, ref_pins in by_ref.items():
            if len(ref_pins) < 2:
                continue
            if _is_passive_component(ref, comps):
                continue
            etypes = {_pin_etype(p, pin_matrix) for p in ref_pins}
            if etypes <= {"passive", "power_in", "power_out", ""}:
                continue
            pnames = [_pin_name(p, pin_matrix) for p in ref_pins]
            warnings.append(
                f"  Bus check — same-component short: {ref} has {len(ref_pins)} pins "
                f"({', '.join(pnames)}) on net '{name}'"
            )
        corrected.append(net)
    return corrected


# ── Rule 5: Crystal load caps ────────────────────────────────────────────


def _is_crystal_component(comps: list, ref: str) -> bool:
    for c in comps:
        if c["ref_des"] == ref:
            id_str = (c.get("id_str", "") or "").upper()
            return id_str.startswith("DEVICE:CRYSTAL") or "CRYSTAL" in id_str
    return False


def _check_crystal_load_caps(
    nets: list[dict],
    pin_matrix: dict,
    comps: list,
    warnings: list,
) -> list[dict]:
    """Warn if any crystal signal pin's net lacks a load capacitor to GND.

    Each non-GND crystal pin should share its net with exactly one
    capacitor whose other pin goes to GND.
    """
    comp_by_ref = {c["ref_des"]: c for c in comps}
    crystal_refs = {
        c["ref_des"] for c in comps
        if _is_crystal_component(comps, c["ref_des"])
    }
    if not crystal_refs:
        return nets

    for ref in crystal_refs:
        for net in nets:
            name = net.get("net", "")
            pins = net.get("pins", [])

            crystal_pins = [p for p in pins if _ref_of(p) == ref]
            if not crystal_pins:
                continue
            if _is_gnd_net(name):
                continue

            cap_found = any(
                _ref_of(p) != ref
                and (
                    (comp_by_ref.get(_ref_of(p)) or {}).get("id_str", "") or ""
                ).upper().startswith("DEVICE:C_")
                for p in pins
            )

            if not cap_found:
                for p in crystal_pins:
                    warnings.append(
                        f"  Bus check — crystal: {ref} pin '{_pin_name(p, pin_matrix)}' "
                        f"on net '{name}' has no load capacitor to GND"
                    )
    return nets


# ── Rule 6: Power flag required ─────────────────────────────────────────


def _check_power_flag_required(
    nets: list[dict],
    pin_matrix: dict,
    comps: list,
    warnings: list,
) -> list[dict]:
    """Warn when a power rail net (VBUS, 3V3, 5V, VIN, VSYS, etc.) has
    NO pin with etype "power_out" driving it.

    In KiCad's ERC model, every power net must have at least one pin
    typed "power_out" or a PWR_FLAG symbol.  Connectors and regulators
    typically have "passive" or "power_in" pins, which do NOT satisfy
    the ERC — hence the PWR_FLAG requirement.

    GND is exempt — KiCad treats it as a global power source internally.
    """
    POWER_RAILS = frozenset({
        "VBUS", "VIN", "VSYS", "3V3", "5V", "VCC", "VDD",
        "VOUT", "V+", "V-", "3.3V", "1.8V", "1.2V",
    })

    for net in nets:
        name = net.get("net", "").strip().upper()
        pins = net.get("pins", [])

        if name not in POWER_RAILS and not _is_power_net(name):
            continue
        if _is_gnd_net(name):
            continue

        has_power_out = any(
            _pin_etype(p, pin_matrix).lower() == "power_out"
            for p in pins
        )
        if not has_power_out:
            warnings.append(
                f"  Bus check — power flag: net '{name}' has no power-output pin "
                f"driving it. Place a PWR_FLAG symbol on this net for ERC compliance."
            )
    return nets


# ── Rule 7: Auto-generated net names ────────────────────────────────────


def _check_auto_named_nets(
    nets: list[dict],
    pin_matrix: dict,
    warnings: list,
) -> list[dict]:
    """Flag nets whose name looks auto-generated (KiCad's default naming).

    Auto-generated names like "NET-U1-47" or "N-0001" make net-class
    assignment in the PCB editor impossible and confuse bring-up.
    Every functional signal should have a descriptive name.
    """
    _AUTO_PATTERN = re.compile(
        r'^(?:NET[-_])|(?:N[-_]\d+)',
        re.IGNORECASE,
    )

    for net in nets:
        name = net.get("net", "")
        if _is_gnd_net(name) or _is_power_net(name):
            continue
        if _AUTO_PATTERN.match(name):
            warnings.append(
                f"  Bus check — auto-named net: '{name}' looks like an auto-generated "
                f"name. Replace with a descriptive signal name (e.g., SENSOR_INT, "
                f"CHG_EN) so net-class assignment works in the PCB editor."
            )
    return nets


# ── Public entry point ──────────────────────────────────────────────────


def check_bus_topology(
    nets: list[dict],
    pin_matrix: dict,
    comps: list,
) -> tuple[list[dict], list[str], set[str]]:
    """Run all deterministic bus-topology checks on LLM-generated nets.

    Args:
        nets: List of net dicts from netlist_node (after LLM batches).
        pin_matrix: Full pin matrix from dispatch_node.
        comps: List of selected components.

    Returns:
        (corrected_nets, warnings, removed_pins):
          corrected_nets — nets with violations repaired (pins removed/merged).
          warnings — human-readable strings describing each fix.
          removed_pins — pin keys ejected from power/GND nets by power isolation.
                        Caller must remove these from ``assigned`` so the
                        fallback can recover them.
    """
    warnings: list[str] = []
    all_removed_pins: set[str] = set()

    # Rule order matters — I2C merge first to collapse aliases, then check
    # the merged result for power/short issues.
    nets = _check_i2c_merge(nets, pin_matrix, warnings)
    nets, removed = _check_power_isolation(nets, pin_matrix, comps, warnings)
    all_removed_pins.update(removed)
    nets = _check_uart_cross(nets, pin_matrix, warnings)
    nets = _check_same_component_short(nets, pin_matrix, comps, warnings)
    nets = _check_crystal_load_caps(nets, pin_matrix, comps, warnings)
    nets = _check_power_flag_required(nets, pin_matrix, comps, warnings)
    nets = _check_auto_named_nets(nets, pin_matrix, warnings)

    return nets, warnings, all_removed_pins
