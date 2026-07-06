"""Phase 1: Compare parser output against source of truth.

For every (pad ...) node in the footprint, calls _parse_pad() from
pcb_design.pcb_import and records what survives and what's dropped.
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

# Import the target module
sys.path.insert(0, str(REPO_ROOT))
from pcb_design.pcb_import import _parse_pad
from pcb_design.board_model import PadDef

FOOTPRINT_PATH = REPO_ROOT / "kicad-footprints" / "Package_DFN_QFN.pretty" / "QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias.kicad_mod"


def extract_raw_pad_nodes(ast: list) -> list[list]:
    """Return all (pad ...) AST nodes in order."""
    nodes = []
    def walk(n):
        if not isinstance(n, list) or len(n) < 2:
            return
        if n[0] == "pad":
            nodes.append(n)
            return
        for child in n[1:]:
            if isinstance(child, list):
                walk(child)
    walk(ast)
    return nodes


def get_parser_fields(pad: PadDef) -> dict:
    """Convert a PadDef to a comparable dict — only fields the parser populates."""
    return {
        "number": pad.number,
        "x": pad.x,
        "y": pad.y,
        "width": pad.width,
        "height": pad.height,
        "type": pad.type,
        "shape": pad.shape,
        "rotation": pad.rotation,
        "drill": pad.drill,
        "layers": pad.layers,
    }


def main():
    raw = FOOTPRINT_PATH.read_text(encoding="utf-8")
    ast = parse_sexp(raw)

    pad_nodes = extract_raw_pad_nodes(ast)

    # Load source of truth for comparison
    truth_path = REPO_ROOT / ".opencode" / "investigation" / "phase0_source_truth.json"
    truth_data = json.loads(truth_path.read_text(encoding="utf-8"))
    truth_pads = truth_data["pads"]

    if len(pad_nodes) != len(truth_pads):
        print(f"WARNING: pad node count ({len(pad_nodes)}) != truth count ({len(truth_pads)})")
        # Pad extract might differ; we'll match by index anyway

    results = []
    mismatches = []

    for i, node in enumerate(pad_nodes):
        parsed = _parse_pad(node)
        truth = truth_pads[i] if i < len(truth_pads) else None

        entry = {
            "index": i,
            "truth_number": truth["number"] if truth else "?",
        }

        if parsed is None:
            entry["parsed"] = None
            entry["fields_match"] = False
            entry["errors"] = ["_parse_pad returned None"]
            results.append(entry)
            continue

        parsed_fields = get_parser_fields(parsed)
        entry["parsed"] = parsed_fields

        # Compare with truth
        field_diffs = {}
        if truth:
            # Fields both should have
            for key in ["number", "x", "y", "width", "height", "type", "shape", "rotation"]:
                pv = parsed_fields[key]
                tv = truth.get({
                    "number": "number",
                    "x": "at_x",
                    "y": "at_y",
                    "width": "size_w",
                    "height": "size_h",
                    "type": "type",
                    "shape": "shape",
                    "rotation": "at_rotation",
                }[key], None)
                if isinstance(tv, float) and isinstance(pv, float):
                    if abs(pv - tv) > 1e-9:
                        field_diffs[key] = {"truth": tv, "parsed": pv}
                elif pv != tv:
                    field_diffs[key] = {"truth": tv, "parsed": pv}

            # Drill comparison
            truth_drill = truth.get("drill_diameter")
            if truth_drill is not None:
                if parsed_fields.get("drill") is None:
                    field_diffs["drill"] = {"truth": truth_drill, "parsed": None}
                elif abs(parsed_fields["drill"] - truth_drill) > 1e-9:
                    field_diffs["drill"] = {"truth": truth_drill, "parsed": parsed_fields["drill"]}
            else:
                if parsed_fields.get("drill") is not None:
                    field_diffs["drill"] = {"truth": None, "parsed": parsed_fields["drill"]}

            # Layers
            truth_layers = truth.get("layers", [])
            parsed_layers = parsed_fields.get("layers", [])
            if parsed_layers != truth_layers:
                field_diffs["layers"] = {"truth": truth_layers, "parsed": parsed_layers}

            # Missing fields (fields in truth but NOT in PadDef at all)
            missing_fields = []
            for extra_key in ("roundrect_rratio", "zone_connect", "property",
                              "remove_unused_layers", "clearance", "solder_mask_margin",
                              "thermal_width", "thermal_gap", "primitives", "options"):
                if truth.get(extra_key) is not None:
                    missing_fields.append(extra_key)
            if missing_fields:
                entry["missing_fields"] = missing_fields

        entry["fields_match"] = (len(field_diffs) == 0)
        entry["diffs"] = field_diffs

        if field_diffs:
            mismatches.append(entry)

        results.append(entry)

    # Summary
    total = len(results)
    parsed_ok = sum(1 for r in results if r["parsed"] is not None)
    field_match = sum(1 for r in results if r.get("fields_match"))
    field_mismatch = sum(1 for r in results if r.get("parsed") and not r["fields_match"])
    null_parse = sum(1 for r in results if r["parsed"] is None)

    missing_fields_all = set()
    for r in results:
        for mf in r.get("missing_fields", []):
            missing_fields_all.add(mf)

    output = {
        "summary": {
            "total_pad_nodes": total,
            "parsed_successfully": parsed_ok,
            "fields_match_truth": field_match,
            "fields_mismatch": field_mismatch,
            "null_parse": null_parse,
            "field_value_mismatches": len(mismatches),
            "fields_present_in_truth_but_not_in_PadDef": sorted(missing_fields_all),
        },
        "pads": results,
    }

    out_path = REPO_ROOT / ".opencode" / "investigation" / "phase1_parser_output.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"\nSummary:")
    print(f"  Total pad nodes in footprint: {total}")
    print(f"  Parsed successfully: {parsed_ok}")
    print(f"  Fields match truth: {field_match}")
    print(f"  Fields mismatch: {field_mismatch}")
    print(f"  Null parse: {null_parse}")
    print(f"  Field value mismatches: {len(mismatches)}")
    print(f"  Fields in truth but NOT in PadDef: {sorted(missing_fields_all)}")
    if mismatches:
        print(f"\nFirst mismatch:")
        m = mismatches[0]
        print(f"  Pad index {m['index']} (number={m['truth_number']}): {m['diffs']}")


if __name__ == "__main__":
    main()
