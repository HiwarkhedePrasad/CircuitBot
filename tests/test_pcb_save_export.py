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


def test_save_board_model_marks_render_from_model_and_persists():
    from server import app, LAST_DESIGN, design_lock

    board_model = _sample_board_model()
    with app.test_client() as client:
        response = client.post(
            "/api/save_board_model",
            data=json.dumps({"board_model": board_model}),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["ok"] is True

    with design_lock:
        saved = LAST_DESIGN["board_model"]
        assert saved["_render_from_model"] is True
        assert saved["components"][0]["x"] == 12.34
        assert saved["traces"][0]["path"][1] == {"x": 3.0, "y": 4.0}


def test_pcb_render_source_uses_current_saved_board_model():
    from server import app, LAST_DESIGN, design_lock

    board_model = _sample_board_model()
    board_model["components"][0]["x"] = 44.4
    board_model["components"][0]["y"] = 55.5
    board_model["traces"][0]["path"] = [{"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 20.0}]

    with design_lock:
        LAST_DESIGN["board_model"] = dict(board_model, _render_from_model=True)
        LAST_DESIGN["selected_components"] = []

    with app.test_client() as client:
        response = client.get("/api/pcb_render_source")

    assert response.status_code == 200
    text = response.data.decode("utf-8")
    assert "(at 44.4 55.5 90)" in text
    assert '(layer "F.Cu")' in text
    assert "(segment (start 10 10) (end 20 20)" in text or "(segment (start 10.0 10.0) (end 20.0 20.0)" in text


def test_export_pcb_works_with_board_model_and_empty_selected_components():
    from server import app, LAST_DESIGN, design_lock

    board_model = _sample_board_model()
    with design_lock:
        LAST_DESIGN["board_model"] = dict(board_model, _render_from_model=True)
        LAST_DESIGN["selected_components"] = []

    with app.test_client() as client:
        response = client.get("/api/export_pcb")

    assert response.status_code == 200
    assert "kicad_pcb" in response.data.decode("utf-8")
