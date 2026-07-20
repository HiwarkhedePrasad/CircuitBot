"""Deterministic pin-matching rules for common circuit patterns.

Each rule matches known component+pin patterns at the netlist stage and
produces connections deterministically — zero LLM calls.  Pins consumed by
a rule are removed from the LLM's signal pool, letting it focus on novel or
ambiguous connections only.

Usage::

    from agent.pin_matcher import match_pins

    result = match_pins(components, pin_matrix, existing_nets, existing_power_pins)
    # merge result["new_nets"], result["new_power_pins"],
    # result["new_netlist"], mark result["matched_pins"] as assigned
"""

from __future__ import annotations

from typing import Any

# ── Helpers ────────────────────────────────────────────────────────────────


def _canonical(name: str) -> str:
    """Strip formatting noise from KiCad pin names for comparison."""
    return (
        name.strip()
        .upper()
        .replace("{", "")
        .replace("}", "")
        .replace("_", "")
        .replace(" ", "")
    )


def _id_str(ref_des: str, components: list[dict]) -> str:
    for c in components:
        if c["ref_des"] == ref_des:
            return c.get("id_str", "")
    return ""


def _category(ref_des: str, components: list[dict]) -> str:
    for c in components:
        if c["ref_des"] == ref_des:
            return c.get("category", "")
    return ""


def _lib_prefix(ref_des: str, components: list[dict]) -> str:
    id_str = _id_str(ref_des, components)
    return id_str.split(":")[0] if ":" in id_str else ""


# ── MatchResult ────────────────────────────────────────────────────────────


class MatchResult:
    """Collects everything a rule produces."""

    def __init__(self):
        self.new_nets: list[dict[str, Any]] = []
        self.new_power_pins: list[dict[str, str]] = []
        self.new_netlist: list[dict[str, str]] = []
        self.matched_pins: set[str] = set()

    def merge(self, other: MatchResult):
        self.new_nets.extend(other.new_nets)
        self.new_power_pins.extend(other.new_power_pins)
        self.new_netlist.extend(other.new_netlist)
        self.matched_pins.update(other.matched_pins)

    def to_dict(self) -> dict:
        return {
            "new_nets": self.new_nets,
            "new_power_pins": self.new_power_pins,
            "new_netlist": self.new_netlist,
            "matched_pins": self.matched_pins,
        }


# ── Rule 1: Temp sensor V_{OUT} → MCU ADC input ──────────────────────────

_RULE1_TEMP_OUTPUT = frozenset({"VOUT", "V_OUT", "VTEMP", "TEMPOUT", "TEMP_OUT"})
_RULE1_MCU_ADC = frozenset({"SENSORVP", "SENSORVN", "SENSOR_VP", "SENSOR_VN",
                            "ADC_IN0", "ADC_IN1", "ADC0", "ADC1"})


def _match_temp_sensor_to_adc(
    components: list[dict],
    pin_matrix: dict[str, dict],
    assigned: set[str],
) -> MatchResult:
    result = MatchResult()
    temp_candidates: list[tuple[str, str, str]] = []
    mcu_adc_candidates: list[tuple[str, str, str]] = []

    for key, pin in pin_matrix.items():
        if key in assigned:
            continue
        ref = key.split(":")[0]
        name = _canonical(pin.get("name", ""))
        etype = pin.get("etype", "")

        if name in _RULE1_TEMP_OUTPUT and etype == "output":
            temp_candidates.append((ref, key, name))
        elif name in _RULE1_MCU_ADC and etype == "input":
            mcu_adc_candidates.append((ref, key, name))

    if not temp_candidates or not mcu_adc_candidates:
        return result

    for t_ref, t_key, _ in temp_candidates:
        for m_ref, m_key, m_name in mcu_adc_candidates:
            if t_key in result.matched_pins or m_key in result.matched_pins:
                continue
            cat = _category(m_ref, components)
            lib = _lib_prefix(m_ref, components)
            is_mcu = cat.upper() == "MCU" or "MCU" in lib.upper() or "ESP32" in lib.upper()
            if not is_mcu:
                continue
            net_name = f"{t_ref}_ADC"
            result.new_nets.append({"net": net_name, "pins": [t_key, m_key]})
            result.new_netlist.append({"source": t_key, "target": m_key, "net": net_name})
            result.matched_pins.update({t_key, m_key})

    return result


# ── Rule 2: Power terminal → VDD rail + GND ──────────────────────────────

_TERMINAL_PATTERNS = frozenset({"SCREW_TERMINAL", "TERMINAL_BLOCK", "CONN_01X02"})


def _match_power_terminal(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    result = MatchResult()

    terminals: list[str] = []
    for c in components:
        id_str = c.get("id_str", "").upper()
        for pat in _TERMINAL_PATTERNS:
            if pat in id_str:
                terminals.append(c["ref_des"])
                break

    if not terminals:
        return result

    # Discover available power rails from existing power pins
    power_rails: set[str] = set()
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND",):
            power_rails.add(rail)

    # If no VDD/VCC/VIN rail exists, prefer VDD as default
    if not power_rails:
        return result

    primary_rail = None
    for preferred in ("VDD", "VCC", "VIN", "3V3", "5V"):
        if preferred in power_rails:
            primary_rail = preferred
            break
    if not primary_rail:
        primary_rail = next(iter(power_rails))

    for term_ref in terminals:
        term_pins = [
            key for key in pin_matrix
            if key.split(":")[0] == term_ref and key not in assigned
        ]
        term_pins.sort()
        hva_pin = term_pins[0] if term_pins else None
        gnd_pin = term_pins[1] if len(term_pins) > 1 else None

        if hva_pin and hva_pin not in result.matched_pins:
            net_name = primary_rail
            result.new_nets.append({"net": net_name, "pins": [hva_pin]})
            result.new_power_pins.append({"pin": hva_pin, "net": net_name})
            result.matched_pins.add(hva_pin)

        if gnd_pin and gnd_pin not in result.matched_pins:
            result.new_nets.append({"net": "GND", "pins": [gnd_pin]})
            result.new_power_pins.append({"pin": gnd_pin, "net": "GND"})
            result.matched_pins.add(gnd_pin)

    return result


# ── Rule 3: Decoupling capacitor → VDD/GND of nearest MCU ────────────────

_CAPACITOR_PATTERNS = frozenset({"C_SMALL", "C_POLARIZED", "CAPACITOR",
                                 "CP_SMALL", "CP_", "C_SMALL_",
                                 "C_POLARIZED_SMALL"})


def _match_decoupling_cap(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    result = MatchResult()

    caps: list[str] = []
    for c in components:
        id_str = c.get("id_str", "").upper()
        for pat in _CAPACITOR_PATTERNS:
            if pat in id_str:
                caps.append(c["ref_des"])
                break

    if not caps:
        return result

    # Discover power rails
    power_rails: dict[str, str] = {}  # canon name -> first pin example
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND",) and rail not in power_rails:
            power_rails[rail] = pp["pin"]

    if not power_rails:
        return result

    primary_rail = None
    for preferred in ("VDD", "VCC", "VIN", "3V3", "5V"):
        if preferred in power_rails:
            primary_rail = preferred
            break
    if not primary_rail:
        primary_rail = next(iter(power_rails))

    gnd_pin_example = None
    for pp in existing_power_pins:
        if pp.get("net", "").upper() == "GND":
            gnd_pin_example = pp["pin"]
            break

    for cap_ref in caps:
        cap_pins = sorted([
            key for key in pin_matrix
            if key.split(":")[0] == cap_ref and key not in assigned
            and key not in result.matched_pins
        ])
        if len(cap_pins) < 2:
            continue

        power_pin, gnd_assign = cap_pins[0], cap_pins[1]

        if power_pin not in result.matched_pins:
            result.new_nets.append({"net": primary_rail, "pins": [power_pin]})
            result.new_power_pins.append({"pin": power_pin, "net": primary_rail})
            result.matched_pins.add(power_pin)

        if gnd_assign not in result.matched_pins:
            result.new_nets.append({"net": "GND", "pins": [gnd_assign]})
            result.new_power_pins.append({"pin": gnd_assign, "net": "GND"})
            result.matched_pins.add(gnd_assign)

    return result


# ── Rule 4: USB-C receptacle deterministic wiring ────────────────────────

_USB_C_CONNECTOR_KEYWORDS = frozenset({"USB_C", "USB-C", "TYPEC", "TYPE_C", "USB_C_RECEPTACLE"})


def _match_usb_c_receptacle(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire USB-C receptacle pins deterministically:
    - VBUS → power rail (VBUS/VDD/VCC)
    - GND/SHIELD → GND
    - CC1/CC2 → GND (5.1kΩ pulldown for USB sink detection)
    - SBU1/SBU2 → GND (unused in USB 2.0)
    - D+/D- → left for LLM (connects to MCU or USB-UART bridge)
    """
    result = MatchResult()

    usb_refs: list[str] = []
    for c in components:
        id_upper = c.get("id_str", "").upper()
        if any(kw in id_upper for kw in _USB_C_CONNECTOR_KEYWORDS):
            usb_refs.append(c["ref_des"])

    if not usb_refs:
        return result

    # Discover available power rails
    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS") and rail not in power_rails:
            power_rails[rail] = pp["pin"]

    vbus_rail = None
    for preferred in ("VBUS", "VDD", "VCC", "5V", "VIN"):
        if preferred in power_rails:
            vbus_rail = preferred
            break
    if not vbus_rail:
        vbus_rail = "VBUS"

    for ref in usb_refs:
        # Build name -> [pin_key, ...] to preserve ALL pins per name
        usb_pins: dict[str, list[str]] = {}
        for k in pin_matrix:
            if k.split(":")[0] == ref and k not in assigned:
                cname = _canonical(pin_matrix[k].get("name", ""))
                usb_pins.setdefault(cname, []).append(k)

        # VBUS -> power rail (match ALL VBUS pins, not just the first)
        for vbus_name in ("VBUS", "VBUS1", "VBUS2"):
            for pk in usb_pins.get(vbus_name, []):
                if pk not in result.matched_pins:
                    result.new_nets.append({"net": vbus_rail, "pins": [pk]})
                    result.new_power_pins.append({"pin": pk, "net": vbus_rail})
                    result.matched_pins.add(pk)

        # GND pins
        for gnd_name in ("GND", "GND1", "GND2"):
            if gnd_name in usb_pins and usb_pins[gnd_name] not in result.matched_pins:
                pk = usb_pins[gnd_name]
                result.new_nets.append({"net": "GND", "pins": [pk]})
                result.new_power_pins.append({"pin": pk, "net": "GND"})
                result.matched_pins.add(pk)

        # SHIELD → GND
        if "SHIELD" in usb_pins and usb_pins["SHIELD"] not in result.matched_pins:
            pk = usb_pins["SHIELD"]
            result.new_nets.append({"net": "GND", "pins": [pk]})
            result.new_power_pins.append({"pin": pk, "net": "GND"})
            result.matched_pins.add(pk)

        # CC1/CC2 → GND (5.1kΩ pulldown for sink detection)
        for cc_name in ("CC1", "CC2"):
            if cc_name in usb_pins and usb_pins[cc_name] not in result.matched_pins:
                pk = usb_pins[cc_name]
                result.new_nets.append({"net": "GND", "pins": [pk]})
                result.matched_pins.add(pk)

        # SBU1/SBU2 → GND (unused in USB 2.0)
        for sbu_name in ("SBU1", "SBU2"):
            if sbu_name in usb_pins and usb_pins[sbu_name] not in result.matched_pins:
                pk = usb_pins[sbu_name]
                result.new_nets.append({"net": "GND", "pins": [pk]})
                result.matched_pins.add(pk)

    return result


# ── Orchestrator ──────────────────────────────────────────────────────────


def _discover_power_rails(existing_nets: list[dict],
                          assigned: set[str] | None = None
                          ) -> list[dict[str, str]]:
    """Extract a ``power_pins``-style list from *existing_nets*.

    Used when ``existing_power_pins`` isn't available yet (pin matcher
    runs before the power-pins list is built in the netlist node).
    """
    power_pins: list[dict[str, str]] = []
    for net in existing_nets:
        name = net.get("net", "")
        if not name:
            continue
        canon = name.upper().lstrip("+")
        if canon in ("GND", "GROUND", "VSS", "VEE"):
            continue
        for p in net.get("pins", []):
            if assigned is not None and p not in assigned:
                continue
            power_pins.append({"pin": p, "net": name})
    return power_pins


def match_pins(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_nets: list[dict],
    existing_power_pins: list[dict[str, str]] | None = None,
    assigned: set[str] | None = None,
) -> dict:
    """Run all deterministic pin-matching rules.

    Parameters
    ----------
    components : list[dict]
        Selected components with ref_des, id_str, category.
    pin_matrix : dict[str, dict]
        Pin-key → pin-info mapping.
    existing_nets : list[dict]
        Nets already created (power/GND pre-assignment).
    existing_power_pins : list[dict[str, str]] | None
        Power pins already assigned.  If ``None`` it is derived from
        *existing_nets* via :func:`_discover_power_rails`.
    assigned : set[str] | None
        Pin keys already consumed (power/GND pre-assigned).  If ``None``
        a fresh set is built from *existing_nets* and *existing_power_pins*.

    Returns
    -------
    dict with keys:
        new_nets : list[dict]
            Net objects to merge via ``_merge_net``.
        new_power_pins : list[dict[str, str]]
            Extra power-pin entries.
        new_netlist : list[dict[str, str]]
            Signal wire connections ``{source, target, net}``.
        matched_pins : set[str]
            Pin keys consumed — add to the caller's ``assigned`` set.
    """
    if assigned is None:
        assigned = set()
        for net in existing_nets:
            assigned.update(net.get("pins", []))
        if existing_power_pins:
            for pp in existing_power_pins:
                assigned.add(pp["pin"])

    if existing_power_pins is None:
        existing_power_pins = _discover_power_rails(existing_nets, assigned)

    result = MatchResult()

    _RULES = [
        ("temp sensor → ADC",     _match_temp_sensor_to_adc,       (components, pin_matrix, assigned)),
        ("power terminal",        _match_power_terminal,           (components, pin_matrix, existing_power_pins, assigned)),
        ("decoupling capacitor",  _match_decoupling_cap,           (components, pin_matrix, existing_power_pins, assigned)),
        ("USB-C receptacle",      _match_usb_c_receptacle,         (components, pin_matrix, existing_power_pins, assigned)),
    ]
    for rule_name, rule_fn, rule_args in _RULES:
        try:
            sub = rule_fn(*rule_args)
            result.merge(sub)
        except Exception as ex:
            import sys
            print(f"Pin matcher: rule '{rule_name}' failed ({type(ex).__name__}: {ex}) — skipping",
                  file=sys.stderr)

    return result.to_dict()
