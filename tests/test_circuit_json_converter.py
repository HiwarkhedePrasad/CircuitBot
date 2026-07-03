"""Tests for BoardModel → Circuit JSON converter."""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pcb_design.circuit_json_converter import board_model_to_circuit_json
from pcb_design.board_model import BoardModel, BoardComponent, PadDef, BoardTrace, BoardVia


def test_empty_board():
    m = BoardModel()
    result = board_model_to_circuit_json(m)
    assert len(result) >= 1
    boards = [e for e in result if e["type"] == "pcb_board"]
    assert len(boards) == 1
    assert len(boards[0]["outline"]) == 5


def test_single_component():
    m = BoardModel()
    m.components.append(BoardComponent(
        ref="R1", footprint="Resistor_SMD:R_0805",
        x=10, y=20, layer="F.Cu",
        pads=[
            PadDef(number="1", x=-1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
            PadDef(number="2", x=1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
        ]
    ))
    result = board_model_to_circuit_json(m)

    comps = [e for e in result if e["type"] == "pcb_component"]
    assert len(comps) == 1
    assert comps[0]["center"]["x"] == 10
    assert comps[0]["center"]["y"] == 20
    assert comps[0]["layer"] == "top"

    ports = [e for e in result if e["type"] == "pcb_port"]
    assert len(ports) == 2


def test_trace_conversion():
    m = BoardModel()
    m.traces.append(BoardTrace(net="GND", layer="F.Cu", width=0.254, path=[(0, 0), (10, 10)]))
    result = board_model_to_circuit_json(m)

    traces = [e for e in result if e["type"] == "pcb_trace"]
    assert len(traces) == 1
    assert len(traces[0]["route"]) == 2
    assert traces[0]["route"][0]["x"] == 0
    assert traces[0]["route"][0]["layer"] == "top"


def test_trace_with_via():
    m = BoardModel()
    m.traces.append(BoardTrace(net="GND", layer="F.Cu", width=0.254, path=[(0, 0), (10, 10)], via=(5, 5)))
    result = board_model_to_circuit_json(m)

    traces = [e for e in result if e["type"] == "pcb_trace"]
    route = traces[0]["route"]
    via_pts = [p for p in route if p.get("route_type") == "via"]
    assert len(via_pts) == 1
    assert via_pts[0]["x"] == 5


def test_via_conversion():
    m = BoardModel()
    m.vias.append(BoardVia(x=5, y=5, drill=0.3, diameter=0.6))
    result = board_model_to_circuit_json(m)

    vias = [e for e in result if e["type"] == "pcb_via"]
    assert len(vias) == 1
    assert vias[0]["x"] == 5
    assert vias[0]["outer_diameter"] == 0.6
    assert vias[0]["hole_diameter"] == 0.3


def test_net_connectivity():
    m = BoardModel(nets=[{"name": "GND", "pins": ["R1:1", "R1:2"]}])
    m.components.append(BoardComponent(
        ref="R1", footprint="Resistor_SMD:R_0805",
        x=0, y=0, layer="F.Cu",
        pads=[
            PadDef(number="1", x=-1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
            PadDef(number="2", x=1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
        ]
    ))
    result = board_model_to_circuit_json(m)

    nets = [e for e in result if e["type"] == "source_net"]
    assert len(nets) == 1
    assert nets[0]["name"] == "GND"

    traces = [e for e in result if e["type"] == "source_trace"]
    assert len(traces) == 1
    assert len(traces[0]["connected_source_port_ids"]) == 2
    assert nets[0]["source_net_id"] in traces[0]["connected_source_net_ids"]


def test_layer_mapping():
    m = BoardModel()
    m.components.append(BoardComponent(
        ref="U1", footprint="SOIC-8", x=0, y=0, layer="B.Cu",
        pads=[PadDef(number="1", x=0, y=-2, width=1, height=0.5, layers=["B.Cu"])]
    ))
    result = board_model_to_circuit_json(m)

    comps = [e for e in result if e["type"] == "pcb_component"]
    assert comps[0]["layer"] == "bottom"

    ports = [e for e in result if e["type"] == "pcb_port"]
    assert ports[0]["layers"] == ["bottom"]


if __name__ == "__main__":
    test_empty_board()
    print("PASS: test_empty_board")
    test_single_component()
    print("PASS: test_single_component")
    test_trace_conversion()
    print("PASS: test_trace_conversion")
    test_trace_with_via()
    print("PASS: test_trace_with_via")
    test_via_conversion()
    print("PASS: test_via_conversion")
    test_net_connectivity()
    print("PASS: test_net_connectivity")
    test_layer_mapping()
    print("PASS: test_layer_mapping")
    print("ALL PASS")
