"""Shared internal geometry model for PCB boards.

This is the single source of truth for all board data:
- Import: .kicad_pcb → BoardModel
- Agent output: placement + routing → BoardModel
- Frontend: BoardModel → PCB viewer renderer
- Export: BoardModel → .kicad_pcb
"""

from __future__ import annotations

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
    layers: list[str] = field(default_factory=lambda: ["F.Cu", "F.Mask", "F.Paste"])

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

    components: list[BoardComponent] = field(default_factory=list)
    traces: list[BoardTrace] = field(default_factory=list)
    vias: list[BoardVia] = field(default_factory=list)
    zones: list[BoardZone] = field(default_factory=list)

    nets: list[dict] = field(default_factory=list)
    power_pins: list[dict] = field(default_factory=list)
    power_labels: list[dict] = field(default_factory=list)

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
                "pads": [
                    {
                        "number": p.number, "x": p.x, "y": p.y,
                        "width": p.width, "height": p.height,
                        "shape": p.shape, "type": p.type,
                        "rotation": p.rotation, "drill": p.drill,
                        "layers": p.layers,
                    }
                    for p in c.pads
                ],
            }

        return {
            "version": self.version,
            "generator": self.generator,
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
            "nets": self.nets,
            "power_pins": self.power_pins,
            "power_labels": self.power_labels,
        }

    @staticmethod
    def from_dict(data: dict) -> "BoardModel":
        model = BoardModel(
            version=data.get("version", "20260206"),
            generator=data.get("generator", "circuitbot"),
            nets=data.get("nets", []),
            power_pins=data.get("power_pins", []),
            power_labels=data.get("power_labels", []),
        )
        for cd in data.get("components", []):
            pads = [
                PadDef(
                    number=p["number"], x=p["x"], y=p["y"],
                    width=p["width"], height=p["height"],
                    shape=p.get("shape", "rect"), type=p.get("type", "smd"),
                    rotation=p.get("rotation", 0.0), drill=p.get("drill"),
                    layers=p.get("layers", ["F.Cu", "F.Mask", "F.Paste"]),
                )
                for p in cd.get("pads", [])
            ]
            model.components.append(BoardComponent(
                ref=cd["ref"], footprint=cd.get("footprint", ""),
                x=cd["x"], y=cd["y"], rotation=cd.get("rotation", 0.0),
                layer=cd.get("layer", "F.Cu"), value=cd.get("value", ""),
                pads=pads,
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
