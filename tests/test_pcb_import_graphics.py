import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pcb_design.pcb_import import import_board


def test_import_board_preserves_footprint_graphics_and_outline_segments():
    board_text = textwrap.dedent(
        """
        (kicad_pcb
          (version 20240101)
          (net 0 "")
          (net 1 "GND")
          (footprint "Resistor_SMD:R_0805"
            (layer "F.Cu")
            (at 10 20 90)
            (property "Reference" "R1" (at 0 -1.8 0) (layer "F.SilkS"))
            (property "Value" "10k" (at 0 1.8 0) (layer "F.Fab"))
            (fp_line (start -1.5 -0.8) (end 1.5 -0.8) (stroke (width 0.12)) (layer "F.SilkS"))
            (fp_rect (start -1.7 -1.0) (end 1.7 1.0) (stroke (width 0.05)) (layer "F.CrtYd"))
            (pad "1" smd roundrect (at -1 0 0) (size 1.2 0.8) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND"))
            (pad "2" smd roundrect (at 1 0 0) (size 1.2 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))
          )
          (gr_line (start 0 0) (end 30 0) (layer "Edge.Cuts"))
          (gr_line (start 30 0) (end 30 20) (layer "Edge.Cuts"))
        )
        """
    ).strip()

    with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False, encoding="utf-8") as handle:
        handle.write(board_text)
        temp_path = handle.name

    try:
        model = import_board(temp_path)
    finally:
        os.unlink(temp_path)

    assert len(model.components) == 1
    component = model.components[0]
    assert component.ref == "R1"
    assert component.rotation == 90
    assert len(component.pads) == 2
    assert component.pads[0].shape == "roundrect"
    assert len(component.graphics) >= 3
    assert any(item["kind"] == "fp_line" for item in component.graphics)
    assert any(item["kind"] == "fp_rect" for item in component.graphics)
    assert any(item["kind"] == "property" and item["text"] == "R1" for item in component.graphics)
    assert model._pcbnew_content is not None
    assert '(footprint "Resistor_SMD:R_0805"' in model._pcbnew_content
    assert len(model.outline_segments) == 2
    assert model.outline_segments[0]["kind"] == "gr_line"


def test_get_pads_for_net_does_not_move_pad_center_by_pad_rotation():
    board_text = textwrap.dedent(
        """
        (kicad_pcb
          (version 20240101)
          (net 0 "")
          (net 1 "SIG")
          (footprint "Test:RotPad"
            (layer "F.Cu")
            (at 10 20 90)
            (property "Reference" "U1" (at 0 -1 0) (layer "F.SilkS"))
            (pad "1" smd rect (at 1 0 90) (size 1.2 0.8) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "SIG"))
          )
        )
        """
    ).strip()

    with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False, encoding="utf-8") as handle:
        handle.write(board_text)
        temp_path = handle.name

    try:
        model = import_board(temp_path)
    finally:
        os.unlink(temp_path)

    assert model.get_pads_for_net("SIG") == [(10.0, 21.0)]
