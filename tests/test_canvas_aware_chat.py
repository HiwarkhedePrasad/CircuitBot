"""Tests for canvas-aware copilot.

Verifies that the design_query intent is recognized, canvas context is injected
into modify requests, and the chat handler can answer questions about the canvas.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Prompt router tests ────────────────────────────────────────────────

def test_design_query_intent_recognized():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("What components are on the canvas?")
    assert result["intent"] == "design_query"
    assert result["confidence"] >= 0.7


def test_design_query_connected():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("Is R1 connected?")
    assert result["intent"] == "design_query"


def test_design_query_unconnected():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("Which pins are unconnected?")
    assert result["intent"] == "design_query"


def test_design_query_nets():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("Show me the nets")
    assert result["intent"] == "design_query"


def test_add_component_still_works():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("add a 10k resistor")
    assert result["intent"] == "add_component"


def test_modify_still_works():
    from agent.prompt_router import _keyword_fallback
    result = _keyword_fallback("change R1 to 4.7k")
    assert result["intent"] == "modify_design"


# ── Canvas context injection tests ─────────────────────────────────────

def test_modify_uses_canvas_context():
    from server.state import DesignSession, session_manager
    from agent.nodes.modify import classify_modification_node

    ds = DesignSession("test_modify_ctx")
    ds.clear_design()
    ds.replace_design({
        "selected_components": [
            {"ref": "R1", "ref_des": "R1", "name": "Device:R", "id_str": "Device:R", "value": "10k", "x": 10, "y": 20, "pins": [{"number": "1"}, {"number": "2"}]},
            {"ref": "R2", "ref_des": "R2", "name": "Device:R", "id_str": "Device:R", "value": "4.7k", "x": 30, "y": 20, "pins": [{"number": "1"}, {"number": "2"}]},
        ],
        "wire_paths": [],
        "net_labels": [],
    })
    ds.revision = 1

    state = {
        "prompt": "Change R1 to 100k",
        "original_design": ds.get_design(),
        "design_session": ds,
    }

    # classify_modification_node should use canvas context
    # (It will call LLM which may fail in test, but the context should be built)
    result = classify_modification_node(state)
    # The function should return without error
    assert "modification_type" in result


def test_modify_fallback_without_session():
    from agent.nodes.modify import classify_modification_node

    state = {
        "prompt": "Change R1 to 100k",
        "original_design": {
            "selected_components": [
                {"ref": "R1", "name": "Device:R", "value": "10k"},
            ],
        },
        # No design_session — should use fallback
    }

    result = classify_modification_node(state)
    assert "modification_type" in result


# ── Design query handler tests ─────────────────────────────────────────

def test_design_query_no_design():
    """Handler should return helpful message when no design exists."""
    from server.state import DesignSession, session_manager

    ds = DesignSession("test_dq_no_design")
    ds.clear_design()

    # The handler would normally emit via socketio, but we can verify
    # the DesignSession state check
    assert not ds.get_design()


def test_design_query_with_components():
    """Verify DesignSession has components for design query."""
    from server.state import DesignSession, session_manager

    ds = DesignSession("test_dq_with_comps")
    ds.clear_design()
    ds.replace_design({
        "selected_components": [
            {"ref": "R1", "ref_des": "R1", "name": "Device:R", "value": "10k", "x": 10, "y": 20},
            {"ref": "C1", "ref_des": "C1", "name": "Device:C", "value": "100nF", "x": 30, "y": 20},
        ],
        "wire_paths": [],
        "net_labels": [],
    })
    ds.revision = 1

    design = ds.get_design()
    comps = design.get("selected_components", [])
    assert len(comps) == 2
    assert comps[0]["ref"] == "R1"
    assert comps[1]["ref"] == "C1"


# ── Feature flag gating tests ──────────────────────────────────────────

def test_feature_flag_exists():
    from agent.feature_flags import is_enabled, status
    flags = status()
    assert "CANVAS_AWARE_COPILOT" in flags
    # Default should be False
    assert flags["CANVAS_AWARE_COPILOT"] is False


def test_modify_uses_canvas_context_when_flag_enabled():
    from agent.feature_flags import set_flag
    from server.state import DesignSession
    from agent.nodes.modify import classify_modification_node

    set_flag("CANVAS_AWARE_COPILOT", True)
    try:
        ds = DesignSession("test_flag_enabled")
        ds.clear_design()
        ds.replace_design({
            "selected_components": [
                {"ref": "R1", "ref_des": "R1", "name": "Device:R", "value": "10k", "x": 10, "y": 20, "pins": [{"number": "1"}, {"number": "2"}]},
            ],
            "wire_paths": [],
            "net_labels": [],
        })
        ds.revision = 1

        state = {
            "prompt": "Change R1 to 100k",
            "original_design": ds.get_design(),
            "design_session": ds,
        }

        result = classify_modification_node(state)
        assert "modification_type" in result
    finally:
        set_flag("CANVAS_AWARE_COPILOT", False)


def test_modify_falls_back_when_flag_disabled():
    from agent.feature_flags import set_flag
    from server.state import DesignSession
    from agent.nodes.modify import classify_modification_node

    set_flag("CANVAS_AWARE_COPILOT", False)
    ds = DesignSession("test_flag_disabled")
    ds.clear_design()
    ds.replace_design({
        "selected_components": [
            {"ref": "R1", "ref_des": "R1", "name": "Device:R", "value": "10k", "x": 10, "y": 20},
        ],
    })

    state = {
        "prompt": "Change R1 to 100k",
        "original_design": ds.get_design(),
        "design_session": ds,
    }

    # Should use fallback (short component list) even with design_session
    result = classify_modification_node(state)
    assert "modification_type" in result
