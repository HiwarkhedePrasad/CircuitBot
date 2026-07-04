import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pcb_design.pcb_import import import_board


def test_import_board_handles_mixed_top_level_items_without_recursive_rescan_assumptions():
    board_text = textwrap.dedent(
        """
        (kicad_pcb
          (version 20240101)
          (net 0 "")
          (net 1 "GND")
          (footprint "Connector_PinHeader_2.54mm:PinHeader_1x02"
            (layer "F.Cu")
            (at 0 0 0)
            (property "Reference" "J1" (at 0 -2 0) (layer "F.SilkS"))
            (property "Value" "HDR" (at 0 2 0) (layer "F.Fab"))
            (pad "1" thru_hole circle (at 0 0 0) (size 1.5 1.5) (drill 0.8) (layers "*.Cu" "*.Mask") (net 1 "GND"))
            (pad "2" thru_hole circle (at 2.54 0 0) (size 1.5 1.5) (drill 0.8) (layers "*.Cu" "*.Mask"))
          )
          (segment (start 0 0) (end 10 0) (width 0.5) (layer "F.Cu") (net 1))
          (segment (start 10 0) (end 20 0) (width 0.25) (layer "B.Cu") (net 1))
          (arc (start 20 0) (mid 22 2) (end 24 0) (width 0.25) (layer "back_copper") (net 1))
          (via (at 10 0) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
          (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts"))
          (gr_arc (start 20 0) (mid 25 5) (end 20 10) (layer "Edge.Cuts"))
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
    assert len(model.traces) == 3
    assert len(model.vias) == 1
    assert len(model.outline_segments) == 2
    assert model.traces[0].layer == "F.Cu"
    assert model.traces[1].layer == "B.Cu"
    assert model.traces[2].layer == "B.Cu"
    assert model.vias[0].layers == ["F.Cu", "B.Cu"]
    gnd_net = next(net for net in model.nets if net["name"] == "GND")
    assert gnd_net["pins"] == ["J1:1"]
