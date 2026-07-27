"""Connection record emitter — generates ConnectionRecords from a classified ConnectivityGraph."""

from __future__ import annotations

import uuid

from agent.connection_strategy import WIRE, LABEL, GLOBAL
from agent.connection_graph import ConnectivityGraph
from agent.routing.constants import PIN_STUB_LEN
from agent.routing.geometry import _absolute_pin_position, _pin_direction, _stub_point, _snap
from agent.routing.make_path import make_path
from agent.routing.path_utils import _path_length

_counter: dict[str, int] = {}


def _next_id(prefix: str) -> str:
    _counter[prefix] = _counter.get(prefix, 0) + 1
    return f"conn_{prefix}_{_counter[prefix]:04d}"


def _stub_length(pin: dict, component: dict) -> float:
    base = PIN_STUB_LEN
    bbox = component.get("bbox") or component.get("geom_bbox")
    if bbox:
        clearance = max(float(bbox.get("w", 0)), float(bbox.get("h", 0))) * 0.3
        return max(base, clearance)
    return base


def _make_stub(pin_key: str, pin: dict, component: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    pos = _absolute_pin_position(pin, component)
    direction = _pin_direction(pin)
    length = _stub_length(pin, component)
    stub_end = _stub_point(pos[0], pos[1], direction, length)
    return pos, stub_end


def _emit_label(net_record, graph: ConnectivityGraph) -> list[dict]:
    records = []
    for pk in net_record.pins:
        pin = graph.pin_matrix.get(pk)
        comp_ref = pk.split(":")[0]
        comp = graph.components.get(comp_ref)
        if not pin or not comp:
            continue
        pin_pos, stub_end = _make_stub(pk, pin, comp)
        records.append({
            "id": _next_id("label"),
            "type": LABEL,
            "net": net_record.name,
            "source_pin": pk,
            "target_pin": None,
            "geometry": {
                "stub_wires": [(pin_pos[0], pin_pos[1], stub_end[0], stub_end[1])],
                "label_positions": [stub_end],
                "wire_path": None,
            },
            "style": {"wire_width": 0.0, "label_size": 1.27},
        })
    return records


def _emit_global(net_record, graph: ConnectivityGraph) -> list[dict]:
    records = []
    for pk in net_record.pins:
        pin = graph.pin_matrix.get(pk)
        comp_ref = pk.split(":")[0]
        comp = graph.components.get(comp_ref)
        if not pin or not comp:
            continue
        pin_pos, stub_end = _make_stub(pk, pin, comp)
        records.append({
            "id": _next_id("global"),
            "type": GLOBAL,
            "net": net_record.name,
            "source_pin": pk,
            "target_pin": None,
            "geometry": {
                "stub_wires": [(pin_pos[0], pin_pos[1], stub_end[0], stub_end[1])],
                "label_positions": [stub_end],
                "wire_path": None,
            },
            "style": {"wire_width": 0.0, "label_size": 1.27},
        })
    return records


def _emit_wire(net_record, graph: ConnectivityGraph) -> list[dict]:
    records = []
    pins = net_record.pins

    comp_refs = list(dict.fromkeys(p.split(":")[0] for p in pins if ":" in p))
    if len(comp_refs) < 2:
        return records

    anchor = comp_refs[0]
    if net_record.active_components:
        anchor = net_record.active_components[0]["ref_des"]

    anchor_pins = [p for p in pins if p.startswith(anchor + ":")]
    if not anchor_pins:
        anchor_pins = [pins[0]]
    anchor_pin = anchor_pins[0]
    apin_obj = graph.pin_matrix.get(anchor_pin)
    acomp_obj = graph.components.get(anchor)
    if not apin_obj or not acomp_obj:
        return records

    apos = _absolute_pin_position(apin_obj, acomp_obj)
    comps_for_routing = list(graph.components.values())

    for pk in pins:
        if pk == anchor_pin:
            continue
        ref = pk.split(":")[0]
        pin_obj = graph.pin_matrix.get(pk)
        comp_obj = graph.components.get(ref)
        if not pin_obj or not comp_obj:
            continue
        tpos = _absolute_pin_position(pin_obj, comp_obj)
        s_dir = _pin_direction(apin_obj)
        t_dir = _pin_direction(pin_obj)

        path = make_path(apos, s_dir, tpos, t_dir, comps_for_routing, anchor, ref)
        if not path:
            path = [apos, tpos]

        path_len = _path_length(path)
        wire_pts = [(p[0], p[1]) for p in path]
        records.append({
            "id": _next_id("wire"),
            "type": WIRE,
            "net": net_record.name,
            "source_pin": anchor_pin,
            "target_pin": pk,
            "geometry": {
                "stub_wires": [],
                "label_positions": [],
                "wire_path": wire_pts,
            },
            "style": {"wire_width": 0.0, "label_size": 1.27},
        })

    return records


_EMITTERS = {
    WIRE: _emit_wire,
    LABEL: _emit_label,
    GLOBAL: _emit_global,
}


def emit_connections(graph: ConnectivityGraph) -> list[dict]:
    _counter.clear()
    records = []
    for net in graph.nets:
        emitter = _EMITTERS.get(net.strategy)
        if emitter:
            records.extend(emitter(net, graph))
    return records
