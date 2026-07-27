"""
CircuitJSON data models — subset of the tscircuit schema.
Defines the core structures for components, footprints, symbols, and pins.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Pin:
    """A single pin on a component."""
    number: str = ""
    name: str = ""
    type: str = ""          # input, output, bidirectional, passive, power_in, power_out
    net: str = ""
    side: str = ""          # top, bottom, left, right
    position: tuple = (0, 0)

    @classmethod
    def from_dict(cls, d: dict) -> "Pin":
        return cls(
            number=str(d.get("number", d.get("pin_number", ""))),
            name=d.get("name", d.get("pin_name", "")),
            type=d.get("type", d.get("pin_type", "")),
            net=d.get("net", ""),
            side=d.get("side", ""),
            position=(d.get("x", 0), d.get("y", 0)),
        )


@dataclass
class Symbol:
    """Schematic symbol for a component."""
    lib_id: str = ""
    library: str = ""
    name: str = ""
    pins: list = field(default_factory=list)  # list of Pin
    bbox: tuple = (0, 0, 0, 0)               # (x1, y1, x2, y2)

    @classmethod
    def from_dict(cls, d: dict) -> "Symbol":
        pins = [Pin.from_dict(p) for p in d.get("pins", [])]
        return cls(
            lib_id=d.get("lib_id", d.get("id_str", "")),
            library=d.get("library", ""),
            name=d.get("name", ""),
            pins=pins,
            bbox=tuple(d.get("bbox", (0, 0, 0, 0))),
        )


@dataclass
class Footprint:
    """PCB footprint for a component."""
    name: str = ""
    library: str = ""
    pads: list = field(default_factory=list)   # list of pad dicts
    layers: list = field(default_factory=list)  # F.Cu, B.Cu, F.SilkS, etc.
    bbox: tuple = (0, 0, 0, 0)

    @classmethod
    def from_dict(cls, d) -> "Footprint":
        if isinstance(d, str):
            return cls(name=d)
        if not isinstance(d, dict):
            return cls()
        return cls(
            name=d.get("name", d.get("footprint", "")),
            library=d.get("library", ""),
            pads=d.get("pads", []),
            layers=d.get("layers", []),
            bbox=tuple(d.get("bbox", (0, 0, 0, 0))),
        )


@dataclass
class Component:
    """A complete component with symbol, footprint, and metadata."""
    id_str: str = ""                    # e.g. "Device:R", "MCU_ESP32:ESP32-C3"
    name: str = ""                      # human-readable name
    description: str = ""
    category: str = ""                  # resistor, capacitor, ic, connector, etc.
    library: str = ""                   # source library
    symbol: Optional[Symbol] = None
    footprint: Optional[Footprint] = None
    pins: list = field(default_factory=list)  # list of Pin
    datasheet: str = ""
    manufacturer: str = ""
    mpn: str = ""                       # manufacturer part number
    tags: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Component":
        sym = Symbol.from_dict(d.get("symbol", {})) if d.get("symbol") else None
        fp = Footprint.from_dict(d.get("footprint", {})) if d.get("footprint") else None
        pins = [Pin.from_dict(p) for p in d.get("pins", [])]
        return cls(
            id_str=d.get("id_str", d.get("component_id", "")),
            name=d.get("name", d.get("component_name", "")),
            description=d.get("description", ""),
            category=d.get("category", ""),
            library=d.get("library", ""),
            symbol=sym,
            footprint=fp,
            pins=pins,
            datasheet=d.get("datasheet", ""),
            manufacturer=d.get("manufacturer", ""),
            mpn=d.get("mpn", d.get("manufacturer_part_number", "")),
            tags=d.get("tags", []),
        )

    def to_dict(self) -> dict:
        return {
            "id_str": self.id_str,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "library": self.library,
            "pins": [{"number": p.number, "name": p.name, "type": p.type} for p in self.pins],
            "datasheet": self.datasheet,
            "manufacturer": self.manufacturer,
            "mpn": self.mpn,
        }


@dataclass
class Net:
    """A net connecting multiple pins."""
    name: str = ""
    pins: list = field(default_factory=list)  # list of "ref:pin" strings

    @classmethod
    def from_dict(cls, d: dict) -> "Net":
        return cls(
            name=d.get("name", d.get("net_name", "")),
            pins=d.get("pins", []),
        )
