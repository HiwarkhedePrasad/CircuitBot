import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _sample_board_model():
    return {
        "version": "20260206",
        "generator": "circuitbot",
        "components": [
            {
                "ref": "R1",
                "footprint": "Resistor_SMD:R_0805",
                "x": 12.34,
                "y": 56.78,
                "rotation": 90,
                "layer": "F.Cu",
                "value": "10k",
                "pads": [
                    {
                        "number": "1",
                        "x": -1.0,
                        "y": 0.0,
                        "width": 1.2,
                        "height": 0.8,
                        "shape": "roundrect",
                        "type": "smd",
                        "rotation": 0.0,
                        "drill": None,
                        "roundrect_rratio": 0.25,
                        "layers": ["F.Cu", "F.Paste", "F.Mask"],
                    },
                    {
                        "number": "2",
                        "x": 1.0,
                        "y": 0.0,
                        "width": 1.2,
                        "height": 0.8,
                        "shape": "roundrect",
                        "type": "smd",
                        "rotation": 0.0,
                        "drill": None,
                        "roundrect_rratio": 0.25,
                        "layers": ["F.Cu", "F.Paste", "F.Mask"],
                    },
                ],
                "graphics": [],
            }
        ],
        "traces": [
            {
                "net": "_manual",
                "layer": "F.Cu",
                "width": 0.254,
                "path": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
                "via": None,
            }
        ],
        "vias": [
            {
                "x": 3.0,
                "y": 4.0,
                "drill": 0.3,
                "diameter": 0.7,
                "layers": ["F.Cu", "B.Cu"],
                "net": "_manual",
            }
        ],
        "nets": [{"name": "_manual", "pins": []}],
        "outline_segments": [
            {
                "kind": "gr_line",
                "layer": "Edge.Cuts",
                "start": {"x": 0.0, "y": 0.0},
                "end": {"x": 10.0, "y": 0.0},
                "points": [],
            }
        ],
    }



# Test helper: access the default session design
def _td():
    from server import session_manager
    return session_manager.get_or_create("test").last_design

def test_save_board_model_marks_render_from_model_and_persists():
    from server import app, session_manager, design_lock

    board_model = _sample_board_model()
    with app.test_client() as client:
        response = client.post(
            "/api/save_board_model?session_id=test",
            data=json.dumps({"board_model": board_model}),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["ok"] is True

    with design_lock:
        saved = _td()["board_model"]
        assert saved["_render_from_model"] is True
        assert saved["components"][0]["x"] == 12.34
        assert saved["traces"][0]["path"][1] == {"x": 3.0, "y": 4.0}


def test_pcb_render_source_uses_current_saved_board_model():
    from server import app, session_manager, design_lock

    board_model = _sample_board_model()
    board_model["components"][0]["x"] = 44.4
    board_model["components"][0]["y"] = 55.5
    board_model["traces"][0]["path"] = [{"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 20.0}]

    with design_lock:
        _td()["board_model"] = dict(board_model, _render_from_model=True)
        _td()["selected_components"] = []

    with app.test_client() as client:
        response = client.get("/api/pcb_render_source?session_id=test")

    assert response.status_code == 200
    text = response.data.decode("utf-8")
    assert "(at 44.4 55.5 90)" in text
    assert '(layer "F.Cu")' in text
    assert "(segment (start 10 10) (end 20 20)" in text or "(segment (start 10.0 10.0) (end 20.0 20.0)" in text


def test_export_pcb_works_with_board_model_and_empty_selected_components():
    from server import app, session_manager, design_lock

    board_model = _sample_board_model()
    with design_lock:
        _td()["board_model"] = dict(board_model, _render_from_model=True)
        _td()["selected_components"] = []

    with app.test_client() as client:
        response = client.get("/api/export_pcb?session_id=test")

    assert response.status_code == 200
    assert "kicad_pcb" in response.data.decode("utf-8")


# ── Schematic export tests ─────────────────────────────────────────────


def test_export_sch_returns_valid_kicad_sch():
    """Schematic export must return valid kicad_sch when a design exists."""
    from server import app, session_manager, design_lock

    with design_lock:
        _td()["selected_components"] = [
            {"id_str": "Device:R", "ref_des": "R1", "category": "Component",
             "description": "Resistor", "footprint": "Resistor_SMD:R_0805"},
        ]
        _td()["component_ops"] = {}
        _td()["component_placements"] = []
        _td()["power_labels"] = []
        _td()["wire_paths"] = []

    with app.test_client() as client:
        response = client.get("/api/export_sch?session_id=test")

    assert response.status_code == 200
    text = response.data.decode("utf-8")
    # Must be parseable as a KiCad schematic S-expression
    assert text.startswith("(kicad_sch")
    assert "(lib_symbols" in text
    assert "(sheet_instances" in text


def test_export_sch_fails_without_design():
    from server import app, session_manager
    """Export should 404 when no design exists for the session."""
    # Use a session that has never been populated
    with app.test_client() as client:
        response = client.get("/api/export_sch?session_id=empty-test-session")

    assert response.status_code == 404
    data = json.loads(response.data)
    assert "No design generated yet" in data["error"]


def test_export_pcb_fails_without_design():
    from server import app, session_manager
    """PCB export should 404 when no design exists for the session."""
    with app.test_client() as client:
        response = client.get("/api/export_pcb?session_id=no-design-session")

    assert response.status_code == 404
    data = json.loads(response.data)
    assert "No design generated yet" in data["error"]


def test_export_pcb_rejects_unrouted_agent_board():
    from server import app, design_lock

    board_model = _sample_board_model()
    board_model["traces"] = []
    with design_lock:
        _td()["board_model"] = dict(board_model, _render_from_model=True)
        _td()["selected_components"] = [{"ref_des": "R1", "id_str": "Device:R"}]
        _td()["netlist"] = [{"source": "R1:1", "target": "R1:2", "net": "TEST"}]

    with app.test_client() as client:
        response = client.get("/api/export_pcb?session_id=test")

    assert response.status_code == 409
    assert "unrouted signal connections" in json.loads(response.data)["error"]


# ── Session isolation tests ────────────────────────────────────────────


def test_export_cross_session_isolation():
    """Design in session 'a' should not be visible from session 'b'."""
    from server import app, design_lock, session_manager

    # Populate session 'a' with a board model
    bm_a = _sample_board_model()
    with design_lock:
        session_manager.get_or_create("session_a").last_design["board_model"] = dict(bm_a, _render_from_model=True)

    # Export from session 'b' must fail
    with app.test_client() as client:
        resp_b = client.get("/api/export_pcb?session_id=session_b")
    assert resp_b.status_code == 404

    # Export from session 'a' must succeed
    with app.test_client() as client:
        resp_a = client.get("/api/export_pcb?session_id=session_a")
    assert resp_a.status_code == 200
    assert "kicad_pcb" in resp_a.data.decode("utf-8")


def test_save_board_model_respects_session():
    """save_board_model should write only to the specified session,
    not to 'default' or other sessions."""
    from server import app, design_lock, session_manager

    bm = _sample_board_model()
    with design_lock:
        session_manager.get_or_create("default").last_design.clear()
        session_manager.get_or_create("custom-session").last_design.clear()
    with app.test_client() as client:
        resp = client.post(
            "/api/save_board_model?session_id=custom-session",
            data=json.dumps({"board_model": bm}),
            content_type="application/json",
        )
    assert resp.status_code == 200

    # 'default' session should be empty
    with design_lock:
        default_bm = session_manager.get_or_create("default").last_design.get("board_model")
    assert default_bm is None

    # 'custom-session' should have the board model
    with design_lock:
        custom_bm = session_manager.get_or_create("custom-session").last_design.get("board_model")
    assert custom_bm is not None
    assert custom_bm["_render_from_model"] is True
