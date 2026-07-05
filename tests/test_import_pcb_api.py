import io
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_import_pcb_api_accepts_upload_and_returns_board_model():
    from server import app

    board_text = textwrap.dedent(
        """
        (kicad_pcb
          (version 20240101)
          (net 0 "")
          (footprint "Resistor_SMD:R_0805"
            (layer "F.Cu")
            (at 10 20 90)
            (property "Reference" "R1" (at 0 -1.8 0) (layer "F.SilkS"))
            (property "Value" "10k" (at 0 1.8 0) (layer "F.Fab"))
            (pad "1" smd roundrect (at -1 0 0) (size 1.2 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))
            (pad "2" smd roundrect (at 1 0 0) (size 1.2 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))
          )
        )
        """
    ).strip()

    with app.test_client() as client:
        response = client.post(
            "/api/import_pcb",
            data={
                "pcb_file": (io.BytesIO(board_text.encode("utf-8")), "sample.kicad_pcb"),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert "board_model" in data
    assert len(data["board_model"]["components"]) == 1
    assert data["board_model"]["components"][0]["ref"] == "R1"
