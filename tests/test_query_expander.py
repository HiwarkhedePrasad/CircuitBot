import pytest
from agent.knowledge.query_expander import expand_subsystem_query
from kicad_rag.taxonomy import get_canonical_symbols
from agent.reranker import _check_voltage_and_connector_constraints


def test_expand_subsystem_query_power_regulation():
    sub = {"subsystem": "Power Regulation", "function": "Regulates 5V down to 3.3V"}
    queries = expand_subsystem_query(sub)
    assert any("3.3V" in q or "AMS1117-3.3" in q for q in queries)


def test_expand_subsystem_query_power_input():
    sub = {"subsystem": "Power Input", "function": "Provides 5V DC from a USB-C connector"}
    queries = expand_subsystem_query(sub)
    assert any("USB_C_Receptacle_USB2.0_16P" in q for q in queries)


def test_get_canonical_symbols_regulator():
    syms = get_canonical_symbols("3.3V LDO regulator")
    assert "Regulator_Linear:AMS1117-3.3" in syms
    assert "Regulator_Linear:AP2112K-3.3" in syms


def test_reranker_pre_filter_zeros_bad_regulator():
    candidates = [
        {"id_str": "Regulator_Linear:TPS7A0530PDBV", "score": 8},
        {"id_str": "Regulator_Linear:AMS1117-3.3", "score": 9},
    ]
    filtered = _check_voltage_and_connector_constraints(
        candidates, "Power Regulation", "Regulates 5V down to 3.3V"
    )
    tps = next(c for c in filtered if "TPS7A0530" in c["id_str"])
    assert tps["score"] == 0
    ams = next(c for c in filtered if "AMS1117-3.3" in c["id_str"])
    assert ams["score"] == 9
