"""Tests for ConnectivityGraph."""

from agent.connection_graph import ConnectivityGraph


def _make_component(ref_des, id_str="Device:R_Small", category=None):
    if category is None:
        category = "Sensor" if not id_str.startswith("Device:") else "Device"
    return {
        "ref_des": ref_des,
        "id_str": id_str,
        "category": category,
        "x": 0.0,
        "y": 0.0,
        "rotation": 0,
    }


def test_graph_builds_nets():
    pins = {
        "U1:1": {"name": "VCC", "x": 0, "y": 0, "etype": "power_in"},
        "U1:2": {"name": "GPIO1", "x": 0, "y": 2.54, "etype": "bidirectional"},
        "R1:1": {"name": "1", "x": 0, "y": 5.08, "etype": "passive"},
        "R1:2": {"name": "2", "x": 0, "y": 7.62, "etype": "passive"},
    }
    nets = [
        {"net": "3V3", "pins": ["U1:1"]},
        {"net": "GPIO1", "pins": ["U1:2", "R1:1"]},
    ]
    components = [
        _make_component("U1", "Sensor_Temperature:BME280"),
        _make_component("R1", "Device:R_Small"),
    ]
    placements = {"U1": {"x": 0, "y": 0}, "R1": {"x": 10, "y": 10}}

    graph = ConnectivityGraph(nets, pins, components, placements)
    assert len(graph.nets) == 2

    net_gpio = next(n for n in graph.nets if n.name == "GPIO1")
    assert len(net_gpio.pins) == 2
    assert net_gpio.active_components
    assert net_gpio.passive_components


def test_graph_classifies_strategies():
    pins = {
        "U1:1": {"name": "SDA", "x": 0, "y": 0, "etype": "bidirectional"},
        "U2:1": {"name": "SDA", "x": 0, "y": 5.08, "etype": "bidirectional"},
    }
    nets = [{"net": "SDA", "pins": ["U1:1", "U2:1"]}]
    components = [
        _make_component("U1", "Sensor_Temperature:BME280"),
        _make_component("U2", "Sensor_Temperature:TMP117xxYBG"),
    ]
    placements = {"U1": {"x": 0, "y": 0}, "U2": {"x": 100, "y": 50}}

    from agent.connection_strategy import LABEL
    graph = ConnectivityGraph(nets, pins, components, placements)
    assert graph.nets[0].strategy == LABEL


def test_span_is_computed():
    pins = {
        "U1:1": {"name": "GPIO", "x": 0, "y": 0, "etype": "bidirectional"},
        "R1:1": {"name": "1", "x": 0, "y": 2.54, "etype": "passive"},
    }
    nets = [{"net": "GPIO1", "pins": ["U1:1", "R1:1"]}]
    components = [{"ref_des": "U1", "id_str": "Sensor:XYZ"}, {"ref_des": "R1", "id_str": "Device:R_Small"}]
    placements = {"U1": {"x": 0, "y": 0}, "R1": {"x": 10, "y": 5}}
    graph = ConnectivityGraph(nets, pins, components, placements)
    assert graph.nets[0].span > 0
