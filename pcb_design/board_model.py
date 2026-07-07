"""Shared internal geometry model for PCB boards.

This is the single source of truth for all board data:
- Import: .kicad_pcb → BoardModel
- Agent output: placement + routing → BoardModel
- Frontend: BoardModel → PCB viewer renderer
- Export: BoardModel → .kicad_pcb
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

try:
    from shapely.geometry import Point, Polygon, LineString, MultiPolygon
    from shapely import affinity
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    Point = Polygon = LineString = MultiPolygon = None


@dataclass
class DRCConfig:
    min_clearance: float = 0.254
    min_trace_width: float = 0.254
    power_trace_width: float = 0.5
    board_edge_margin: float = 3.0
    zone_clearance: float = 0.254
    min_zone_area: float = 10.0
    thermal_relief_gap: float = 0.254
    thermal_spoke_width: float = 0.254


@dataclass
class PadDef:
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: str = "rect"
    type: str = "smd"
    rotation: float = 0.0
    drill: Optional[float] = None
    drill_width: Optional[float] = None
    drill_offset_x: float = 0.0
    drill_offset_y: float = 0.0
    roundrect_rratio: Optional[float] = None
    rect_delta_x: float = 0.0
    rect_delta_y: float = 0.0
    layers: list[str] = field(default_factory=lambda: ["F.Cu", "F.Mask", "F.Paste"])

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "shape": self.shape,
            "type": self.type,
            "rotation": self.rotation,
            "drill": self.drill,
            "drill_width": self.drill_width,
            "drill_offset_x": self.drill_offset_x,
            "drill_offset_y": self.drill_offset_y,
            "roundrect_rratio": self.roundrect_rratio,
            "rect_delta_x": self.rect_delta_x,
            "rect_delta_y": self.rect_delta_y,
            "layers": self.layers,
        }

    def to_polygon(self) -> Optional["Polygon"]:
        if not HAS_SHAPELY:
            return None
        w, h = self.width / 2, self.height / 2
        if self.shape == "circle":
            poly = Point(0, 0).buffer(max(w, h), resolution=16)
        elif self.shape == "oval":
            from shapely.geometry import box
            r = min(w, h)
            cw, ch = (max(w, h) - r, r * 2) if w > h else (r * 2, max(w, h) - r)
            poly = box(-cw / 2, -ch / 2, cw / 2, ch / 2).buffer(r, resolution=16, join_style=2)
        else:
            from shapely.geometry import box
            poly = box(-w, -h, w, h)
        poly = affinity.translate(poly, self.x, self.y)
        if self.rotation:
            poly = affinity.rotate(poly, self.rotation, origin=(self.x, self.y), use_radians=False)
        return poly


@dataclass
class BoardComponent:
    ref: str
    footprint: str
    x: float
    y: float
    rotation: float = 0.0
    layer: str = "F.Cu"
    value: str = ""
    pads: list[PadDef] = field(default_factory=list)
    graphics: list[dict] = field(default_factory=list)
    bbox: Optional[tuple[float, float, float, float]] = None

    def pad_polygons(self) -> list[Polygon]:
        return [p.to_polygon() for p in self.pads if p.to_polygon() is not None]

    def outline_polygon(self) -> Optional[Polygon]:
        if not HAS_SHAPELY or not self.pads:
            return None
        from shapely.geometry import box as shapely_box
        xs = [p.x for p in self.pads]
        ys = [p.y for p in self.pads]
        if not xs or not ys:
            return None
        margin = 1.0
        return shapely_box(min(xs) - margin, min(ys) - margin,
                           max(xs) + margin, max(ys) + margin)


@dataclass
class BoardTrace:
    net: str
    layer: str
    width: float
    path: list[tuple[float, float]]
    via: Optional[tuple[float, float]] = None

    def to_linestring(self) -> Optional[LineString]:
        if not HAS_SHAPELY or len(self.path) < 2:
            return None
        return LineString(self.path)


@dataclass
class BoardVia:
    x: float
    y: float
    drill: float
    diameter: float
    layers: list[str] = field(default_factory=lambda: ["F.Cu", "B.Cu"])
    net: str = ""


@dataclass
class BoardZone:
    net: str
    layer: str
    polygon: Optional["Polygon"]
    priority: int = 0


@dataclass
class BoardModel:
    version: str = "20260206"
    generator: str = "circuitbot"
    _pcbnew_content: str | None = None

    components: list[BoardComponent] = field(default_factory=list)
    traces: list[BoardTrace] = field(default_factory=list)
    vias: list[BoardVia] = field(default_factory=list)
    zones: list[BoardZone] = field(default_factory=list)

    nets: list[dict] = field(default_factory=list)
    power_pins: list[dict] = field(default_factory=list)
    power_labels: list[dict] = field(default_factory=list)
    outline_segments: list[dict] = field(default_factory=list)

    outline: Optional["Polygon"] = None
    layers: list[tuple[int, str, str]] = field(default_factory=lambda: [
        (0, "F.Cu", "signal"),
        (31, "B.Cu", "signal"),
        (36, "F.SilkS", "user"),
        (37, "Edge.Cuts", "user"),
    ])

    def component_at(self, ref: str) -> Optional[BoardComponent]:
        for c in self.components:
            if c.ref == ref:
                return c
        return None

    def normalize_nets(self) -> None:
        """Convert any KiCad-imported net entries to the canonical format.

        Canonical format: ``{"name": str, "pins": [pin_key, ...]}``.
        This converts ``{"id": int, "name": str}`` entries (from KiCad import)
        to the canonical shape, preserving any existing pin lists.
        """
        canonical = []
        for net in self.nets:
            name = net.get("name") or net.get("net", "")
            pins = net.get("pins", [])
            if not name:
                continue
            canonical.append({"name": name, "pins": list(pins)})
        self.nets = canonical

    def get_pads_for_net(self, net_name: str) -> list[tuple[float, float]]:
        """Return absolute board-space (x, y) positions for every pad on *net_name*.

        Resolves pin keys (e.g. ``"U1:3"``) to component-relative pad offsets,
        then applies the component's position and rotation.

        Returns an empty list when the net name is unknown or has no pins.
        """
        self.normalize_nets()
        pin_keys: list[str] = []
        for net in self.nets:
            if net.get("name", "").upper() == net_name.upper():
                pin_keys = net.get("pins", [])
                break
        if not pin_keys:
            return []
        results: list[tuple[float, float]] = []
        for pk in pin_keys:
            ref, _, pnum = pk.partition(":")
            comp = self.component_at(ref)
            if comp is None:
                continue
            pad = next((p for p in comp.pads if str(p.number) == pnum), None)
            if pad is None:
                continue
            angle = math.radians(comp.rotation)
            rx = pad.x * math.cos(angle) - pad.y * math.sin(angle)
            ry = pad.x * math.sin(angle) + pad.y * math.cos(angle)
            results.append((comp.x + rx, comp.y + ry))
        return results

    def all_obstacle_polygons(self) -> list[Polygon]:
        if not HAS_SHAPELY:
            return []
        obs = []
        for c in self.components:
            o = c.outline_polygon()
            if o is not None:
                obs.append(o)
        for t in self.traces:
            ls = t.to_linestring()
            if ls is not None:
                obs.append(ls.buffer(t.width / 2, cap_style=2, join_style=2))
        return obs

    def to_dict(self) -> dict:
        def _comp_dict(c: BoardComponent) -> dict:
            return {
                "ref": c.ref,
                "footprint": c.footprint,
                "x": c.x,
                "y": c.y,
                "rotation": c.rotation,
                "layer": c.layer,
                "value": c.value,
                "pads": [p.to_dict() for p in c.pads],
                "graphics": c.graphics,
            }

        return {
            "version": self.version,
            "generator": self.generator,
            "_pcbnew_content": self._pcbnew_content,
            "components": [_comp_dict(c) for c in self.components],
            "traces": [
                {
                    "net": t.net, "layer": t.layer, "width": t.width,
                    "path": [{"x": p[0], "y": p[1]} for p in t.path],
                    "via": {"x": t.via[0], "y": t.via[1]} if t.via else None,
                }
                for t in self.traces
            ],
            "vias": [
                {
                    "x": v.x, "y": v.y, "drill": v.drill,
                    "diameter": v.diameter, "layers": v.layers, "net": v.net,
                }
                for v in self.vias
            ],
            "nets": [
                {"name": n.get("name") or n.get("net", ""), "pins": n.get("pins", [])}
                for n in self.nets
            ],
            "power_pins": self.power_pins,
            "power_labels": self.power_labels,
            "outline_segments": self.outline_segments,
        }

    @staticmethod
    def from_dict(data: dict) -> "BoardModel":
        model = BoardModel(
            version=data.get("version", "20260206"),
            generator=data.get("generator", "circuitbot"),
            _pcbnew_content=data.get("_pcbnew_content"),
            nets=data.get("nets", []),
            power_pins=data.get("power_pins", []),
            power_labels=data.get("power_labels", []),
            outline_segments=data.get("outline_segments", []),
        )
        model.normalize_nets()
        for cd in data.get("components", []):
            pads = [
                PadDef(
                    number=p.get("number", p.get("num", "")), x=p["x"], y=p["y"],
                    width=p["width"], height=p["height"],
                    shape=p.get("shape", "rect"), type=p.get("type", "smd"),
                    rotation=p.get("rotation", 0.0), drill=p.get("drill"),
                    drill_width=p.get("drill_width"),
                    drill_offset_x=p.get("drill_offset_x", 0.0),
                    drill_offset_y=p.get("drill_offset_y", 0.0),
                    roundrect_rratio=p.get("roundrect_rratio"),
                    rect_delta_x=p.get("rect_delta_x", 0.0),
                    rect_delta_y=p.get("rect_delta_y", 0.0),
                    layers=p.get("layers", ["F.Cu", "F.Mask", "F.Paste"]),
                )
                for p in cd.get("pads", [])
            ]
            model.components.append(BoardComponent(
                ref=cd["ref"], footprint=cd.get("footprint", ""),
                x=cd["x"], y=cd["y"], rotation=cd.get("rotation", 0.0),
                layer=cd.get("layer", "F.Cu"), value=cd.get("value", ""),
                pads=pads,
                graphics=cd.get("graphics", []),
            ))
        for td in data.get("traces", []):
            path = [(p["x"], p["y"]) for p in td.get("path", [])]
            via = None
            if td.get("via"):
                via = (td["via"]["x"], td["via"]["y"])
            model.traces.append(BoardTrace(
                net=td.get("net", ""), layer=td.get("layer", "F.Cu"),
                width=td.get("width", 0.254), path=path, via=via,
            ))
        for vd in data.get("vias", []):
            model.vias.append(BoardVia(
                x=vd["x"], y=vd["y"], drill=vd.get("drill", 0.3),
                diameter=vd.get("diameter", 0.6),
                layers=vd.get("layers", ["F.Cu", "B.Cu"]), net=vd.get("net", ""),
            ))
        return model
