"""Phase 0: Source-of-truth extraction from .kicad_mod file.

Parses the QFN-48 footprint using sexpr.py and extracts every (pad ...) entry
verbatim with all fields. This is the canonical reference table.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Ensure sexpr.py is importable
UTILS_ROOT = str(REPO_ROOT / "kicad-library-utils" / "common")
if UTILS_ROOT not in sys.path:
    sys.path.insert(0, UTILS_ROOT)
from sexpr import parse_sexp

# Target footprint
FOOTPRINT_PATH = REPO_ROOT / "kicad-footprints" / "Package_DFN_QFN.pretty" / "QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias.kicad_mod"


def extract_field(node: list, key: str) -> list:
    """Return direct children whose first element equals *key*."""
    return [c for c in node if isinstance(c, list) and len(c) > 0 and c[0] == key]


def find_key(node: list, key: str) -> list | None:
    """Return the first direct child with the given key, or None."""
    for c in node:
        if isinstance(c, list) and len(c) > 0 and c[0] == key:
            return c
    return None


def extract_pads(ast: list) -> list[dict]:
    """Walk the AST and return a list of pad dicts with ALL fields."""
    pads = []

    def walk(n):
        if not isinstance(n, list) or len(n) < 2:
            return
        if n[0] == "pad":
            pad = extract_single_pad(n)
            if pad:
                pads.append(pad)
        for child in n[1:]:
            walk(child)

    walk(ast)
    return pads


def extract_single_pad(node: list) -> dict:
    """Extract every field from a single (pad ...) S-expr node."""
    entry = {
        "_index": None,  # filled in later
        "number": str(node[1]) if len(node) > 1 else "",
        "type": str(node[2]) if len(node) > 2 else "",
        "shape": str(node[3]) if len(node) > 3 else "",
    }

    for i, child in enumerate(node):
        if not isinstance(child, list) or len(child) == 0:
            continue
        key = child[0]

        if key == "at":
            entry["at_x"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else 0.0
            entry["at_y"] = float(child[2]) if len(child) > 2 and isinstance(child[2], (int, float)) else 0.0
            entry["at_rotation"] = float(child[3]) if len(child) > 3 and isinstance(child[3], (int, float)) else 0.0

        elif key == "size":
            entry["size_w"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else 0.0
            entry["size_h"] = float(child[2]) if len(child) > 2 and isinstance(child[2], (int, float)) else 0.0

        elif key == "layers":
            entry["layers"] = [str(s) for s in child[1:]]

        elif key == "drill":
            if len(child) > 1 and isinstance(child[1], (int, float)):
                entry["drill_diameter"] = float(child[1])
                entry["drill_oval_w"] = None
                entry["drill_oval_h"] = None
            elif len(child) > 2 and isinstance(child[1], (int, float)) and isinstance(child[2], (int, float)):
                # oval drill: (drill w h)
                entry["drill_diameter"] = None
                entry["drill_oval_w"] = float(child[1])
                entry["drill_oval_h"] = float(child[2])

        elif key == "roundrect_rratio":
            entry["roundrect_rratio"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "zone_connect":
            entry["zone_connect"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "property":
            entry["property"] = str(child[1]) if len(child) > 1 else None

        elif key == "remove_unused_layers":
            entry["remove_unused_layers"] = str(child[1]) if len(child) > 1 else "yes"

        elif key == "clearance":
            entry["clearance"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "solder_mask_margin":
            entry["solder_mask_margin"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "solder_paste_margin":
            entry["solder_paste_margin"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "solder_paste_margin_ratio":
            entry["solder_paste_margin_ratio"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "thermal_width":
            entry["thermal_width"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "thermal_gap":
            entry["thermal_gap"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "thermal_bridge_angle":
            entry["thermal_bridge_angle"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "options":
            option_children = extract_field(child[1:], "clearance")
            entry["options"] = {}
            for oc in child[1:]:
                if isinstance(oc, list) and len(oc) > 0:
                    entry["options"][oc[0]] = " ".join(str(x) for x in oc[1:]) if len(oc) > 1 else True

        elif key == "primitives":
            prims = []
            for prim_child in child[1:]:
                if isinstance(prim_child, list) and len(prim_child) > 0:
                    prim_type = prim_child[0]
                    prim_data = {"type": prim_type}
                    for pc in prim_child[1:]:
                        if isinstance(pc, list) and len(pc) > 0:
                            prim_data[pc[0]] = " ".join(str(x) for x in pc[1:])
                    prims.append(prim_data)
            entry["primitives"] = prims

        elif key == "rect_delta":
            entry["rect_delta"] = [float(child[1]), float(child[2])] if len(child) > 2 else None

        elif key == "chamfer_ratio":
            entry["chamfer_ratio"] = float(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None

        elif key == "chamfer":
            entry["chamfer"] = [str(s) for s in child[1:]]

        elif key == "net":
            entry["net_id"] = int(child[1]) if len(child) > 1 and isinstance(child[1], (int, float)) else None
            entry["net_name"] = str(child[2]) if len(child) > 2 else None

        elif key == "tstamp" or key == "uuid":
            entry[key] = str(child[1]) if len(child) > 1 else None

    if "roundrect_rratio" not in entry:
        entry["roundrect_rratio"] = None
    if "zone_connect" not in entry:
        entry["zone_connect"] = None
    if "property" not in entry:
        entry["property"] = None
    if "remove_unused_layers" not in entry:
        entry["remove_unused_layers"] = None
    if "layers" not in entry:
        entry["layers"] = []

    return entry


def main():
    raw = FOOTPRINT_PATH.read_text(encoding="utf-8")
    ast = parse_sexp(raw)

    pads = extract_pads(ast)

    # Assign indices
    for i, pad in enumerate(pads):
        pad["_index"] = i

    # Build summary
    summary = {
        "footprint": str(FOOTPRINT_PATH),
        "total_pad_entries": len(pads),
        "unique_pad_numbers": {},
    }

    # Count by number
    num_counts = {}
    shape_counts = {}
    type_counts = {}
    rratio_count = 0
    drill_count = 0
    zone_connect_count = 0
    primitives_count = 0
    options_count = 0
    property_count = 0
    remove_unused_count = 0

    for pad in pads:
        num = pad["number"]
        num_counts[num] = num_counts.get(num, 0) + 1
        shape = pad.get("shape", "")
        ptype = pad.get("type", "")
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
        if pad.get("roundrect_rratio") is not None:
            rratio_count += 1
        if pad.get("drill_diameter") is not None or pad.get("drill_oval_w") is not None:
            drill_count += 1
        if pad.get("zone_connect") is not None:
            zone_connect_count += 1
        if pad.get("primitives") is not None:
            primitives_count += 1
        if pad.get("options") is not None:
            options_count += 1
        if pad.get("property") is not None:
            property_count += 1
        if pad.get("remove_unused_layers") is not None:
            remove_unused_count += 1

    summary["unique_pad_numbers"] = {k: v for k, v in sorted(num_counts.items())}
    summary["shapes"] = shape_counts
    summary["types"] = type_counts
    summary["roundrect_rratio_count"] = rratio_count
    summary["drill_count"] = drill_count
    summary["zone_connect_count"] = zone_connect_count
    summary["primitives_count"] = primitives_count
    summary["options_count"] = options_count
    summary["property_count"] = property_count
    summary["remove_unused_layers_count"] = remove_unused_count

    # Unrecognized keys per pad
    all_keys = set()
    for pad in pads:
        all_keys.update(pad.keys())

    output = {
        "summary": summary,
        "all_keys_found": sorted(all_keys),
        "pads": pads,
    }

    out_path = REPO_ROOT / ".opencode" / "investigation" / "phase0_source_truth.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"\nSummary:")
    print(f"  Total pad entries: {summary['total_pad_entries']}")
    print(f"  Unique pad numbers: {dict(sorted(num_counts.items()))}")
    print(f"  Shapes: {shape_counts}")
    print(f"  Types: {type_counts}")
    print(f"  roundrect_rratio: {rratio_count}")
    print(f"  Drills: {drill_count}")
    print(f"  zone_connect: {zone_connect_count}")
    print(f"  primitives: {primitives_count}")
    print(f"  options: {options_count}")
    print(f"  property: {property_count}")
    print(f"  remove_unused_layers: {remove_unused_count}")


if __name__ == "__main__":
    main()
