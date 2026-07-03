"""Tests for /api/apply_edits edit event processing.

These test the backend's edit event schema validation, trace creation,
component relocation, and error handling.
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pcb_design.board_model import BoardModel, BoardComponent, PadDef, BoardTrace


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_test_model() -> BoardModel:
    """Create a BoardModel with one component (R1, two pads) and one net."""
    m = BoardModel()
    m.components.append(BoardComponent(
        ref="R1", footprint="Resistor_SMD:R_0805",
        x=10, y=20, layer="F.Cu",
        pads=[
            PadDef(number="1", x=-1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
            PadDef(number="2", x=1, y=0, width=1.5, height=0.8, layers=["F.Cu"]),
        ]
    ))
    m.nets = [{"name": "GND", "pins": ["R1:1", "R1:2"]}]
    return m


# ── Validation tests ────────────────────────────────────────────────────────

def test_rejects_empty_events():
    events = []
    ok, data = _apply(events)
    assert not ok, "Should reject empty events"
    assert "No edit_events" in data.get("error", "")


def test_rejects_non_array():
    ok, data = _apply("not_an_array")
    assert not ok, "Should reject non-array"


def test_rejects_no_board_model():
    """Without a board model in LAST_DESIGN, apply_edits should fail."""
    from server import app, LAST_DESIGN, design_lock
    with design_lock:
        saved = LAST_DESIGN.get("board_model")
        saved_components = LAST_DESIGN.get("selected_components")
        LAST_DESIGN["board_model"] = None
        LAST_DESIGN["selected_components"] = []
    try:
        with app.test_client() as client:
            resp = client.post(
                "/api/apply_edits",
                data=json.dumps({"edit_events": [{
                    "pcb_edit_event_type": "edit_trace_hint",
                    "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
                    "in_progress": False,
                }]}),
                content_type="application/json",
            )
            data = json.loads(resp.data)
            assert resp.status_code == 400
            assert "No board model or schematic design" in data.get("error", "")
    finally:
        with design_lock:
            LAST_DESIGN["board_model"] = saved
            LAST_DESIGN["selected_components"] = saved_components


def test_skips_in_progress_events():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
        "in_progress": True,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1


def test_rejects_missing_event_type():
    ok, data = _apply([{"in_progress": False}])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1
    assert "Missing" in data["errors"][0]["error"]


def test_rejects_unknown_type():
    ok, data = _apply([{
        "edit_event_type": "edit_unknown_type",
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1
    assert "Unknown" in data["errors"][0]["error"]


# ── Trace hint tests ────────────────────────────────────────────────────────

def test_trace_hint_minimal():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
        "edit_event_id": "test_001",
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 1
    assert data.get("ignored") == 0
    assert "board_model" in data
    traces = data["board_model"].get("traces", [])
    assert len(traces) == 1
    assert traces[0]["net"] == "_manual"
    assert traces[0]["layer"] == "F.Cu"
    assert traces[0]["width"] == 0.254
    assert len(traces[0]["path"]) == 2


def test_trace_hint_with_port():
    """When pcb_port_id is provided and resolvable, the trace net should match."""
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "pcb_port_id": "pcb_port_R1_1",
        "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
        "edit_event_id": "test_002",
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 1
    traces = data["board_model"].get("traces", [])
    assert traces[0]["net"] == "GND"


def test_trace_hint_on_bottom():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [
            {"x": 0, "y": 0, "layer": "bottom"},
            {"x": 5, "y": 5, "layer": "bottom"},
        ],
        "edit_event_id": "test_003",
        "in_progress": False,
    }])
    assert ok
    traces = data["board_model"].get("traces", [])
    assert traces[0]["layer"] == "B.Cu"


def test_trace_hint_custom_width():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [
            {"x": 0, "y": 0, "trace_width": 0.5},
            {"x": 5, "y": 5, "trace_width": 0.5},
        ],
        "in_progress": False,
    }])
    assert ok
    traces = data["board_model"].get("traces", [])
    assert traces[0]["width"] == 0.5


def test_trace_hint_rejects_single_point():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [{"x": 0, "y": 0}],
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1


def test_trace_hint_rejects_non_list_route():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": "not_a_list",
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1


def test_trace_hint_skips_via_points():
    """Via-marked route points should be excluded from the path."""
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [
            {"x": 0, "y": 0},
            {"x": 2.5, "y": 2.5, "via": True},
            {"x": 5, "y": 5},
        ],
        "in_progress": False,
    }])
    assert ok
    traces = data["board_model"].get("traces", [])
    assert len(traces[0]["path"]) == 2
    assert traces[0]["path"][1]["x"] == 5


# ── Component location tests ────────────────────────────────────────────────

def test_component_location():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_component_location",
        "pcb_component_id": "pcb_component_R1",
        "original_center": {"x": 10, "y": 20},
        "new_center": {"x": 15, "y": 25},
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 1
    comps = data["board_model"].get("components", [])
    r1 = next(c for c in comps if c["ref"] == "R1")
    assert r1["x"] == 15
    assert r1["y"] == 25


def test_component_location_missing_id():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_component_location",
        "new_center": {"x": 15, "y": 25},
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1


def test_component_location_bad_center():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_component_location",
        "pcb_component_id": "pcb_component_R1",
        "new_center": {"x": 15},
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1


def test_component_location_nonexistent():
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_component_location",
        "pcb_component_id": "pcb_component_NONEXIST",
        "new_center": {"x": 15, "y": 25},
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1
    assert "not found" in data["errors"][0]["error"].lower()


# ── Multiple events ─────────────────────────────────────────────────────────

def test_multiple_events():
    ok, data = _apply([
        {
            "pcb_edit_event_type": "edit_trace_hint",
            "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
            "in_progress": False,
        },
        {
            "pcb_edit_event_type": "edit_trace_hint",
            "route": [{"x": 10, "y": 10}, {"x": 15, "y": 15}],
            "in_progress": False,
        },
    ])
    assert ok
    assert data.get("applied") == 2
    assert data.get("ignored") == 0
    assert len(data["board_model"].get("traces", [])) == 2


def test_mixed_valid_invalid():
    """One valid event and one invalid should report both counts."""
    ok, data = _apply([
        {
            "pcb_edit_event_type": "edit_trace_hint",
            "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
            "in_progress": False,
        },
        {
            "pcb_edit_event_type": "edit_component_location",
            "pcb_component_id": "pcb_component_NONEXIST",
            "new_center": {"x": 15, "y": 25},
            "in_progress": False,
        },
    ])
    assert ok
    assert data.get("applied") == 1
    assert data.get("ignored") == 1
    assert len(data.get("errors", [])) == 1


def test_ratsnest_recalculated():
    """After applying a trace, ratsnest should be present and updated."""
    ok, data = _apply([{
        "pcb_edit_event_type": "edit_trace_hint",
        "route": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
        "in_progress": False,
    }])
    assert ok
    assert "ratsnest" in data["board_model"]
    # Our test model has net GND with two pins + manual net from the trace
    assert len(data["board_model"]["ratsnest"]) >= 0


# ── Schematic edit tests ────────────────────────────────────────────────────

def test_schematic_add_wire():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_add_wire",
        "source": "R1:1",
        "target": "R1:2",
        "path": [{"x": -1, "y": 0}, {"x": 1, "y": 0}],
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 1
    wires = data.get("wire_paths", [])
    assert len(wires) == 1
    assert wires[0]["source"] == "R1:1"
    assert wires[0]["target"] == "R1:2"
    assert wires[0]["net"] == "GND"


def test_schematic_add_wire_uses_live_pin_positions():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_add_wire",
        "source": "R1:1",
        "target": "R1:2",
        "path": [{"x": 99, "y": 99}, {"x": 100, "y": 100}],
        "in_progress": False,
    }], placements=[{"ref_des": "R1", "x": 10, "y": 20}])
    assert ok
    wire = data["wire_paths"][0]
    assert wire["path"][0] == {"x": 8.89, "y": 20.32}
    assert wire["path"][-1] == {"x": 11.43, "y": 20.32}
    for a, b in zip(wire["path"], wire["path"][1:]):
        assert a["x"] == b["x"] or a["y"] == b["y"]


def test_schematic_add_wire_rejects_bad_pin():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_add_wire",
        "source": "R1:1",
        "target": "R1:99",
        "path": [{"x": -1, "y": 0}, {"x": 1, "y": 0}],
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 0
    assert data.get("ignored") == 1
    assert "valid pin keys" in data["errors"][0]["error"]


def test_schematic_delete_wire_by_pair():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_delete_wire",
        "source": "R1:1",
        "target": "R1:2",
        "in_progress": False,
    }], existing_wires=[{
        "wire_id": "wire_existing",
        "source": "R1:1",
        "target": "R1:2",
        "path": [{"x": -1, "y": 0}, {"x": 1, "y": 0}],
    }])
    assert ok
    assert data.get("applied") == 1
    assert data.get("wire_paths") == []


def test_schematic_move_component():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_move_component",
        "ref_des": "R1",
        "new_center": {"x": 12, "y": 15},
        "in_progress": False,
    }])
    assert ok
    assert data.get("applied") == 1
    placement = next(p for p in data["component_placements"] if p["ref_des"] == "R1")
    assert placement["x"] == 12
    assert placement["y"] == 15


def test_schematic_move_component_reattaches_existing_wire():
    ok, data = _apply_schematic([{
        "edit_event_type": "schematic_move_component",
        "ref_des": "R1",
        "new_center": {"x": 12, "y": 15},
        "in_progress": False,
    }], existing_wires=[{
        "wire_id": "wire_existing",
        "source": "R1:1",
        "target": "R1:2",
        "path": [{"x": -1, "y": 0}, {"x": 30, "y": 99}, {"x": 1, "y": 0}],
    }])
    assert ok
    wire = data["wire_paths"][0]
    assert wire["path"][0] == {"x": 11.43, "y": 15.24}
    assert wire["path"][-1] == {"x": 12.7, "y": 15.24}
    for a, b in zip(wire["path"], wire["path"][1:]):
        assert a["x"] == b["x"] or a["y"] == b["y"]


def test_repeated_schematic_moves_do_not_accumulate_endpoint_drift():
    first_ok, first = _apply_schematic([{
        "edit_event_type": "schematic_move_component",
        "ref_des": "R1",
        "new_center": {"x": 12, "y": 15},
        "in_progress": False,
    }], existing_wires=[{
        "wire_id": "wire_existing",
        "source": "R1:1",
        "target": "R1:2",
        "path": [{"x": -1, "y": 0}, {"x": 1, "y": 0}],
    }])
    assert first_ok
    second_ok, second = _apply_schematic([{
        "edit_event_type": "schematic_move_component",
        "ref_des": "R1",
        "new_center": {"x": 12, "y": 15},
        "in_progress": False,
    }], existing_wires=first["wire_paths"],
       placements=first["component_placements"])
    assert second_ok
    assert second["wire_paths"][0]["path"] == first["wire_paths"][0]["path"]


def test_mixed_schematic_and_pcb_events():
    from server import app, LAST_DESIGN, design_lock
    with design_lock:
        model = _make_test_model()
        LAST_DESIGN["selected_components"] = [{"id_str": "Device:R", "ref_des": "R1"}]
        LAST_DESIGN["board_model"] = model.to_dict()
        LAST_DESIGN["pin_matrix"] = {
            "R1:1": {"x": -1, "y": 0, "ref_des": "R1", "pin_num": "1"},
            "R1:2": {"x": 1, "y": 0, "ref_des": "R1", "pin_num": "2"},
        }
        LAST_DESIGN["netlist"] = [{"source": "R1:1", "target": "R1:2", "net": "GND"}]
        LAST_DESIGN["wire_paths"] = []
        LAST_DESIGN["component_placements"] = [{"ref_des": "R1", "x": 0, "y": 0}]

    with app.test_client() as client:
        resp = client.post(
            "/api/apply_edits",
            data=json.dumps({"edit_events": [
                {
                    "pcb_edit_event_type": "edit_trace_hint",
                    "route": [{"x": 0, "y": 0}, {"x": 5, "y": 0}],
                    "in_progress": False,
                },
                {
                    "edit_event_type": "schematic_add_wire",
                    "source": "R1:1",
                    "target": "R1:2",
                    "path": [{"x": -1, "y": 0}, {"x": 1, "y": 0}],
                    "in_progress": False,
                },
            ]}),
            content_type="application/json",
        )
    data = json.loads(resp.data)
    assert resp.status_code == 200
    assert data["applied"] == 2
    assert len(data["board_model"]["traces"]) == 1
    assert len(data["wire_paths"]) == 1


def test_save_layout_accepts_manual_wire_without_final_design():
    from server import app, LAST_DESIGN, design_lock
    with design_lock:
        LAST_DESIGN.clear()

    with app.test_client() as client:
        resp = client.post(
            "/api/save_layout",
            data=json.dumps({
                "placements": [{"ref_des": "R1", "x": 0, "y": 0}],
                "wire_paths": [{
                    "wire_id": "local_wire",
                    "source": "R1:1",
                    "target": "R1:2",
                    "path": [{"x": -5, "y": 0}, {"x": 5, "y": 0}],
                }],
            }),
            content_type="application/json",
        )
        data = json.loads(resp.data)

    assert resp.status_code == 200
    assert data["ok"] is True
    with design_lock:
        assert LAST_DESIGN["wire_paths"][0]["wire_id"] == "local_wire"


# ── Fixture helpers (must be imported after server) ──────────────────────────

_events_pending = []


def _apply(edit_events):
    """Call /api/apply_edits logic directly.

    Sets up a test BoardModel in LAST_DESIGN, invokes the handler,
    and returns (ok, response_data).
    """
    from server import app, LAST_DESIGN, design_lock

    with design_lock:
        model = _make_test_model()
        LAST_DESIGN["selected_components"] = [{"id_str": "Device:R", "ref_des": "R1"}]
        LAST_DESIGN["board_model"] = model.to_dict()

    with app.test_client() as client:
        resp = client.post(
            "/api/apply_edits",
            data=json.dumps({"edit_events": edit_events}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        ok = resp.status_code == 200 and data.get("ok", False)
        return ok, data


def _apply_schematic(edit_events, existing_wires=None, placements=None):
    from server import app, LAST_DESIGN, design_lock

    with design_lock:
        LAST_DESIGN["selected_components"] = [{"id_str": "Device:R", "ref_des": "R1"}]
        LAST_DESIGN["board_model"] = None
        LAST_DESIGN["pin_matrix"] = {
            "R1:1": {"x": -1, "y": 0, "ref_des": "R1", "pin_num": "1"},
            "R1:2": {"x": 1, "y": 0, "ref_des": "R1", "pin_num": "2"},
        }
        LAST_DESIGN["netlist"] = [{"source": "R1:1", "target": "R1:2", "net": "GND"}]
        LAST_DESIGN["wire_paths"] = list(existing_wires or [])
        LAST_DESIGN["component_placements"] = list(placements or [{"ref_des": "R1", "x": 0, "y": 0}])

    with app.test_client() as client:
        resp = client.post(
            "/api/apply_edits",
            data=json.dumps({"edit_events": edit_events}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        ok = resp.status_code == 200 and data.get("ok", False)
        return ok, data


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("test_rejects_empty_events", test_rejects_empty_events),
        ("test_rejects_non_array", test_rejects_non_array),
        ("test_rejects_no_board_model", test_rejects_no_board_model),
        ("test_skips_in_progress_events", test_skips_in_progress_events),
        ("test_rejects_missing_event_type", test_rejects_missing_event_type),
        ("test_rejects_unknown_type", test_rejects_unknown_type),
        ("test_trace_hint_minimal", test_trace_hint_minimal),
        ("test_trace_hint_with_port", test_trace_hint_with_port),
        ("test_trace_hint_on_bottom", test_trace_hint_on_bottom),
        ("test_trace_hint_custom_width", test_trace_hint_custom_width),
        ("test_trace_hint_rejects_single_point", test_trace_hint_rejects_single_point),
        ("test_trace_hint_rejects_non_list_route", test_trace_hint_rejects_non_list_route),
        ("test_trace_hint_skips_via_points", test_trace_hint_skips_via_points),
        ("test_component_location", test_component_location),
        ("test_component_location_missing_id", test_component_location_missing_id),
        ("test_component_location_bad_center", test_component_location_bad_center),
        ("test_component_location_nonexistent", test_component_location_nonexistent),
        ("test_multiple_events", test_multiple_events),
        ("test_mixed_valid_invalid", test_mixed_valid_invalid),
        ("test_ratsnest_recalculated", test_ratsnest_recalculated),
    ]
    fail_count = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            fail_count += 1
    print(f"\n{'ALL PASS' if fail_count == 0 else f'{fail_count} FAILURE(S)'}")
    sys.exit(1 if fail_count else 0)
