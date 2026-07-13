"""Contract test: BoardModel.to_dict() → from_dict() round-trip preserves all data."""
import pytest
from pcb_design.board_model import (
    BoardModel, BoardComponent, BoardTrace, BoardVia, PadDef,
)


def _make_sample_model() -> BoardModel:
    """Build a BoardModel with representative data covering all field types."""
    model = BoardModel(version="20260206", generator="circuitbot-test")

    # Component with SMD and THT pads
    model.components.append(BoardComponent(
        ref="U1", footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm",
        x=10.0, y=20.0, rotation=45.0, layer="F.Cu", value="ESP32",
        pads=[
            PadDef(number="1", x=-3.5, y=-3.5, width=0.4, height=0.7,
                   shape="rect", type="smd", rotation=0.0,
                   layers=["F.Cu", "F.Mask", "F.Paste"]),
            PadDef(number="2", x=-3.5, y=-2.7, width=0.4, height=0.7,
                   shape="rect", type="smd", rotation=0.0,
                   layers=["F.Cu", "F.Mask", "F.Paste"]),
            PadDef(number="17", x=3.5, y=3.5, width=0.6, height=0.6,
                   shape="circle", type="tht", drill=0.3,
                   drill_width=None, layers=["F.Cu", "B.Cu", "F.Mask", "B.Mask"]),
        ],
        graphics=[{"kind": "line", "layer": "F.SilkS", "start": {"x": -4, "y": -4}, "end": {"x": 4, "y": -4}}],
    ))

    model.components.append(BoardComponent(
        ref="R1", footprint="Resistor_SMD:R_0402_1005Metric",
        x=25.0, y=15.0, rotation=0.0, layer="F.Cu", value="10k",
        pads=[
            PadDef(number="1", x=-0.5, y=0, width=0.5, height=0.6,
                   shape="rect", type="smd", layers=["F.Cu", "F.Mask", "F.Paste"]),
            PadDef(number="2", x=0.5, y=0, width=0.5, height=0.6,
                   shape="rect", type="smd", layers=["F.Cu", "F.Mask", "F.Paste"]),
        ],
        graphics=[],
    ))

    # Traces with and without vias
    model.traces.append(BoardTrace(
        net="VCC", layer="F.Cu", width=0.254,
        path=[(10.0, 20.0), (15.0, 20.0), (15.0, 15.0), (24.5, 15.0)],
        via=None,
    ))
    model.traces.append(BoardTrace(
        net="GND", layer="B.Cu", width=0.3,
        path=[(10.5, 20.5), (10.5, 30.0)],
        via=(10.5, 30.0),
    ))

    # Vias
    model.vias.append(BoardVia(
        x=10.5, y=30.0, drill=0.3, diameter=0.6,
        layers=["F.Cu", "B.Cu"], net="GND",
    ))

    # Nets
    model.nets = [
        {"name": "VCC", "pins": ["U1:1", "R1:1"]},
        {"name": "GND", "pins": ["U1:17", "R1:2"]},
    ]

    model.power_pins = [{"pin": "U1:1", "net": "VCC"}]
    model.power_labels = [{"pin": "U1:1", "net": "VCC", "x": 10.0, "y": 18.0, "dir": "up"}]
    model.outline_segments = [
        {"kind": "gr_line", "layer": "Edge.Cuts", "start": {"x": 0, "y": 0}, "end": {"x": 50, "y": 0}},
        {"kind": "gr_arc", "layer": "Edge.Cuts", "start": {"x": 50, "y": 0}, "mid": {"x": 52, "y": 2}, "end": {"x": 50, "y": 4}},
    ]

    model.apply_layer_count(4)
    return model


class TestBoardModelRoundTrip:
    """BoardModel.to_dict() → from_dict() must preserve all data."""

    def test_basic_round_trip(self):
        original = _make_sample_model()
        d = original.to_dict()
        restored = BoardModel.from_dict(d)

        assert restored.version == original.version
        assert restored.generator == original.generator
        assert restored.layer_count == original.layer_count
        assert len(restored.components) == len(original.components)
        assert len(restored.traces) == len(original.traces)
        assert len(restored.vias) == len(original.vias)
        assert len(restored.nets) == len(original.nets)

    def test_component_fields_preserved(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())

        c = restored.components[0]
        assert c.ref == "U1"
        assert c.footprint == "Package_QFP:TQFP-32_7x7mm_P0.8mm"
        assert c.x == pytest.approx(10.0)
        assert c.y == pytest.approx(20.0)
        assert c.rotation == pytest.approx(45.0)
        assert c.layer == "F.Cu"
        assert c.value == "ESP32"
        assert len(c.pads) == 3

    def test_pad_fields_preserved(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())

        # SMD pad
        p0 = restored.components[0].pads[0]
        assert p0.number == "1"
        assert p0.x == pytest.approx(-3.5)
        assert p0.width == pytest.approx(0.4)
        assert p0.shape == "rect"
        assert p0.type == "smd"
        assert p0.drill is None

        # THT pad
        p2 = restored.components[0].pads[2]
        assert p2.type == "tht"
        assert p2.drill == pytest.approx(0.3)

    def test_trace_path_tuples_vs_dicts(self):
        original = _make_sample_model()
        d = original.to_dict()

        # In dict form, path entries are {x, y} dicts
        assert isinstance(d["traces"][0]["path"][0], dict)
        assert "x" in d["traces"][0]["path"][0]

        restored = BoardModel.from_dict(d)
        # After from_dict, path entries are (x, y) tuples
        assert isinstance(restored.traces[0].path[0], tuple)
        assert restored.traces[0].path[0] == pytest.approx((10.0, 20.0))

    def test_trace_via_tuple_vs_dict(self):
        original = _make_sample_model()
        d = original.to_dict()

        # In dict form, via is {x, y} dict
        assert isinstance(d["traces"][1]["via"], dict)

        restored = BoardModel.from_dict(d)
        # After from_dict, via is (x, y) tuple
        assert isinstance(restored.traces[1].via, tuple)
        assert restored.traces[1].via == pytest.approx((10.5, 30.0))

    def test_trace_without_via(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())
        assert restored.traces[0].via is None

    def test_via_fields_preserved(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())

        v = restored.vias[0]
        assert v.x == pytest.approx(10.5)
        assert v.y == pytest.approx(30.0)
        assert v.drill == pytest.approx(0.3)
        assert v.diameter == pytest.approx(0.6)
        assert v.net == "GND"

    def test_nets_normalized(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())

        net_names = {n["name"] for n in restored.nets}
        assert "VCC" in net_names
        assert "GND" in net_names

    def test_layer_count_generates_layers(self):
        original = _make_sample_model()
        restored = BoardModel.from_dict(original.to_dict())

        assert restored.layer_count == 4
        copper_layers = [name for _, name, _ in restored.layers if "Cu" in name]
        assert "F.Cu" in copper_layers
        assert "B.Cu" in copper_layers
        assert "In1.Cu" in copper_layers
        assert "In2.Cu" in copper_layers

    def test_empty_model_round_trip(self):
        model = BoardModel()
        restored = BoardModel.from_dict(model.to_dict())
        assert len(restored.components) == 0
        assert len(restored.traces) == 0
        assert len(restored.vias) == 0

    def test_json_serializable(self):
        """The dict output must be JSON-serializable (no tuples, no special types)."""
        import json
        original = _make_sample_model()
        d = original.to_dict()
        # Must not raise
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_double_round_trip_stable(self):
        """Two consecutive round-trips should produce identical results."""
        original = _make_sample_model()
        d1 = original.to_dict()
        m2 = BoardModel.from_dict(d1)
        d2 = m2.to_dict()
        m3 = BoardModel.from_dict(d2)

        assert m3.to_dict() == d2
