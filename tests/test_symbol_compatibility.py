"""Tests for the Symbol Compatibility Gate node.

Tests the core deterministic checks:
1. Pin count vs expected (reject 400-pin connector for 4-pin header)
2. Grid-array connector detection (Samtec A1/B1/C1/D1 pattern)
3. Library prefix matching (Sensor_* for sensor subsystems)
4. Edge cases: missing pads, single-pin connectors, etc.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.nodes.symbol_compatibility import (
    _estimate_expected_pins,
    _actual_pin_count,
    _is_grid_array_connector,
    _library_category,
    _check_component,
)


# ── _estimate_expected_pins ──────────────────────────────────────────────────

def test_estimate_from_function():
    sub = {"function": "4-pin UART programming header for ESP32"}
    assert _estimate_expected_pins(sub, "") == 4


def test_estimate_from_multi_digit():
    sub = {"function": "12-pin JTAG connector"}
    assert _estimate_expected_pins(sub, "") == 12


def test_estimate_from_example_component():
    sub = {"example_components": ["Conn_01x08"]}
    assert _estimate_expected_pins(sub, "") == 8


def test_estimate_from_example_multi():
    sub = {"example_components": ["Conn_01x04", "AVR-ISP-6"]}
    assert _estimate_expected_pins(sub, "") == 4


def test_estimate_connector_default():
    sub = {"subsystem": "Programming Header"}
    assert _estimate_expected_pins(sub, "") == 4


def test_estimate_sensor_default():
    sub = {"subsystem": "Temperature Sensor"}
    assert _estimate_expected_pins(sub, "") == 6


def test_estimate_mcu_default():
    sub = {"subsystem": "Microcontroller"}
    assert _estimate_expected_pins(sub, "") == 48


def test_estimate_empty():
    assert _estimate_expected_pins({}, "") == 8


# ── _actual_pin_count ────────────────────────────────────────────────────────

def test_count_from_pads():
    comp = {"pads": [{"number": "1"}, {"number": "2"}, {"number": "3"}]}
    count, src = _actual_pin_count(comp)
    assert count == 3
    assert src == "pads"


def test_count_from_pins():
    comp = {"pins": [{"num": "1"}, {"num": "2"}]}
    count, src = _actual_pin_count(comp)
    assert count == 2
    assert src == "pins"


def test_count_empty():
    comp = {}
    count, src = _actual_pin_count(comp)
    assert count == 0
    assert src == "none"


# ── _is_grid_array_connector ─────────────────────────────────────────────────

def _make_grid_pins(rows: int, cols: int, prefix: str = "") -> list[dict]:
    """Generate a grid-array pin list, e.g. A1, B1, C1, A2, B2, C2, ..."""
    pads = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:rows]
    for col in range(1, cols + 1):
        for letter in letters:
            pads.append({"number": f"{prefix}{letter}{col}"})
    return pads


def test_grid_array_detected_4row_4col():
    pads = _make_grid_pins(4, 4)
    assert _is_grid_array_connector(pads)


def test_grid_array_detected_2row_25col():
    pads = _make_grid_pins(2, 25)
    assert _is_grid_array_connector(pads)


def test_simple_header_not_detected():
    pads = [{"number": str(i)} for i in range(1, 5)]
    assert not _is_grid_array_connector(pads)


def test_small_connector_not_detected():
    pads = _make_grid_pins(4, 3)  # 12 pins < 16 threshold
    assert not _is_grid_array_connector(pads)


def test_single_row_high_count():
    pads = _make_grid_pins(1, 64)  # single row, 64 pins
    assert not _is_grid_array_connector(pads)


def test_samtec_like_connector():
    """Samtec 400-pin: 10 rows × 40 cols."""
    pads = _make_grid_pins(10, 40)
    assert _is_grid_array_connector(pads)


# ── _library_category ────────────────────────────────────────────────────────

def test_library_category():
    assert _library_category({"id_str": "Connector:AVR-ISP-6"}) == "Connector"
    assert _library_category({"id_str": "MCU_Espressif:ESP32-C3"}) == "MCU_Espressif"
    assert _library_category({"id_str": "Device:R_Small"}) == "Device"
    assert _library_category({"id_str": ""}) == ""
    assert _library_category({"id_str": "NoColon"}) == ""


# ── _check_component ─────────────────────────────────────────────────────────

def _make_comp(ref: str, id_str: str, pads: list[dict], subsystem: str = "") -> dict:
    return {
        "ref_des": ref,
        "id_str": id_str,
        "pads": pads,
        "subsystem": subsystem,
    }


def test_400pin_connector_rejected_for_4pin():
    """A Samtec 400-pin connector selected for a 4-pin programming header."""
    comp = _make_comp("J2", "Connector:Samtec_ASP-134486-01",
                      _make_grid_pins(10, 40))
    sub = {"subsystem": "Programming Header", "function": "4-pin programming header"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) >= 1
    assert any("oversize" in e.lower() or "400" in e for e in errors)


def test_4pin_header_passes():
    """A 4-pin header for a 4-pin programming header should pass."""
    comp = _make_comp("J1", "Connector:Conn_01x04",
                      [{"number": str(i)} for i in range(1, 5)])
    sub = {"subsystem": "Programming Header", "function": "4-pin programming header"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) == 0


def test_8pin_header_for_4pin_warns():
    """An 8-pin header for a 4-pin subsystem — ratio=2, under threshold."""
    comp = _make_comp("J1", "Connector:Conn_01x08",
                      [{"number": str(i)} for i in range(1, 9)])
    sub = {"subsystem": "Programming Header", "function": "4-pin programming header"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) == 0  # 8/4 = 2x, under 5x threshold


def test_sensor_category_mismatch():
    """A Connector part selected for a Sensor subsystem should fail."""
    comp = _make_comp("U1", "Connector:AVR-ISP-6",
                      [{"number": str(i)} for i in range(1, 7)],
                      subsystem="Temperature Sensor")
    sub = {"subsystem": "Temperature Sensor", "function": "I2C temperature sensing"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) >= 1
    assert any("category mismatch" in e.lower() for e in errors)


def test_sensor_category_pass():
    """A Sensor_* part for a Sensor subsystem should pass."""
    comp = _make_comp("U1", "Sensor_Temperature:TMP117",
                      [{"number": str(i)} for i in range(1, 7)],
                      subsystem="Temperature Sensor")
    sub = {"subsystem": "Temperature Sensor", "function": "I2C temperature sensing"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) == 0


def test_mcu_category_mismatch():
    """A Connector part where MCU expected should fail."""
    comp = _make_comp("U1", "Connector:AVR-ISP-6",
                      [{"number": str(i)} for i in range(1, 7)],
                      subsystem="Microcontroller")
    sub = {"subsystem": "Microcontroller", "function": "ESP32-C3 MCU"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) >= 1
    assert any("category mismatch" in e.lower() for e in errors)


def test_missing_pads_skips_pin_check():
    """When pads/pins data is missing, pin count checks are skipped."""
    comp = _make_comp("J1", "Connector:Conn_01x04", [])
    sub = {"subsystem": "Programming Header", "function": "4-pin programming header"}
    errors = _check_component(comp, sub, "", {})
    assert len(errors) == 0


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("test_estimate_from_function", test_estimate_from_function),
        ("test_estimate_from_multi_digit", test_estimate_from_multi_digit),
        ("test_estimate_from_example_component", test_estimate_from_example_component),
        ("test_estimate_from_example_multi", test_estimate_from_example_multi),
        ("test_estimate_connector_default", test_estimate_connector_default),
        ("test_estimate_sensor_default", test_estimate_sensor_default),
        ("test_estimate_mcu_default", test_estimate_mcu_default),
        ("test_estimate_empty", test_estimate_empty),
        ("test_count_from_pads", test_count_from_pads),
        ("test_count_from_pins", test_count_from_pins),
        ("test_count_empty", test_count_empty),
        ("test_grid_array_detected_4row_4col", test_grid_array_detected_4row_4col),
        ("test_grid_array_detected_2row_25col", test_grid_array_detected_2row_25col),
        ("test_simple_header_not_detected", test_simple_header_not_detected),
        ("test_small_connector_not_detected", test_small_connector_not_detected),
        ("test_single_row_high_count", test_single_row_high_count),
        ("test_samtec_like_connector", test_samtec_like_connector),
        ("test_library_category", test_library_category),
        ("test_400pin_connector_rejected_for_4pin", test_400pin_connector_rejected_for_4pin),
        ("test_4pin_header_passes", test_4pin_header_passes),
        ("test_8pin_header_for_4pin_warns", test_8pin_header_for_4pin_warns),
        ("test_sensor_category_mismatch", test_sensor_category_mismatch),
        ("test_sensor_category_pass", test_sensor_category_pass),
        ("test_mcu_category_mismatch", test_mcu_category_mismatch),
        ("test_missing_pads_skips_pin_check", test_missing_pads_skips_pin_check),
    ]
    fail_count = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            fail_count += 1
    print(f"\n{'ALL PASS' if fail_count == 0 else f'{fail_count} FAILURE(S)'}")
    sys.exit(1 if fail_count else 0)
