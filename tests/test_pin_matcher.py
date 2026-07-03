"""Tests for deterministic pin-matching rules.

Covers:
1. Temp sensor V_{OUT} → MCU SENSOR_VP / SENSOR_VN
2. Screw terminal → VDD + GND
3. Decoupling capacitor → VDD + GND
4. Integration: match_pins orchestrator
5. Edge cases: already-assigned pins, missing data, no power rails
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.pin_matcher import (
    match_pins,
    _match_temp_sensor_to_adc,
    _match_power_terminal,
    _match_decoupling_cap,
    _discover_power_rails,
    MatchResult,
)


# ── Helpers ────────────────────────────────────────────────────────────────

_LM35_PINS = {
    "U301:1": {"x": 0, "y": 5.08, "name": "+V_{S}", "num": "1", "pin_num": "1",
               "ref_des": "U301", "etype": "power_in"},
    "U301:2": {"x": 10.16, "y": 0, "name": "V_{OUT}", "num": "2", "pin_num": "2",
               "ref_des": "U301", "etype": "output"},
    "U301:3": {"x": 0, "y": -5.08, "name": "GND", "num": "3", "pin_num": "3",
               "ref_des": "U301", "etype": "power_in"},
}

_ESP32_PINS = {
    "U101:1":  {"x": 0, "y": 0, "name": "GND",        "num": "1",  "pin_num": "1",
                "ref_des": "U101", "etype": "power_in"},
    "U101:2":  {"x": 0, "y": 0, "name": "VDD",        "num": "2",  "pin_num": "2",
                "ref_des": "U101", "etype": "power_in"},
    "U101:4":  {"x": 0, "y": 0, "name": "SENSOR_VP",  "num": "4",  "pin_num": "4",
                "ref_des": "U101", "etype": "input"},
    "U101:5":  {"x": 0, "y": 0, "name": "SENSOR_VN",  "num": "5",  "pin_num": "5",
                "ref_des": "U101", "etype": "input"},
    "U101:34": {"x": 0, "y": 0, "name": "RXD0/IO3",   "num": "34", "pin_num": "34",
                "ref_des": "U101", "etype": "bidirectional"},
    "U101:35": {"x": 0, "y": 0, "name": "TXD0/IO1",   "num": "35", "pin_num": "35",
                "ref_des": "U101", "etype": "bidirectional"},
}

_COMPONENTS = [
    {"ref_des": "U301", "id_str": "Sensor_Temperature:LM35-LP", "category": "Sensor"},
    {"ref_des": "U101", "id_str": "RF_Module:ESP32-WROOM-32D",  "category": "MCU"},
    {"ref_des": "J1",   "id_str": "Connector:Screw_Terminal_01x02", "category": "Connector"},
    {"ref_des": "C5",   "id_str": "Device:C_Small",             "category": "Device"},
    {"ref_des": "J401", "id_str": "Connector:Conn_01x04_Pin",   "category": "Connector"},
    {"ref_des": "R6",   "id_str": "Device:R_Small",             "category": "Device"},
    {"ref_des": "D501", "id_str": "Device:LED",                 "category": "Device"},
]

_ALL_PINS = {}
_ALL_PINS.update(_LM35_PINS)
_ALL_PINS.update(_ESP32_PINS)
_ALL_PINS.update({
    "J1:1":   {"x": 0, "y": 0, "name": "1", "num": "1", "pin_num": "1",
               "ref_des": "J1", "etype": "passive"},
    "J1:2":   {"x": 0, "y": 0, "name": "2", "num": "2", "pin_num": "2",
               "ref_des": "J1", "etype": "passive"},
    "C5:1":   {"x": 0, "y": 0, "name": "~", "num": "1", "pin_num": "1",
               "ref_des": "C5", "etype": "passive"},
    "C5:2":   {"x": 0, "y": 0, "name": "~", "num": "2", "pin_num": "2",
               "ref_des": "C5", "etype": "passive"},
    "D501:1": {"x": 0, "y": 0, "name": "K", "num": "1", "pin_num": "1",
               "ref_des": "D501", "etype": "passive"},
    "D501:2": {"x": 0, "y": 0, "name": "A", "num": "2", "pin_num": "2",
               "ref_des": "D501", "etype": "passive"},
})


# ── _match_temp_sensor_to_adc ────────────────────────────────────────────

def test_temp_sensor_matches_sensor_vp():
    assigned = {"U301:1", "U301:3", "U101:1", "U101:2"}
    result = _match_temp_sensor_to_adc(_COMPONENTS, _ALL_PINS, assigned)
    assert len(result.matched_pins) == 2
    assert "U301:2" in result.matched_pins
    assert "U101:4" in result.matched_pins
    assert len(result.new_nets) == 1
    assert result.new_nets[0]["net"] == "U301_ADC"
    assert "U301:2" in result.new_nets[0]["pins"]
    assert "U101:4" in result.new_nets[0]["pins"]


def test_temp_sensor_skips_already_assigned():
    assigned = {"U301:1", "U301:2", "U301:3", "U101:1", "U101:2"}
    result = _match_temp_sensor_to_adc(_COMPONENTS, _ALL_PINS, assigned)
    assert len(result.matched_pins) == 0


def test_temp_sensor_no_mcu():
    comps = [
        {"ref_des": "U301", "id_str": "Sensor_Temperature:LM35-LP", "category": "Sensor"},
        {"ref_des": "J1", "id_str": "Connector:Screw_Terminal_01x02", "category": "Connector"},
    ]
    assigned = {"U301:1", "U301:3"}
    result = _match_temp_sensor_to_adc(comps, _ALL_PINS, assigned)
    assert len(result.matched_pins) == 0


def test_temp_sensor_no_adc_pins():
    pins = dict(_LM35_PINS)
    pins.update({
        "U101:1": {"x": 0, "y": 0, "name": "GND", "num": "1", "pin_num": "1",
                   "ref_des": "U101", "etype": "power_in"},
        "U101:2": {"x": 0, "y": 0, "name": "VDD", "num": "2", "pin_num": "2",
                   "ref_des": "U101", "etype": "power_in"},
    })
    assigned = {"U301:1", "U301:3", "U101:1", "U101:2"}
    result = _match_temp_sensor_to_adc(_COMPONENTS, pins, assigned)
    assert len(result.matched_pins) == 0


# ── _match_power_terminal ────────────────────────────────────────────────

def test_power_terminal_connects_vdd_gnd():
    assigned = {"U301:1", "U301:3", "U101:1", "U101:2"}
    existing_pp = [
        {"pin": "U101:1", "net": "GND"},
        {"pin": "U101:2", "net": "VDD"},
    ]
    result = _match_power_terminal(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 2
    assert "J1:1" in result.matched_pins
    assert "J1:2" in result.matched_pins
    assert len(result.new_power_pins) == 2
    assert any(pp["net"] == "VDD" for pp in result.new_power_pins)
    assert any(pp["net"] == "GND" for pp in result.new_power_pins)


def test_power_terminal_no_rails():
    assigned = set()
    existing_pp = []
    result = _match_power_terminal(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 0


def test_power_terminal_only_gnd_rail():
    assigned = {"U101:1"}
    existing_pp = [{"pin": "U101:1", "net": "GND"}]
    result = _match_power_terminal(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 0


# ── _match_decoupling_cap ────────────────────────────────────────────────

def test_decoupling_cap_connects_vdd_gnd():
    assigned = {"U301:1", "U301:3", "U101:1", "U101:2"}
    existing_pp = [
        {"pin": "U101:1", "net": "GND"},
        {"pin": "U101:2", "net": "VDD"},
    ]
    result = _match_decoupling_cap(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 2
    assert "C5:1" in result.matched_pins
    assert "C5:2" in result.matched_pins
    assert len(result.new_power_pins) == 2
    assert any(pp["net"] == "VDD" for pp in result.new_power_pins)
    assert any(pp["net"] == "GND" for pp in result.new_power_pins)


def test_decoupling_cap_skips_assigned():
    assigned = {"U301:1", "U301:3", "U101:1", "U101:2", "C5:1", "C5:2"}
    existing_pp = [
        {"pin": "U101:1", "net": "GND"},
        {"pin": "U101:2", "net": "VDD"},
    ]
    result = _match_decoupling_cap(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 0


def test_decoupling_cap_no_rails():
    assigned = set()
    existing_pp = []
    result = _match_decoupling_cap(_COMPONENTS, _ALL_PINS, existing_pp, assigned)
    assert len(result.matched_pins) == 0


# ── _discover_power_rails ────────────────────────────────────────────────

def test_discover_power_rails_from_nets():
    nets = [
        {"net": "GND", "pins": ["U101:1", "U301:3"]},
        {"net": "VDD", "pins": ["U101:2"]},
    ]
    assigned = {"U101:1", "U101:2", "U301:3"}
    pp = _discover_power_rails(nets, assigned)
    non_gnd = [p for p in pp if p["net"] != "GND"]
    assert len(non_gnd) == 1
    assert non_gnd[0]["net"] == "VDD"
    assert non_gnd[0]["pin"] == "U101:2"


def test_discover_power_rails_skips_unassigned():
    nets = [
        {"net": "VDD", "pins": ["U101:2", "C5:1"]},
    ]
    assigned = {"U101:2"}  # C5:1 not yet assigned
    pp = _discover_power_rails(nets, assigned)
    assert len(pp) == 1
    assert pp[0]["pin"] == "U101:2"
    assert pp[0]["net"] == "VDD"


# ── match_pins integration ───────────────────────────────────────────────

def test_match_pins_full_integration():
    nets = [
        {"net": "GND", "pins": ["U101:1", "U301:3"]},
        {"net": "VDD", "pins": ["U101:2"]},
    ]
    assigned = {"U101:1", "U101:2", "U301:3", "U301:1"}  # +VS assigned as power
    result = match_pins(_COMPONENTS, _ALL_PINS, nets, assigned=assigned)
    assert len(result["matched_pins"]) > 0
    assert "U301:2" in result["matched_pins"]  # VOUT matched
    assert "U101:4" in result["matched_pins"]  # SENSOR_VP matched
    assert "J1:1" in result["matched_pins"]    # terminal power
    assert "J1:2" in result["matched_pins"]    # terminal GND
    assert "C5:1" in result["matched_pins"]    # cap VDD
    assert "C5:2" in result["matched_pins"]    # cap GND

    # Check signal net exists
    signal_nets = [n for n in result["new_nets"]
                   if n["net"] == "U301_ADC"]
    assert len(signal_nets) == 1
    pins = signal_nets[0]["pins"]
    assert "U301:2" in pins
    assert "U101:4" in pins

    # Check power pins created
    assert len(result["new_power_pins"]) >= 4  # term VDD, term GND, cap VDD, cap GND


def test_match_pins_empty_components():
    result = match_pins([], {}, [], assigned=set())
    assert len(result["matched_pins"]) == 0
    assert len(result["new_nets"]) == 0


def test_match_pins_no_assigned():
    nets = []
    result = match_pins(_COMPONENTS, _ALL_PINS, nets)
    assert isinstance(result, dict)
    assert "matched_pins" in result
    assert "new_nets" in result


# ── MatchResult ──────────────────────────────────────────────────────────

def test_match_result_merge():
    a = MatchResult()
    a.matched_pins.add("X:1")
    a.new_nets.append({"net": "N1", "pins": ["X:1"]})

    b = MatchResult()
    b.matched_pins.add("Y:2")
    b.new_nets.append({"net": "N2", "pins": ["Y:2"]})

    a.merge(b)
    assert a.matched_pins == {"X:1", "Y:2"}
    assert len(a.new_nets) == 2


def test_match_result_to_dict():
    r = MatchResult()
    r.matched_pins.add("Z:3")
    d = r.to_dict()
    assert d["matched_pins"] == {"Z:3"}
    assert d["new_nets"] == []
    assert d["new_power_pins"] == []
    assert d["new_netlist"] == []
