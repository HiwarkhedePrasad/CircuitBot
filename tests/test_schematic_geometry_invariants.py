import pytest

from agent.kicad_export import generate_kicad_sch
from agent.routing.api import _abs_pin_position, _paths_intersect, route_traces
from agent.routing.collision import _path_collisions
from agent.routing.geometry import _absolute_pin_position
from agent.sexpr_utils import _extract_pins_from_ops


def _component(ref, x, y, rotation=0, bbox=None):
    return {
        "ref_des": ref,
        "x": x,
        "y": y,
        "rotation": rotation,
        "bbox": bbox or {"x": -3.0, "y": -3.0, "w": 6.0, "h": 6.0},
    }


def test_pin_position_uses_component_rotation_everywhere():
    pin = {"x": 2.54, "y": 1.27}
    placement = {"ref_des": "U1", "x": 10.16, "y": 20.32, "rotation": 90}

    expected = (11.43, 17.78)
    assert _absolute_pin_position(pin, placement) == expected
    assert _abs_pin_position("U1:1", {"U1:1": pin}, [placement]) == expected


def test_pin_extraction_does_not_move_coincident_symbol_pins():
    ops = [
        ["pin", ["at", "0", "0", "0"], ["length", "2.54"], ["name", "~"], ["number", "1"]],
        ["pin", ["at", "0", "0", "0"], ["length", "2.54"], ["name", "~"], ["number", "2"]],
    ]

    pins = _extract_pins_from_ops(ops, "U1")
    assert (pins["U1:1"]["x"], pins["U1:1"]["y"]) == (0.0, 0.0)
    assert (pins["U1:2"]["x"], pins["U1:2"]["y"]) == (0.0, 0.0)


def test_rotated_component_keepout_blocks_route_through_body():
    source = _component("S1", -20.32, 0.0)
    blocker = _component(
        "X1", 0.0, 0.0, rotation=90,
        bbox={"x": -2.54, "y": -10.16, "w": 5.08, "h": 20.32},
    )
    target = _component("T1", 20.32, 0.0)
    components = [source, blocker, target]
    pins = {
        "S1:1": {"x": 3.81, "y": 0.0, "angle": 180},
        "T1:1": {"x": -3.81, "y": 0.0, "angle": 0},
    }

    traces, dropped = route_traces(
        components, [{"source": "S1:1", "target": "T1:1", "net": "SIG"}], pins,
    )

    assert not dropped
    path = [(point["x"], point["y"]) for point in traces[0]["path"]]
    assert _path_collisions(path, components, "S1", "T1") == 0


def test_different_nets_cannot_share_existing_wire_geometry():
    components = [
        _component("A1", 0.0, 0.0),
        _component("B1", 20.32, 0.0),
        _component("C1", 0.0, 0.0),
        _component("D1", 20.32, 0.0),
    ]
    pins = {
        "A1:1": {"x": 0.0, "y": 0.0, "angle": 0},
        "B1:1": {"x": 0.0, "y": 0.0, "angle": 180},
        "C1:1": {"x": 0.0, "y": 0.0, "angle": 0},
        "D1:1": {"x": 0.0, "y": 0.0, "angle": 180},
    }
    existing = [{
        "source": "A1:1", "target": "B1:1", "net": "NET_A",
        "path": [{"x": 0.0, "y": 0.0}, {"x": 20.32, "y": 0.0}],
    }]

    traces, dropped = route_traces(
        components, [{"source": "C1:1", "target": "D1:1", "net": "NET_B"}], pins,
        existing_traces=existing,
    )

    assert traces == []
    assert dropped == [("C1", "D1")]


def test_cross_net_intersection_detection_is_exact_for_off_grid_paths():
    horizontal = [(0.2, 0.4), (5.2, 0.4)]
    vertical = [(2.7, -1.1), (2.7, 1.9)]
    near_miss = [(2.7, 0.5), (5.2, 0.5)]

    assert _paths_intersect(horizontal, vertical)
    assert not _paths_intersect(horizontal, near_miss)


def test_cross_net_wire_intersection_skips_segment():
    """Cross-net crossings are valid in schematics; the intersecting segment is skipped."""
    design = {
        "selected_components": [],
        "component_ops": {},
        "component_placements": [],
        "power_labels": [],
        "wire_paths": [
            {
                "source": "A:1", "target": "B:1", "net": "NET_A",
                "path": [{"x": 0.0, "y": 0.0}, {"x": 5.08, "y": 0.0}],
            },
            {
                "source": "C:1", "target": "D:1", "net": "NET_B",
                "path": [{"x": 2.54, "y": -2.54}, {"x": 2.54, "y": 2.54}],
            },
        ],
    }

    # Should not raise — the intersecting NET_B segment is skipped
    result = generate_kicad_sch(design)
    assert "(kicad_sch" in result
