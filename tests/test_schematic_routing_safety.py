import re

from agent.kicad_export import generate_kicad_sch
from agent.layout_engine import (
    BackendLayoutEngine,
    _path_collisions,
)


def _rect_ops(width=10.0, height=10.0):
    return [[
        "rectangle",
        ["start", -width / 2, -height / 2],
        ["end", width / 2, height / 2],
    ]]


def _overlap_count(components):
    count = 0
    for index, first in enumerate(components):
        a = first["bbox"]
        ax1, ay1 = first["x"] + a["x"], first["y"] + a["y"]
        ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
        for second in components[index + 1:]:
            b = second["bbox"]
            bx1, by1 = second["x"] + b["x"], second["y"] + b["y"]
            bx2, by2 = bx1 + b["w"], by1 + b["h"]
            if ax2 > bx1 and ax1 < bx2 and ay2 > by1 and ay1 < by2:
                count += 1
    return count


def test_placement_removes_overlaps_using_offset_geometry_bounds():
    engine = BackendLayoutEngine()
    # These boxes have very different offsets from their symbol origins,
    # which the old origin-only overlap calculation handled incorrectly.
    first_ops = [[
        "rectangle",
        ["start", -20.0, -5.0],
        ["end", -5.0, 5.0],
    ]]
    second_ops = [[
        "rectangle",
        ["start", 5.0, -5.0],
        ["end", 20.0, 5.0],
    ]]
    engine.add_component("U1", first_ops, "MCU")
    engine.add_component("U2", second_ops, "MCU")
    engine.components[0]["x"] = 10.0
    engine.components[1]["x"] = -10.0

    assert _overlap_count(engine.components) == 1
    assert engine._remove_overlaps() == 0
    assert _overlap_count(engine.components) == 0


def test_signal_routes_never_cross_intervening_component_body():
    engine = BackendLayoutEngine()
    engine.add_component("S1", _rect_ops(), "Source")
    engine.add_component("X1", _rect_ops(), "Obstacle")
    engine.add_component("T1", _rect_ops(), "Target")

    by_ref = {component["ref_des"]: component for component in engine.components}
    by_ref["S1"]["x"], by_ref["S1"]["y"] = -20.0, 0.0
    by_ref["X1"]["x"], by_ref["X1"]["y"] = 0.0, 0.0
    by_ref["T1"]["x"], by_ref["T1"]["y"] = 20.0, 0.0

    pin_matrix = {
        "S1:1": {"x": 7.54, "y": 0.0, "angle": 180},
        "T1:1": {"x": -7.54, "y": 0.0, "angle": 0},
    }
    traces, dropped = engine.route_traces(
        [{"source": "S1:1", "target": "T1:1", "net": "SIG"}],
        pin_matrix,
    )

    assert not dropped
    assert len(traces) == 1
    path = [(point["x"], point["y"]) for point in traces[0]["path"]]
    assert _path_collisions(path, engine.components, "S1", "T1") == 0


def test_power_export_uses_short_per_pin_stubs_instead_of_centroid_fanout():
    design = {
        "selected_components": [],
        "component_ops": {},
        "component_placements": [],
        "wire_paths": [],
        "power_labels": [
            {"pin": "U1:1", "net": "3V3", "x": 0.0, "y": 0.0, "dir": "left"},
            {"pin": "U2:1", "net": "3V3", "x": 50.8, "y": 25.4, "dir": "right"},
        ],
        "netlist": [],
    }

    schematic = generate_kicad_sch(design)

    assert schematic.count('(global_label "3V3"') == 2
    segments = re.findall(
        r"\(wire \(pts \(xy ([-\d.]+) ([-\d.]+)\) "
        r"\(xy ([-\d.]+) ([-\d.]+)\)\)",
        schematic,
    )
    assert len(segments) == 2
    for x1, y1, x2, y2 in segments:
        length = abs(float(x2) - float(x1)) + abs(float(y2) - float(y1))
        assert abs(length - 2.54) < 1e-9
