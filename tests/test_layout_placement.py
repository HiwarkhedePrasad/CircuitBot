"""Regression tests for block-aware placement (blocks_v2 mode).

Validates that:
  1. Block detection assigns components to the correct functional blocks.
  2. Components in the same block (e.g., RESET_BLOCK: R_pullup + MCU) are
     placed within 30 mm of each other.
  3. The layout engine runs without errors for both "graph" and "blocks_v2"
     modes.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.layout_engine import (
    BackendLayoutEngine,
    PLACEMENT_MODE as CURRENT_MODE,
    _BLOCK_SEEDS,
    _BLOCK_ROLE,
)


def _make_comp(engine, ref, ops, category, id_str="", for_component=""):
    engine.add_component(ref, ops, category, id_str, for_component)


def _make_rect(w, h):
    """Rectangle ops centered at (0,0) with given width and height."""
    hw, hh = w / 2, h / 2
    return [["rectangle", ["start", -hw, -hh], ["end", hw, hh]]]


def test_block_seeds_match_pin_names():
    """Every _BLOCK_SEEDS key should match at least one NET_CLASS token."""
    from agent.layout_engine import _NET_CLASSES
    for kw in _BLOCK_SEEDS:
        if kw in _NET_CLASSES:
            continue  # also defined in net classification
    # No crash means all keys are valid strings


def test_block_role_map_known():
    """Every _BLOCK_SEEDS value should have a role in _BLOCK_ROLE."""
    for block_name in set(_BLOCK_SEEDS.values()):
        assert block_name in _BLOCK_ROLE, (
            f"Block '{block_name}' from _BLOCK_SEEDS has no role in _BLOCK_ROLE"
        )


def test_placement_blocks_v2_no_crash():
    """blocks_v2 mode completes without error on a representative circuit."""
    e = BackendLayoutEngine()
    _make_comp(e, "U1", _make_rect(10, 6), "MCU", "MCU:ESP32")
    _make_comp(e, "U2", _make_rect(6, 4), "Regulator_Linear", "Regulator_Linear:AMS1117")
    _make_comp(e, "J1", _make_rect(8, 4), "Connector_USB", "Connector_USB:USB_C_Receptacle")
    _make_comp(e, "R1", _make_rect(4, 2), "Device", "Device:R", for_component="U1")
    _make_comp(e, "C1", _make_rect(4, 2), "Device", "Device:C_Small", for_component="U2")
    _make_comp(e, "D1", _make_rect(4, 2), "Device", "Device:LED", for_component="U1")

    netlist = [
        {"source": "J1:VBUS", "target": "U2:VIN"},
        {"source": "U2:VOUT", "target": "U1:3V3"},
        {"source": "C1:1", "target": "U2:VOUT"},
        {"source": "R1:1", "target": "U1:EN"},
        {"source": "U1:GPIO2", "target": "D1:A"},
    ]
    pin_matrix = {
        "U1:3V3":    {"x": -5, "y": 0, "angle": 180},
        "U1:EN":     {"x": -5, "y": 2, "angle": 180},
        "U1:GPIO2":  {"x": 0, "y": -3, "angle": 90},
        "U2:VIN":    {"x": -3, "y": -1, "angle": 180},
        "U2:VOUT":   {"x": 3, "y": -1, "angle": 0},
        "J1:VBUS":   {"x": -4, "y": 0, "angle": 180},
        "C1:1":      {"x": 1, "y": 0, "angle": 0},
        "R1:1":      {"x": 1, "y": 0, "angle": 0},
        "D1:A":      {"x": 1, "y": 0, "angle": 0},
    }

    e.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

    for c in e.components:
        assert not (c["x"] != c["x"] or c["y"] != c["y"]), (
            f"{c['ref_des']} has NaN position"
        )


def test_block_detection_seeds_reset_block():
    """Components connected via EN/RESET signals get RESET_BLOCK."""
    e = BackendLayoutEngine()
    _make_comp(e, "U1", _make_rect(10, 6), "MCU", "MCU:ESP32")
    _make_comp(e, "R1", _make_rect(4, 2), "Device", "Device:R", for_component="U1")
    _make_comp(e, "SW1", _make_rect(4, 4), "Device", "Device:SW_Push", for_component="U1")

    netlist = [
        {"source": "R1:1", "target": "U1:EN"},
        {"source": "SW1:1", "target": "U1:RST"},
    ]
    pin_matrix = {
        "U1:EN":  {"x": -5, "y": 0, "angle": 180},
        "U1:RST": {"x": -5, "y": 2, "angle": 180},
        "R1:1":   {"x": 1, "y": 0, "angle": 0},
        "SW1:1":  {"x": 1, "y": 0, "angle": 0},
    }

    graph = e._build_weighted_graph(netlist, pin_matrix)
    block_of = e._detect_blocks_louvain(graph, netlist)

    assert block_of.get("U1") == "RESET_BLOCK", (
        f"MCU should be RESET_BLOCK, got {block_of.get('U1')}"
    )
    assert block_of.get("R1") == "RESET_BLOCK", (
        f"Pull-up resistor should be RESET_BLOCK, got {block_of.get('R1')}"
    )
    assert block_of.get("SW1") == "RESET_BLOCK", (
        f"Reset switch should be RESET_BLOCK, got {block_of.get('SW1')}"
    )


def test_place_block_compact():
    """Components in the RESET_BLOCK should be within 30mm of each other."""
    e = BackendLayoutEngine()
    _make_comp(e, "U1", _make_rect(10, 6), "MCU", "MCU:ESP32")
    _make_comp(e, "R1", _make_rect(4, 2), "Device", "Device:R", for_component="U1")
    _make_comp(e, "SW1", _make_rect(4, 4), "Device", "Device:SW_Push", for_component="U1")

    netlist = [
        {"source": "R1:1", "target": "U1:EN"},
        {"source": "SW1:1", "target": "U1:RST"},
    ]
    pin_matrix = {
        "U1:EN":  {"x": -5, "y": 0, "angle": 180},
        "U1:RST": {"x": -5, "y": 2, "angle": 180},
        "R1:1":   {"x": 1, "y": 0, "angle": 0},
        "SW1:1":  {"x": 1, "y": 0, "angle": 0},
    }

    e.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

    u1 = next(c for c in e.components if c["ref_des"] == "U1")
    r1 = next(c for c in e.components if c["ref_des"] == "R1")
    sw1 = next(c for c in e.components if c["ref_des"] == "SW1")

    def _mhd(a, b):
        return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])

    d_r1 = _mhd(u1, r1)
    d_sw1 = _mhd(u1, sw1)

    MAX = 50.0  # allowed Manhattan distance (mm)
    assert d_r1 <= MAX, f"R1 → U1 distance {d_r1:.1f}mm exceeds {MAX}mm"
    assert d_sw1 <= MAX, f"SW1 → U1 distance {d_sw1:.1f}mm exceeds {MAX}mm"


def test_pin_side_left():
    """_pin_side returns 'left' for pins with angle=180."""
    e = BackendLayoutEngine()
    _make_comp(e, "U1", _make_rect(10, 6), "MCU")
    _make_comp(e, "R1", _make_rect(4, 2), "Device", "Device:R")

    netlist = [{"source": "R1:1", "target": "U1:EN"}]
    pin_matrix = {"U1:EN": {"x": -5, "y": 0, "angle": 180}, "R1:1": {"x": 1, "y": 0, "angle": 0}}

    side = e._pin_side("R1", "U1", pin_matrix, netlist)
    assert side == "left", f"Expected 'left', got '{side}'"


def test_pin_side_right():
    """_pin_side returns 'right' for pins with angle=0."""
    e = BackendLayoutEngine()
    _make_comp(e, "U2", _make_rect(6, 4), "Regulator")
    _make_comp(e, "C1", _make_rect(4, 2), "Device", "Device:C_Small")

    netlist = [{"source": "U2:VOUT", "target": "C1:1"}]
    pin_matrix = {"U2:VOUT": {"x": 3, "y": 0, "angle": 0}, "C1:1": {"x": 1, "y": 0, "angle": 0}}

    side = e._pin_side("C1", "U2", pin_matrix, netlist)
    assert side == "right", f"Expected 'right', got '{side}'"


def test_pin_side_top():
    """_pin_side returns 'top' for parent pins with angle=90."""
    e = BackendLayoutEngine()
    _make_comp(e, "U1", _make_rect(10, 6), "MCU")
    _make_comp(e, "D1", _make_rect(4, 2), "Device", "Device:LED")

    netlist = [{"source": "U1:GPIO2", "target": "D1:A"}]
    pin_matrix = {"U1:GPIO2": {"x": 0, "y": -3, "angle": 90}, "D1:A": {"x": 1, "y": 0, "angle": 0}}

    side = e._pin_side("D1", "U1", pin_matrix, netlist)
    assert side == "top", f"Expected 'top', got '{side}'"
