from pcb_design.board_model import BoardComponent, BoardModel, BoardTrace, PadDef
from pcb_design.ratsnest import compute_ratsnest


def test_ratsnest_treats_trace_chains_as_connected():
    model = BoardModel(
        components=[
            BoardComponent(
                ref="U1",
                footprint="test",
                x=0,
                y=0,
                pads=[PadDef(number="1", x=0, y=0, width=1, height=1)],
            ),
            BoardComponent(
                ref="U2",
                footprint="test",
                x=10,
                y=0,
                pads=[PadDef(number="1", x=0, y=0, width=1, height=1)],
            ),
        ],
        traces=[
            BoardTrace(net="SIG", layer="F.Cu", width=0.254, path=[(0, 0), (5, 0)]),
            BoardTrace(net="SIG", layer="F.Cu", width=0.254, path=[(5, 0), (10, 0)]),
        ],
        nets=[{"name": "SIG", "pins": ["U1:1", "U2:1"]}],
    )

    assert compute_ratsnest(model) == {}
