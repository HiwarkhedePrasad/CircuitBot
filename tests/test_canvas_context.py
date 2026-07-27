"""Tests for the canvas context builder.

Verifies that build_canvas_context produces accurate, compact summaries
of the schematic state for LLM consumption.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.sync.live_schematic import (
    build_canvas_context,
    build_canvas_context_for_modify,
    build_canvas_context_for_query,
)
from server.state import DesignSession, session_manager


def _make_session(session_id="test_context") -> DesignSession:
    ds = DesignSession(session_id)
    ds.clear_design()
    return ds


def _sync_components(session_id, components, wire_paths=None, net_labels=None):
    """Helper to sync a snapshot and return the design."""
    ds = session_manager.get_or_create(session_id)
    from server.routes import _normalize_schematic_snapshot, _rebuild_derived_state
    snapshot = {
        'components': components,
        'wire_paths': wire_paths or [],
        'net_labels': net_labels or [],
    }
    normalized = _normalize_schematic_snapshot(snapshot)
    ds.replace_design(normalized)
    _rebuild_derived_state(ds)
    return ds


# ── Basic context tests ────────────────────────────────────────────────

def test_empty_canvas():
    ds = _make_session()
    ctx = build_canvas_context(ds)
    assert "No components on canvas" in ctx
    assert "Canvas revision: 0" in ctx


def test_components_listed():
    ds = _sync_components("test_ctx_1", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10.0, 'y': 20.0, 'value': '10k'},
        {'ref_des': 'C1', 'id_str': 'Device:C', 'x': 30.0, 'y': 40.0, 'value': '100nF'},
    ])
    ctx = build_canvas_context(ds)
    assert "R1: Device:R, 10k" in ctx
    assert "C1: Device:C, 100nF" in ctx
    assert "Components (2)" in ctx


def test_component_pins():
    ds = _sync_components("test_ctx_2", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ])
    ctx = build_canvas_context(ds)
    assert "Component pins:" in ctx
    assert "R1: 1, 2" in ctx


def test_nets_shown():
    ds = _sync_components("test_ctx_3", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
        {'ref_des': 'R2', 'id_str': 'Device:R', 'x': 30, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ], wire_paths=[
        {'wire_id': 'W1', 'source': 'R1:2', 'target': 'R2:1', 'net': 'N$001'},
    ])
    ctx = build_canvas_context(ds)
    assert "Nets (" in ctx
    assert "N$001" in ctx
    assert "R1:2" in ctx or "R2:1" in ctx


def test_net_labels_shown():
    ds = _sync_components("test_ctx_4", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}]},
    ], net_labels=[
        {'id': 'nl_1', 'net': 'VCC', 'x': 10, 'y': 20, 'orientation': 0, 'pin': 'R1:1'},
    ])
    ctx = build_canvas_context(ds)
    assert "Net labels (" in ctx
    assert "VCC" in ctx
    assert "on R1:1" in ctx


def test_unconnected_pins():
    ds = _sync_components("test_ctx_5", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ])
    ctx = build_canvas_context(ds)
    assert "Unconnected pins (" in ctx
    assert "R1:1" in ctx
    assert "R1:2" in ctx


def test_connected_pins_not_in_unconnected():
    ds = _sync_components("test_ctx_6", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ], wire_paths=[
        {'wire_id': 'W1', 'source': 'R1:1', 'target': 'R1:2', 'net': 'GND'},
    ])
    ctx = build_canvas_context(ds)
    # R1:1 and R1:2 are connected via wire, so they should NOT appear in unconnected
    assert "Unconnected pins" not in ctx or "R1:1" not in ctx.split("Unconnected")[1] if "Unconnected" in ctx else True


def test_net_label_makes_pin_connected():
    ds = _sync_components("test_ctx_7", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ], net_labels=[
        {'id': 'nl_1', 'net': 'VCC', 'x': 10, 'y': 20, 'orientation': 0, 'pin': 'R1:1'},
    ])
    ctx = build_canvas_context(ds)
    # R1:1 is connected via label, only R1:2 should be unconnected
    assert "R1:2" in ctx
    # R1:1 should not appear in unconnected section
    unconnected_section = ctx.split("Unconnected pins:")[1] if "Unconnected pins:" in ctx else ""
    assert "R1:1" not in unconnected_section


def test_revision_in_context():
    ds = _make_session("test_ctx_8")
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    ctx = build_canvas_context(ds)
    assert "Canvas revision: 1" in ctx


# ── Variant builders ───────────────────────────────────────────────────

def test_build_for_modify_includes_prompt():
    ds = _sync_components("test_ctx_9", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'value': '10k'},
    ])
    ctx = build_canvas_context_for_modify(ds, "Change R1 to 4.7k")
    assert "Change R1 to 4.7k" in ctx
    assert "R1: Device:R, 10k" in ctx
    assert "User modification request:" in ctx


def test_build_for_query_includes_question():
    ds = _sync_components("test_ctx_10", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20},
    ])
    ctx = build_canvas_context_for_query(ds, "What components are on the canvas?")
    assert "What components are on the canvas?" in ctx
    assert "R1: Device:R" in ctx
    assert "Question:" in ctx


# ── Edge cases ─────────────────────────────────────────────────────────

def test_component_without_pins():
    ds = _sync_components("test_ctx_11", [
        {'ref_des': 'U1', 'id_str': 'MCU_ST_STM32F103C8', 'x': 50, 'y': 50, 'pins': []},
    ])
    ctx = build_canvas_context(ds)
    assert "U1: MCU_ST_STM32F103C8" in ctx
    # Empty pins should not cause errors
    assert "U1:" in ctx


def test_wire_without_net():
    ds = _sync_components("test_ctx_12", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}]},
        {'ref_des': 'R2', 'id_str': 'Device:R', 'x': 30, 'y': 20, 'pins': [{'number': '1'}]},
    ], wire_paths=[
        {'wire_id': 'W1', 'source': 'R1:1', 'target': 'R2:1', 'net': ''},
    ])
    ctx = build_canvas_context(ds)
    assert "Wire connections:" in ctx
    assert "R1:1 -- R2:1" in ctx


def test_component_with_footprint():
    ds = _sync_components("test_ctx_13", [
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'value': '10k', 'footprint': 'R_0805'},
    ])
    ctx = build_canvas_context(ds)
    assert "R_0805" in ctx
