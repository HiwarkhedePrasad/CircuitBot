"""
Test Suite for CircuitBot Open-Source EDA Integrations.

Tests:
1. TOKN Converter (Token-Oriented KiCad Notation)
2. SKiDL Netlisting & ERC Engine
3. Live Supplier Sourcing & JLCPCB BOM/CPL Exporter
4. tscircuit React/TSX Exporter & CircuitJSON
5. SPICE Simulation Engine
6. Model Context Protocol (MCP) Dispatcher
"""

import pytest
from agent.tokn_converter import sexpr_to_tokn, tokn_to_net_dict
from agent.skidl_runner import SKiDLNetlistEngine, build_and_validate_skidl_netlist
from agent.sourcing import ComponentSourcingEngine
from agent.tscircuit_export import export_to_tscircuit_jsx, export_to_circuit_json
from agent.spice_sim import verify_circuit_parameters
from server.mcp_server import run_mcp_tool, MCP_TOOL_MANIFEST


def test_tokn_converter():
    sample_sexpr = """
    (symbol "AMS1117-3.3" (property "Reference" "U1") (property "Value" "AMS1117-3.3") (property "Footprint" "SOT-223")
      (pin power_in (number "1") (name "GND"))
      (pin power_out (number "2") (name "VOUT"))
      (pin power_in (number "3") (name "VIN"))
    )
    (net 1 "GND")
    (net 2 "VCC")
    """
    tokn_str = sexpr_to_tokn(sample_sexpr)
    assert "SYM|U1|AMS1117-3.3|AMS1117-3.3|SOT-223" in tokn_str
    assert "P(1:GND:power_in)" in tokn_str
    assert "NET(1:GND)" in tokn_str

    net_dict = tokn_to_net_dict(tokn_str)
    assert len(net_dict["components"]) == 1
    assert net_dict["components"][0]["ref"] == "U1"


def test_skidl_erc_engine():
    engine = SKiDLNetlistEngine()
    engine.add_component("U1", "AMS1117-3.3", {"1": "power_in", "2": "power_out", "3": "power_in"})
    engine.add_component("C1", "10uF", {"1": "passive", "2": "passive"})

    engine.connect_pin("GND", "U1", "1", "power_in")
    engine.connect_pin("GND", "C1", "2", "passive")
    engine.connect_pin("VOUT", "U1", "2", "power_out")
    engine.connect_pin("VOUT", "C1", "1", "passive")
    engine.connect_pin("VIN", "U1", "3", "power_in")

    erc_errors = engine.run_erc()
    # No floating pins or short circuits should be detected
    assert len(erc_errors) == 0


def test_sourcing_engine():
    comps = [
        {"ref": "U1", "value": "AMS1117-3.3", "footprint": "SOT-223"},
        {"ref": "C1", "value": "10uF", "footprint": "0805"}
    ]
    bom_csv = ComponentSourcingEngine.generate_jlcpcb_bom_csv(comps)
    assert "Comment,Designator,Footprint,LCSC Part Number" in bom_csv
    assert "C6186" in bom_csv  # LCSC C-number for AMS1117-3.3
    assert "C15849" in bom_csv # LCSC C-number for 10uF capacitor

    cpl_csv = ComponentSourcingEngine.generate_jlcpcb_cpl_csv(comps)
    assert "Designator,Mid X,Mid Y,Layer,Rotation" in cpl_csv
    assert "U1" in cpl_csv


def test_tscircuit_exporter():
    comps = [{"ref": "R1", "value": "1k", "footprint": "0603"}]
    nets = {"GND": ["R1.2"]}
    jsx = export_to_tscircuit_jsx(comps, nets)
    assert "import { Circuit, Resistor, Capacitor, Chip, Net } from '@tscircuit/builder';" in jsx
    assert "<resistor name='R1' value='1k' footprint='0603' />" in jsx

    circuit_json = export_to_circuit_json(comps, nets)
    assert "source_component" in circuit_json
    assert "R1" in circuit_json


def test_spice_simulation():
    is_passed, logs = verify_circuit_parameters([], {})
    assert is_passed is True
    assert any("[SPICE]" in line for line in logs)


def test_mcp_dispatcher():
    assert len(MCP_TOOL_MANIFEST) >= 4
    res = run_mcp_tool("circuitbot_export_bom", {})
    assert res["status"] == "success"
    assert "C6186" in res["bom_csv"]


def test_user_specified_component_selection():
    from agent.nodes.select import _expected_buckets, _filter_candidates_by_expected_type
    sub_ds18 = {
        "subsystem": "User-specified (DS18B20)",
        "function": "Temperature sensor requested by user",
        "results": [{"id_str": "Sensor_Temperature:DS18B20", "text": "1-Wire Digital Thermometer TO-92"}]
    }
    buckets = _expected_buckets(sub_ds18)
    assert "sensor" in buckets or "user-specified (ds18b20)" in buckets
    
    candidates = sub_ds18["results"]
    filtered = _filter_candidates_by_expected_type(sub_ds18, candidates)
    assert len(filtered) == 1
    assert filtered[0]["id_str"] == "Sensor_Temperature:DS18B20"

