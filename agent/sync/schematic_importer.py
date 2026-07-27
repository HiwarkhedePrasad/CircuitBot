"""KiCad .kicad_sch file importer.

Parses a .kicad_sch file using the vendored S-expression parser
and returns structured data for bidirectional sync.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from kicad_rag.constants import UTILS_ROOT


def _syspath() -> None:
    p = str(UTILS_ROOT / "common")
    if p not in sys.path:
        sys.path.insert(0, p)


_syspath()
from sexpr import parse_sexp  # noqa: E402


# ── AST helpers (same pattern as pcb_import.py) ────────────────────────────

def _find_all(node: list, *path: str) -> list[list]:
    """Recursively find all sub-lists matching a path of keywords."""
    results: list[list] = []

    def _walk(n: list, depth: int = 0) -> None:
        if not isinstance(n, list) or not n:
            return
        if depth == len(path):
            results.append(n)
            return
        if isinstance(n[0], str) and n[0] == path[depth]:
            _walk(n, depth + 1)
        for child in n[1:]:
            if isinstance(child, list):
                _walk(child, 0)

    _walk(node)
    return results


def _find_one(node: list, *path: str) -> Optional[list]:
    found = _find_all(node, *path)
    return found[0] if found else None


def _direct_children(node: list, *names: str) -> list[list]:
    """Return direct child lists whose head matches one of *names*."""
    wanted = set(names)
    return [
        child for child in node[1:]
        if isinstance(child, list) and child
        and isinstance(child[0], str) and child[0] in wanted
    ]


def _get_str(n: list, idx: int = 1, default: str = "") -> str:
    return str(n[idx]) if len(n) > idx and n[idx] is not None else default


def _get_float(n: list, idx: int = 1, default: float = 0.0) -> float:
    try:
        return float(n[idx])
    except (IndexError, TypeError, ValueError):
        return default


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ImportedComponent:
    ref: str
    lib_id: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float
    uuid: str
    in_bom: bool = True
    on_board: bool = True
    properties: dict = field(default_factory=dict)


@dataclass
class ImportedWire:
    x1: float
    y1: float
    x2: float
    y2: float
    uuid: str = ""


@dataclass
class ImportedLabel:
    text: str
    x: float
    y: float
    rotation: float
    label_type: str  # "label", "global_label", "hierarchical_label"
    uuid: str = ""


@dataclass
class ImportedPowerSymbol:
    ref: str
    value: str  # net name (VCC, GND, etc.)
    lib_id: str
    x: float
    y: float
    rotation: float
    uuid: str = ""


@dataclass
class ImportedNoConnect:
    x: float
    y: float
    uuid: str = ""


@dataclass
class ImportedSheet:
    name: str
    filename: str
    x: float
    y: float
    width: float
    height: float
    uuid: str = ""
    pins: list = field(default_factory=list)


@dataclass
class SchematicImport:
    components: list[ImportedComponent] = field(default_factory=list)
    lib_symbols: dict[str, Any] = field(default_factory=dict)
    wires: list[ImportedWire] = field(default_factory=list)
    labels: list[ImportedLabel] = field(default_factory=list)
    power_symbols: list[ImportedPowerSymbol] = field(default_factory=list)
    no_connects: list[ImportedNoConnect] = field(default_factory=list)
    junctions: list[tuple[float, float]] = field(default_factory=list)
    sheets: list[ImportedSheet] = field(default_factory=list)


# ── Parsing functions ───────────────────────────────────────────────────────

def _parse_symbol_instance(node: list) -> Optional[ImportedComponent]:
    """Parse a (symbol ...) instance node."""
    # Find lib_id
    lib_id_node = _find_one(node, "lib_id")
    lib_id = _get_str(lib_id_node) if lib_id_node else ""

    # Find at position
    at_node = _find_one(node, "at")
    x, y = 0.0, 0.0
    rotation = 0.0
    if at_node:
        x = _get_float(at_node, 1)
        y = _get_float(at_node, 2)
        if len(at_node) > 3:
            rotation = _get_float(at_node, 3)

    # Find uuid
    uuid_node = _find_one(node, "uuid")
    uuid = _get_str(uuid_node) if uuid_node else ""

    # Find properties
    value = ""
    footprint = ""
    ref = ""
    in_bom = True
    on_board = True
    properties: dict[str, str] = {}

    for prop in _direct_children(node, "property"):
        prop_name = _get_str(prop, 1)
        prop_value = _get_str(prop, 2)
        properties[prop_name] = prop_value

        if prop_name == "Value":
            value = prop_value
        elif prop_name == "Footprint":
            footprint = prop_value
        elif prop_name == "Reference":
            ref = prop_value

    # Check in_bom / on_board attributes
    for attr in _direct_children(node, "in_bom", "on_board", "dnp"):
        attr_name = attr[0]
        attr_val = _get_str(attr, 1).lower() if len(attr) > 1 else "yes"
        if attr_name == "in_bom":
            in_bom = attr_val not in ("no", "false", "0")
        elif attr_name == "on_board":
            on_board = attr_val not in ("no", "false", "0")

    if not ref:
        return None

    return ImportedComponent(
        ref=ref,
        lib_id=lib_id,
        value=value,
        footprint=footprint,
        x=x,
        y=y,
        rotation=rotation,
        uuid=uuid,
        in_bom=in_bom,
        on_board=on_board,
        properties=properties,
    )


def _parse_wire(node: list) -> Optional[ImportedWire]:
    """Parse a (wire (pts ...)) node."""
    pts_node = _find_one(node, "pts")
    if not pts_node:
        return None

    points = []
    for xy in _direct_children(pts_node, "xy"):
        px = _get_float(xy, 1)
        py = _get_float(xy, 2)
        points.append((px, py))

    if len(points) < 2:
        return None

    uuid_node = _find_one(node, "uuid")
    uuid = _get_str(uuid_node) if uuid_node else ""

    return ImportedWire(
        x1=points[0][0], y1=points[0][1],
        x2=points[-1][0], y2=points[-1][1],
        uuid=uuid,
    )


def _parse_label(node: list, label_type: str) -> Optional[ImportedLabel]:
    """Parse a (label ...) or (global_label ...) node."""
    text = _get_str(node, 1)

    at_node = _find_one(node, "at")
    x, y = 0.0, 0.0
    rotation = 0.0
    if at_node:
        x = _get_float(at_node, 1)
        y = _get_float(at_node, 2)
        if len(at_node) > 3:
            rotation = _get_float(at_node, 3)

    uuid_node = _find_one(node, "uuid")
    uuid = _get_str(uuid_node) if uuid_node else ""

    return ImportedLabel(
        text=text,
        x=x, y=y,
        rotation=rotation,
        label_type=label_type,
        uuid=uuid,
    )


def _parse_no_connect(node: list) -> Optional[ImportedNoConnect]:
    """Parse a (no_connect ...) node."""
    at_node = _find_one(node, "at")
    if not at_node:
        return None

    x = _get_float(at_node, 1)
    y = _get_float(at_node, 2)

    uuid_node = _find_one(node, "uuid")
    uuid = _get_str(uuid_node) if uuid_node else ""

    return ImportedNoConnect(x=x, y=y, uuid=uuid)


def _parse_junction(node: list) -> Optional[tuple[float, float]]:
    """Parse a (junction ...) node."""
    at_node = _find_one(node, "at")
    if not at_node:
        return None
    return (_get_float(at_node, 1), _get_float(at_node, 2))


def _parse_sheet(node: list) -> Optional[ImportedSheet]:
    """Parse a (sheet ...) node."""
    name = _get_str(node, 1)

    at_node = _find_one(node, "at")
    x, y = 0.0, 0.0
    if at_node:
        x = _get_float(at_node, 1)
        y = _get_float(at_node, 2)

    size_node = _find_one(node, "size")
    width, height = 0.0, 0.0
    if size_node:
        width = _get_float(size_node, 1)
        height = _get_float(size_node, 2)

    # Get filename from property
    filename = ""
    for prop in _direct_children(node, "property"):
        if _get_str(prop, 1) == "Sheetfile":
            filename = _get_str(prop, 2)

    uuid_node = _find_one(node, "uuid")
    uuid = _get_str(uuid_node) if uuid_node else ""

    # Get sheet pins
    pins = []
    for pin in _direct_children(node, "pin"):
        pin_name = _get_str(pin, 1)
        pin_type = _get_str(pin, 2) if len(pin) > 2 else ""
        pins.append({"name": pin_name, "type": pin_type})

    return ImportedSheet(
        name=name, filename=filename,
        x=x, y=y, width=width, height=height,
        uuid=uuid, pins=pins,
    )


def _parse_lib_symbols(node: list) -> dict[str, Any]:
    """Parse the (lib_symbols ...) section into a dict of symbol definitions."""
    symbols: dict[str, Any] = {}
    for child in node[1:]:
        if isinstance(child, list) and child and isinstance(child[0], str):
            sym_name = child[0]
            symbols[sym_name] = child
    return symbols


# ── Main import function ────────────────────────────────────────────────────

def import_schematic(path: str | Path) -> SchematicImport:
    """Parse a .kicad_sch file and return structured data.

    Args:
        path: Path to the .kicad_sch file.

    Returns:
        SchematicImport with all parsed elements.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    ast = parse_sexp(text)

    if not isinstance(ast, list) or not ast:
        raise ValueError("Empty or invalid schematic file")

    root_tag = ast[0]
    if root_tag != "kicad_sch":
        raise ValueError(f"Expected root 'kicad_sch', got '{root_tag}'")

    result = SchematicImport()

    # Parse lib_symbols
    lib_symbols_node = _find_one(ast, "lib_symbols")
    if lib_symbols_node:
        result.lib_symbols = _parse_lib_symbols(lib_symbols_node)

    # Parse symbol instances (components)
    for sym_node in _direct_children(ast, "symbol"):
        comp = _parse_symbol_instance(sym_node)
        if comp:
            # Separate power symbols (#PWR) from regular components
            if comp.ref.startswith("#PWR"):
                result.power_symbols.append(ImportedPowerSymbol(
                    ref=comp.ref,
                    value=comp.value,
                    lib_id=comp.lib_id,
                    x=comp.x, y=comp.y,
                    rotation=comp.rotation,
                    uuid=comp.uuid,
                ))
            else:
                result.components.append(comp)

    # Parse wires
    for wire_node in _direct_children(ast, "wire"):
        wire = _parse_wire(wire_node)
        if wire:
            result.wires.append(wire)

    # Also parse bus wires
    for bus_node in _direct_children(ast, "bus"):
        wire = _parse_wire(bus_node)
        if wire:
            result.wires.append(wire)

    # Parse labels
    for label_node in _direct_children(ast, "label"):
        label = _parse_label(label_node, "label")
        if label:
            result.labels.append(label)

    for label_node in _direct_children(ast, "global_label"):
        label = _parse_label(label_node, "global_label")
        if label:
            result.labels.append(label)

    for label_node in _direct_children(ast, "hierarchical_label"):
        label = _parse_label(label_node, "hierarchical_label")
        if label:
            result.labels.append(label)

    # Parse no_connects
    for nc_node in _direct_children(ast, "no_connect"):
        nc = _parse_no_connect(nc_node)
        if nc:
            result.no_connects.append(nc)

    # Parse junctions
    for junc_node in _direct_children(ast, "junction"):
        pos = _parse_junction(junc_node)
        if pos:
            result.junctions.append(pos)

    # Parse sheets
    for sheet_node in _direct_children(ast, "sheet"):
        sheet = _parse_sheet(sheet_node)
        if sheet:
            result.sheets.append(sheet)

    return result
