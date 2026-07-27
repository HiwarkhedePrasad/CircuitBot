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
        .replace("-", "")
    )


def _id_str(ref_des: str, components: list[dict]) -> str:
    for c in components:
        if c.get("ref_des") == ref_des:
            return c.get("id_str", "")
    return ""


def _category(ref_des: str, components: list[dict]) -> str:
    for c in components:
        if c.get("ref_des") == ref_des:
            return c.get("category", "")
    return ""


def _lib_prefix(ref_des: str, components: list[dict]) -> str:
    id_str = _id_str(ref_des, components)
    return id_str.split(":")[0] if ":" in id_str else ""


def _pins_for_ref(ref: str, pin_matrix: dict) -> dict[str, list[str]]:
    """Build map of pin_num -> [pin_key, ...] and canonical_name -> [pin_key, ...] for a component."""
    result: dict[str, list[str]] = {}
    for key, pin in pin_matrix.items():
        if key.split(":")[0] == ref:
            pin_num = pin.get("num") or pin.get("number") or (key.split(":")[-1] if ":" in key else "")
            if pin_num:
                result.setdefault(str(pin_num).strip().upper(), []).append(key)
            cname = _canonical(pin.get("name", ""))
            if cname:
                result.setdefault(cname, []).append(key)
    return result


def _find_ref_by_lib(components: list[dict], lib_pattern: str) -> list[str]:
    """Find all ref_des whose library prefix matches."""
    results = []
    for c in components:
        lib = _lib_prefix(c.get("ref_des", ""), components)
        if lib_pattern in lib:
            results.append(c["ref_des"])
    return results


def _find_ref_by_id(components: list[dict], id_pattern: str) -> list[str]:
    """Find all ref_des whose id_str (uppercased) contains pattern."""
    id_upper = id_pattern.upper()
    results = []
    for c in components:
        cid = (c.get("id_str", "") or "").upper()
        if id_upper in cid:
            results.append(c["ref_des"])
    return results


def _find_discrete_passives(components: list[dict], pin_matrix: dict, assigned: set[str]) -> list[str]:
    """Find refs that are discrete passives (device lib, 2 pins)."""
    results = []
    for c in components:
        ref = c.get("ref_des", "")
        lib = _lib_prefix(ref, components)
        if lib != "Device":
            continue
        # Count unassigned pins
        unassigned = sum(1 for k in pin_matrix if k.startswith(f"{ref}:") and k not in assigned)
        if unassigned >= 2:
            results.append(ref)
    return results


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
    power_rails: set[str] = set()
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND",):
            power_rails.add(rail)
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
    power_rails: dict[str, str] = {}
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
    result = MatchResult()
    usb_refs: list[str] = []
    for c in components:
        id_upper = c.get("id_str", "").upper()
        if any(kw in id_upper for kw in _USB_C_CONNECTOR_KEYWORDS):
            usb_refs.append(c["ref_des"])
    if not usb_refs:
        return result
    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS") and rail not in power_rails:
            power_rails[rail] = pp["pin"]
    vbus_rail = None
    for preferred in ("VBUS", "5V", "VUSB", "VIN"):
        if preferred in power_rails:
            vbus_rail = preferred
            break
    if not vbus_rail:
        vbus_rail = "VBUS"

    cc_resistors = []
    for comp in components:
        value = str(comp.get("value", "") or "").lower().replace("ohm", "").replace("Ω", "")
        description = str(comp.get("description", "") or "").upper()
        if ("5.1k" in value or "5k1" in value) and "CC" in description:
            pins_for_resistor = [
                key for key in pin_matrix
                if key.split(":")[0] == comp.get("ref_des") and key not in assigned
            ]
            if len(pins_for_resistor) >= 2:
                cc_resistors.append((comp.get("ref_des", ""), pins_for_resistor[:2]))

    for ref in usb_refs:
        usb_pins: dict[str, list[str]] = {}
        for k in pin_matrix:
            if k.split(":")[0] == ref and k not in assigned:
                cname = _canonical(pin_matrix[k].get("name", ""))
                usb_pins.setdefault(cname, []).append(k)
        for vbus_name in ("VBUS", "VBUS1", "VBUS2"):
            for pk in usb_pins.get(vbus_name, []):
                if pk not in result.matched_pins:
                    result.new_nets.append({"net": vbus_rail, "pins": [pk]})
                    result.new_power_pins.append({"pin": pk, "net": vbus_rail})
                    result.matched_pins.add(pk)
        for gnd_name in ("GND", "GND1", "GND2"):
            for pk in usb_pins.get(gnd_name, []):
                if pk not in result.matched_pins:
                    result.new_nets.append({"net": "GND", "pins": [pk]})
                    result.new_power_pins.append({"pin": pk, "net": "GND"})
                    result.matched_pins.add(pk)
        for shield_name in ("SHIELD", "SHIELD1", "SHIELD2"):
            for pk in usb_pins.get(shield_name, []):
                if pk not in result.matched_pins:
                    result.new_nets.append({"net": "GND", "pins": [pk]})
                    result.matched_pins.add(pk)
        for cc_name, resistor in zip(("CC1", "CC2"), cc_resistors):
            for pk in usb_pins.get(cc_name, []):
                if pk in result.matched_pins:
                    continue
                _, (cc_side, ground_side) = resistor
                result.new_nets.append({"net": cc_name, "pins": [pk, cc_side]})
                result.new_nets.append({"net": "GND", "pins": [ground_side]})
                result.matched_pins.update((pk, cc_side, ground_side))
        # SBU pins are optional for USB 2.0 sinks and must remain NC unless a
        # concrete alternate-mode circuit was requested. Grounding them is a
        # topology error, not a harmless default.
    return result


# ── Rule 5: BME280/BMP280 I2C sensor wiring ─────────────────────────────

_BME_SENSOR_IDS = frozenset({"BME280", "BMP280", "BME680", "BMP390", "BMP388"})


def _match_bme_sensor_i2c(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire BME280/BMP280 I2C sensor:
    VDD → existing power rail
    GND → GND
    SCL → I2C_SCL signal net
    SDA → I2C_SDA signal net
    CSB → VDD (tie to VDD for I2C mode)
    SDO → GND (tie to GND for 0x76 address)
    """
    result = MatchResult()
    sensor_refs = _find_ref_by_id(components, "TMP117") or \
                   _find_ref_by_id(components, "BME280") or \
                   _find_ref_by_id(components, "BMP280") or \
                   _find_ref_by_id(components, "TMP1075") or \
                   _find_ref_by_id(components, "MCP9808") or \
                   _find_ref_by_id(components, "BME680")

    if not sensor_refs:
        return result

    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS") and rail not in power_rails:
            power_rails[rail] = pp["pin"]
    primary_rail = None
    for preferred in ("VDD", "VCC", "3V3", "5V", "VIN", "VBUS"):
        if preferred in power_rails:
            primary_rail = preferred
            break
    if not primary_rail and power_rails:
        primary_rail = next(iter(power_rails))
    if not primary_rail:
        primary_rail = "VDD"

    for sref in sensor_refs:
        pins = _pins_for_ref(sref, pin_matrix)

        # VDD → power rail
        for vdd_name in ("VDD", "VCC", "VIN", "3V3"):
            if vdd_name in pins:
                for pk in pins[vdd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": primary_rail, "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": primary_rail})
                        result.matched_pins.add(pk)
                break

        # GND → GND
        for gnd_name in ("GND", "VSS", "AGND"):
            if gnd_name in pins:
                for pk in pins[gnd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": "GND"})
                        result.matched_pins.add(pk)
                break

        # SCL → I2C_SCL
        for scl_name in ("SCL", "SCK", "SPC"):
            if scl_name in pins:
                for pk in pins[scl_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "I2C_SCL", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

        # SDA → I2C_SDA
        for sda_name in ("SDA", "SDI", "SID"):
            if sda_name in pins:
                for pk in pins[sda_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "I2C_SDA", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

        # CSB → VDD (I2C mode)
        for cs_name in ("CSB", "CS", "NCS", "CHIPSELECT"):
            if cs_name in pins:
                for pk in pins[cs_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": primary_rail, "pins": [pk]})
                        result.matched_pins.add(pk)
                break

        # SDO / ADDR / ADD0 → GND (default I2C address 0x48 / 0x76)
        for sdo_name in ("SDO", "ADDR", "ADDRESS", "ADD0", "ADDR0", "AD0", "A0", "A1"):
            if sdo_name in pins:
                for pk in pins[sdo_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

    return result


# ── Rule 6: DS18B20 1-Wire sensor wiring ────────────────────────────────

def _match_ds18b20_onewire(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire DS18B20 1-Wire sensor:
    VDD → existing power rail
    GND → GND
    DQ → OW_DQ signal net
    """
    result = MatchResult()
    sensor_refs = _find_ref_by_id(components, "DS18B20") or \
                   _find_ref_by_id(components, "DS18S20")

    if not sensor_refs:
        return result

    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS") and rail not in power_rails:
            power_rails[rail] = pp["pin"]
    primary_rail = None
    for preferred in ("VDD", "VCC", "3V3", "5V", "VIN"):
        if preferred in power_rails:
            primary_rail = preferred
            break
    if not primary_rail and power_rails:
        primary_rail = next(iter(power_rails))
    if not primary_rail:
        primary_rail = "VDD"

    for sref in sensor_refs:
        pins = _pins_for_ref(sref, pin_matrix)

        for vdd_name in ("VDD", "VCC"):
            if vdd_name in pins:
                for pk in pins[vdd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": primary_rail, "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": primary_rail})
                        result.matched_pins.add(pk)
                break

        for gnd_name in ("GND", "VSS"):
            if gnd_name in pins:
                for pk in pins[gnd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": "GND"})
                        result.matched_pins.add(pk)
                break

        for dq_name in ("DQ", "DATA", "OUT"):
            if dq_name in pins:
                for pk in pins[dq_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "OW_DQ", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

    return result


# ── Rule 7: LED + current-limiting resistor wiring ──────────────────────

def _match_led_with_resistor(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire LED to a GPIO via current-limiting resistor.
    LED anode → resistor → MCU GPIO
    LED cathode → GND
    """
    result = MatchResult()
    led_refs = [c["ref_des"] for c in components
                if (c.get("id_str", "") or "").upper().startswith("DEVICE:LED")]
    if not led_refs:
        return result

    # Find a current-limiting resistor description
    resistor_refs = [c["ref_des"] for c in components
                     if "330" in ((c.get("description", "") or "") + (c.get("value", "") or ""))
                     or "current limit" in ((c.get("description", "") or "") + (c.get("justification", "") or "")).lower()]

    for led_ref in led_refs:
        pins = _pins_for_ref(led_ref, pin_matrix)

        # LED cathode → GND
        for cathode_name in ("CATHODE", "K", "C", "NEG", "-"):
            if cathode_name in pins:
                for pk in pins[cathode_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": "GND"})
                        result.matched_pins.add(pk)
                break

        # LED anode → signal (left for LLM to connect to GPIO)
        for anode_name in ("ANODE", "A", "POS", "+"):
            if anode_name in pins:
                for pk in pins[anode_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        # Create a named net, but leave it unconnected — LLM will
                        # connect it to the appropriate MCU GPIO.
                        net_name = f"{led_ref}_LED"
                        result.new_nets.append({"net": net_name, "pins": [pk]})
                        result.matched_pins.add(pk)
                break

    return result


# ── Rule 8: LDO regulator wiring ──────────────────────────────────────

_LDO_LIBS = frozenset({"REGULATOR_LINEAR", "REGULATOR_SWITCHING"})


def _match_ldo_regulator(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire LDO regulator:
    IN/VIN → input power rail
    OUT/VOUT → output power rail (create if needed)
    GND → GND
    EN → input power rail (if pin exists)
    """
    result = MatchResult()
    reg_refs = []
    for c in components:
        lib = _lib_prefix(c.get("ref_des", ""), components)
        if lib in _LDO_LIBS:
            reg_refs.append(c["ref_des"])
    if not reg_refs:
        return result

    # Discover input power rail
    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS"):
            power_rails[rail] = pp["pin"]
    input_rail = None
    for preferred in ("VBUS", "VIN", "5V", "VDD", "VCC"):
        if preferred in power_rails:
            input_rail = preferred
            break
    if not input_rail and power_rails:
        input_rail = next(iter(power_rails))

    for rref in reg_refs:
        pins = _pins_for_ref(rref, pin_matrix)

        # IN → input power rail
        for in_name in ("IN", "VIN", "INPUT", "VI"):
            if in_name in pins:
                for pk in pins[in_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        rail_name = input_rail or "VIN"
                        result.new_nets.append({"net": rail_name, "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": rail_name})
                        result.matched_pins.add(pk)
                break

        # OUT → regulated 3.3V rail
        for out_name in ("OUT", "VOUT", "VO"):
            if out_name in pins:
                for pk in pins[out_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        out_rail = "3V3"
                        result.new_nets.append({"net": out_rail, "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": out_rail})
                        result.matched_pins.add(pk)
                break

        # Linear-regulator sense/feedback pins are tied to the regulated
        # output when the symbol exposes them. Switching regulators require a
        # dedicated topology and are not covered by this rule.
        if _lib_prefix(rref, components) == "Regulator_Linear":
            for sense_name in ("VSENSE", "SENSE", "FB"):
                for pk in pins.get(sense_name, []):
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "3V3", "pins": [pk]})
                        result.matched_pins.add(pk)

        # GND → GND
        for gnd_name in ("GND", "VSS", "PAD", "EP", "EPAD"):
            if gnd_name in pins:
                for pk in pins[gnd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": "GND"})
                        result.matched_pins.add(pk)
                break

        # EN → VIN (tie to input if not driven externally)
        for en_name in ("EN", "ENA", "ENABLE", "ON/OFF"):
            if en_name in pins:
                for pk in pins[en_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        rail_name = input_rail or "VIN"
                        result.new_nets.append({"net": rail_name, "pins": [pk]})
                        result.matched_pins.add(pk)
                break

    return result


# ── Rule 9: Crystal wiring ─────────────────────────────────────────────

def _match_crystal(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire crystal to MCU oscillator pins.
    XTAL1 → MCU OSC_IN
    XTAL2 → MCU OSC_OUT
    Load cap 1 → GND
    Load cap 2 → GND
    """
    result = MatchResult()
    crystal_refs = [c["ref_des"] for c in components
                    if (c.get("id_str", "") or "").upper().startswith("DEVICE:CRYSTAL")]
    if not crystal_refs:
        return result

    # Find MCU refs
    mcu_refs = [c["ref_des"] for c in components
                if "MCU" in _lib_prefix(c.get("ref_des", ""), components).upper()
                or "ESP32" in (c.get("id_str", "") or "").upper()
                or "RP2040" in (c.get("id_str", "") or "").upper()
                or "STM32" in (c.get("id_str", "") or "").upper()
                or "ATMEGA" in (c.get("id_str", "") or "").upper()]

    if not mcu_refs:
        return result

    for cref in crystal_refs:
        cpins = _pins_for_ref(cref, pin_matrix)

        # Crystal pins: find the two signal pins
        xtal_pins = []
        for name in ("1", "2", "PAD1", "PAD2", "OUT1", "OUT2", "OSC_OUT", "OSC_IN"):
            if name in cpins:
                xtal_pins.extend(cpins[name])

        if len(xtal_pins) < 2:
            continue

        # Match to MCU OSC pins
        for mref in mcu_refs:
            mpins = _pins_for_ref(mref, pin_matrix)

            # Find MCU OSC_IN/OSC_OUT
            mcu_osc_in = []
            mcu_osc_out = []
            for osc_name in ("OSC_IN", "OSCI", "XTAL1", "XTAL_IN", "XIN", "PC14"):
                if osc_name in mpins:
                    mcu_osc_in.extend(mpins[osc_name])
            for osc_name in ("OSC_OUT", "OSCO", "XTAL2", "XTAL_OUT", "XOUT", "PC15"):
                if osc_name in mpins:
                    mcu_osc_out.extend(mpins[osc_name])

            if mcu_osc_in and mcu_osc_out:
                # Connect crystal OUT1 → MCU OSC_IN, OUT2 → OSC_OUT
                if len(xtal_pins) >= 2:
                    p1, p2 = xtal_pins[0], xtal_pins[1]
                    if mcu_osc_in[0] not in assigned and p1 not in result.matched_pins:
                        result.new_nets.append({"net": "XTAL_IN", "pins": [p1, mcu_osc_in[0]]})
                        result.matched_pins.update({p1, mcu_osc_in[0]})
                    if mcu_osc_out[0] not in assigned and p2 not in result.matched_pins:
                        result.new_nets.append({"net": "XTAL_OUT", "pins": [p2, mcu_osc_out[0]]})
                        result.matched_pins.update({p2, mcu_osc_out[0]})
                break

    return result


# ── Rule 10: USB-UART bridge wiring ────────────────────────────────────

def _match_usb_uart_bridge(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire USB-UART bridge to MCU:
    TXD → MCU RXD
    RXD → MCU TXD
    VDD → power rail
    GND → GND
    """
    result = MatchResult()
    bridge_refs = [c["ref_des"] for c in components
                   if _lib_prefix(c.get("ref_des", ""), components) == "Interface_USB"]
    if not bridge_refs:
        return result

    # Find MCU refs
    mcu_refs = [c["ref_des"] for c in components
                if "MCU" in _lib_prefix(c.get("ref_des", ""), components).upper()
                or "ESP32" in (c.get("id_str", "") or "").upper()
                or "RP2040" in (c.get("id_str", "") or "").upper()
                or "STM32" in (c.get("id_str", "") or "").upper()
                or "ATMEGA" in (c.get("id_str", "") or "").upper()]

    if not mcu_refs:
        return result

    for bref in bridge_refs:
        bpins = _pins_for_ref(bref, pin_matrix)

        # Bridge TXD → MCU RXD
        bridge_tx = []
        for tx_name in ("TXD", "TX", "TXD_OUT", "DOUT", "SOUT"):
            if tx_name in bpins:
                bridge_tx.extend(bpins[tx_name])

        # Bridge RXD → MCU TXD
        bridge_rx = []
        for rx_name in ("RXD", "RX", "RXD_IN", "DIN", "SIN"):
            if rx_name in bpins:
                bridge_rx.extend(bpins[rx_name])

        for mref in mcu_refs:
            mpins = _pins_for_ref(mref, pin_matrix)

            mcu_rx = []
            for rx_name in ("RXD", "RX", "UART_RX", "GPIO_RX"):
                if rx_name in mpins:
                    mcu_rx.extend(mpins[rx_name])

            mcu_tx = []
            for tx_name in ("TXD", "TX", "UART_TX", "GPIO_TX"):
                if tx_name in mpins:
                    mcu_tx.extend(mpins[tx_name])

            if bridge_tx and mcu_rx:
                pk = bridge_tx[0]
                mk = mcu_rx[0]
                if pk not in assigned and mk not in assigned \
                   and pk not in result.matched_pins and mk not in result.matched_pins:
                    result.new_nets.append({"net": "UART_TX", "pins": [pk, mk]})
                    result.matched_pins.update({pk, mk})

            if bridge_rx and mcu_tx:
                pk = bridge_rx[0]
                mk = mcu_tx[0]
                if pk not in assigned and mk not in assigned \
                   and pk not in result.matched_pins and mk not in result.matched_pins:
                    result.new_nets.append({"net": "UART_RX", "pins": [pk, mk]})
                    result.matched_pins.update({pk, mk})

    return result


# ── Rule 11: Reset button wiring ──────────────────────────────────────

def _match_reset_button(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire reset switch: one side to MCU RESET, other to GND."""
    result = MatchResult()
    switch_refs = [c["ref_des"] for c in components
                   if "SW_Push" in (c.get("id_str", "") or "").upper()
                   or "SWITCH" in _lib_prefix(c.get("ref_des", ""), components).upper()]

    if not switch_refs:
        return result

    mcu_refs = [c["ref_des"] for c in components
                if "MCU" in _lib_prefix(c.get("ref_des", ""), components).upper()
                or "ESP32" in (c.get("id_str", "") or "").upper()
                or "RP2040" in (c.get("id_str", "") or "").upper()
                or "STM32" in (c.get("id_str", "") or "").upper()
                or "ATMEGA" in (c.get("id_str", "") or "").upper()]

    if not mcu_refs:
        return result

    for sref in switch_refs:
        spins = _pins_for_ref(sref, pin_matrix)

        # Find switch pins (typically 2 pins: pin1, pin2)
        switch_pins = []
        for name in ("1", "2", "P1", "P2", "PIN1", "PIN2"):
            if name in spins:
                switch_pins.extend(spins[name])

        if len(switch_pins) < 2:
            continue

        # First switch pin → MCU RESET
        for mref in mcu_refs:
            mpins = _pins_for_ref(mref, pin_matrix)
            for rst_name in ("RST", "RESET", "NRST", "RESETB", "RSTB"):
                if rst_name in mpins:
                    pk = switch_pins[0]
                    mk = mpins[rst_name][0]
                    if pk not in assigned and mk not in assigned \
                       and pk not in result.matched_pins and mk not in result.matched_pins:
                        result.new_nets.append({"net": "RESET", "pins": [pk, mk]})
                        result.matched_pins.update({pk, mk})
                    break

        # Second switch pin → GND
        gnd_key = switch_pins[1]
        if gnd_key not in assigned and gnd_key not in result.matched_pins:
            result.new_nets.append({"net": "GND", "pins": [gnd_key]})
            result.new_power_pins.append({"pin": gnd_key, "net": "GND"})
            result.matched_pins.add(gnd_key)

    return result


# ── Rule 12: I2C OLED display wiring ──────────────────────────────────

def _match_oled_i2c(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire I2C OLED display:
    VDD → power rail
    GND → GND
    SCL → I2C_SCL
    SDA → I2C_SDA
    """
    result = MatchResult()
    oled_refs = [c["ref_des"] for c in components
                 if "SSD1306" in (c.get("id_str", "") or "").upper()
                 or "SH1106" in (c.get("id_str", "") or "").upper()
                 or "OLED" in (c.get("description", "") or "").upper()]

    if not oled_refs:
        return result

    power_rails: dict[str, str] = {}
    for pp in existing_power_pins:
        rail = pp.get("net", "").upper()
        if rail not in ("GND", "GROUND", "VSS") and rail not in power_rails:
            power_rails[rail] = pp["pin"]
    primary_rail = None
    for preferred in ("VDD", "VCC", "3V3", "5V"):
        if preferred in power_rails:
            primary_rail = preferred
            break
    if not primary_rail and power_rails:
        primary_rail = next(iter(power_rails))
    if not primary_rail:
        primary_rail = "VDD"

    for oref in oled_refs:
        pins = _pins_for_ref(oref, pin_matrix)

        for vdd_name in ("VDD", "VCC", "VIN", "3V3"):
            if vdd_name in pins:
                for pk in pins[vdd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": primary_rail, "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": primary_rail})
                        result.matched_pins.add(pk)
                break

        for gnd_name in ("GND", "VSS"):
            if gnd_name in pins:
                for pk in pins[gnd_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "GND", "pins": [pk]})
                        result.new_power_pins.append({"pin": pk, "net": "GND"})
                        result.matched_pins.add(pk)
                break

        for scl_name in ("SCL", "SCK"):
            if scl_name in pins:
                for pk in pins[scl_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "I2C_SCL", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

        for sda_name in ("SDA", "SDI"):
            if sda_name in pins:
                for pk in pins[sda_name]:
                    if pk not in assigned and pk not in result.matched_pins:
                        result.new_nets.append({"net": "I2C_SDA", "pins": [pk]})
                        result.matched_pins.add(pk)
                break

    return result


# ── Rule 13: USB-C data lines (D+/D-) ──────────────────────────────────

def _match_usb_data_lines(
    components: list[dict],
    pin_matrix: dict[str, dict],
    existing_power_pins: list[dict[str, str]],
    assigned: set[str],
) -> MatchResult:
    """Wire USB D+/D- to the nearest USB-capable device (MCU or bridge)."""
    result = MatchResult()

    usb_conn_refs = []
    for c in components:
        id_upper = c.get("id_str", "").upper()
        if any(kw in id_upper for kw in _USB_C_CONNECTOR_KEYWORDS):
            usb_conn_refs.append(c["ref_des"])

    if not usb_conn_refs:
        return result

    # Find USB-capable target: either MCU with native USB or USB-UART bridge
    target_refs = [c["ref_des"] for c in components
                   if _lib_prefix(c.get("ref_des", ""), components) == "Interface_USB"]

    if not target_refs:
        target_refs = [c["ref_des"] for c in components
                       if "ESP32" in (c.get("id_str", "") or "").upper()
                       or "RP2040" in (c.get("id_str", "") or "").upper()
                       or "SAMD" in (c.get("id_str", "") or "").upper()]

    if not target_refs:
        return result

    for uref in usb_conn_refs:
        upins = _pins_for_ref(uref, pin_matrix)

        usb_dp = upins.get("D+", []) or upins.get("DP", []) or upins.get("USBD_P", [])
        usb_dn = upins.get("D-", []) or upins.get("DN", []) or upins.get("USBD_N", [])

        for tref in target_refs:
            tpins = _pins_for_ref(tref, pin_matrix)

            target_dp = tpins.get("D+", []) or tpins.get("DP", []) or tpins.get("USBD_P", []) or tpins.get("GPIO_USB_D+", [])
            target_dn = tpins.get("D-", []) or tpins.get("DN", []) or tpins.get("USBD_N", []) or tpins.get("GPIO_USB_D-", [])

            if usb_dp and target_dp:
                pk = usb_dp[0]
                tk = target_dp[0]
                if pk not in assigned and tk not in assigned \
                   and pk not in result.matched_pins and tk not in result.matched_pins:
                    result.new_nets.append({"net": "USB_DP", "pins": [pk, tk]})
                    result.matched_pins.update({pk, tk})

            if usb_dn and target_dn:
                pk = usb_dn[0]
                tk = target_dn[0]
                if pk not in assigned and tk not in assigned \
                   and pk not in result.matched_pins and tk not in result.matched_pins:
                    result.new_nets.append({"net": "USB_DN", "pins": [pk, tk]})
                    result.matched_pins.update({pk, tk})

    return result


# ── Orchestrator ──────────────────────────────────────────────────────────


def _discover_power_rails(existing_nets: list[dict],
                          assigned: set[str] | None = None
                          ) -> list[dict[str, str]]:
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
        ("temp sensor → ADC",          _match_temp_sensor_to_adc,     (components, pin_matrix, assigned)),
        ("power terminal",             _match_power_terminal,         (components, pin_matrix, existing_power_pins, assigned)),
        ("decoupling capacitor",       _match_decoupling_cap,         (components, pin_matrix, existing_power_pins, assigned)),
        ("USB-C receptacle",           _match_usb_c_receptacle,       (components, pin_matrix, existing_power_pins, assigned)),
        ("BME/BMP I2C sensor",         _match_bme_sensor_i2c,        (components, pin_matrix, existing_power_pins, assigned)),
        ("DS18B20 1-Wire sensor",      _match_ds18b20_onewire,       (components, pin_matrix, existing_power_pins, assigned)),
        ("LED + resistor",             _match_led_with_resistor,      (components, pin_matrix, existing_power_pins, assigned)),
        ("LDO regulator",              _match_ldo_regulator,          (components, pin_matrix, existing_power_pins, assigned)),
        ("crystal",                    _match_crystal,                (components, pin_matrix, existing_power_pins, assigned)),
        ("USB-UART bridge",            _match_usb_uart_bridge,       (components, pin_matrix, existing_power_pins, assigned)),
        ("reset button",               _match_reset_button,           (components, pin_matrix, existing_power_pins, assigned)),
        ("I2C OLED display",           _match_oled_i2c,               (components, pin_matrix, existing_power_pins, assigned)),
        ("USB data lines",             _match_usb_data_lines,         (components, pin_matrix, existing_power_pins, assigned)),
    ]

    errors: list[dict] = []
    for rule_name, rule_fn, rule_args in _RULES:
        try:
            sub = rule_fn(*rule_args)
            result.merge(sub)
        except Exception as ex:
            import sys
            print(f"Pin matcher: rule '{rule_name}' failed ({type(ex).__name__}: {ex}) — skipping",
                  file=sys.stderr)
            errors.append({"rule": rule_name, "error": str(ex)})

    result_dict = result.to_dict()
    if errors:
        result_dict["errors"] = errors
    return result_dict
