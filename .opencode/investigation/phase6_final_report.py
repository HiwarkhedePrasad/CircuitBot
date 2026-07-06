"""Phase 6: Final Report — compile all findings into a structured summary."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INVEST_DIR = REPO_ROOT / ".opencode" / "investigation"


def load_json(name):
    return json.loads((INVEST_DIR / name).read_text(encoding="utf-8"))


def main():
    truth = load_json("phase0_source_truth.json")
    parser = load_json("phase1_parser_output.json")
    normalizer = load_json("phase2_normalized.json")
    geometry = load_json("phase3_geometry.json")
    kicanvas = load_json("phase35_kicanvas_reference.json")

    s_truth = truth["summary"]
    s_parser = parser["summary"]
    s_normalizer = normalizer["summary"]
    s_geometry = geometry["summary"]
    s_kicanvas = kicanvas["summary"]

    report = {
        "title": "PCB Renderer Investigation — Final Report",
        "target": "QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias.kicad_mod",
        "pipeline_summary": {
            "phase0_source_of_truth": {
                "total_entries": s_truth["total_pad_entries"],
                "unique_numbers": dict(sorted(s_truth["unique_pad_numbers"].items())),
                "shapes": s_truth["shapes"],
                "types": s_truth["types"],
                "roundrect_rratio_count": s_truth["roundrect_rratio_count"],
                "drill_count": s_truth["drill_count"],
                "zone_connect_count": s_truth["zone_connect_count"],
                "primitives_count": s_truth["primitives_count"],
            },
            "phase1_parser": {
                "total_input": s_parser["total_pad_nodes"],
                "parsed_ok": s_parser["parsed_successfully"],
                "field_match_truth": s_parser["fields_match_truth"],
                "field_mismatch": s_parser["fields_mismatch"],
                "fields_dropped": s_parser["fields_present_in_truth_but_not_in_PadDef"],
            },
            "phase2_normalizer": {
                "input_count": s_normalizer["input_pad_count"],
                "output_count": s_normalizer["output_pad_count"],
                "count_match": s_normalizer["count_match"],
                "per_number_match": s_normalizer["per_number_match"],
                "value_deltas": s_normalizer["value_deltas"],
            },
            "phase3_geometry": {
                "total_pads": s_geometry["totalPads"],
                "total_draw_commands": s_geometry["totalDrawCommands"],
                "draw_call_types": s_geometry["padDrawCallTypes"],
                "drill_entries": s_geometry["padsWithDrill"],
                "renderer_handles_drill_separately": True,
                "roundrect_pads": s_geometry["roundrectPadCount"],
                "roundrect_radius_issues": s_geometry["roundrectRadiusIssues"],
            },
            "phase35_kicanvas": {
                "world_position_matches": s_kicanvas["worldPositionMatches"],
                "world_position_mismatches": s_kicanvas["worldPositionMismatches"],
                "shape_rotation_matches": s_kicanvas["shapeRotationMatches"],
                "shape_rotation_mismatches": s_kicanvas["shapeRotationMismatches"],
            },
        },
        "bugs": [
            {
                "id": "B1",
                "title": "roundrect_rratio not parsed/used — hardcoded fallback is wrong",
                "severity": "high",
                "count_affected": 57,
                "description": (
                    "All 57 roundrect pads in the footprint specify `roundrect_rratio 0.25`. "
                    "The parser _parse_pad() at pcb_import.py:103 does not read this field "
                    "(it only handles at, size, layers, drill). PadDef dataclass in "
                    "board_model.py:38 has no roundrect_rratio field. drawPadShape() in "
                    "utils.js:368 hardcodes `Math.min(w, h) * 0.32` instead of using "
                    "the parsed rratio value."
                ),
                "root_cause_file": "pcb_design/pcb_import.py:130",
                "root_cause_detail": (
                    "_parse_pad() only checks keys: 'at', 'size', 'layers', 'drill'. "
                    "All other pad children (roundrect_rratio, zone_connect, property, "
                    "remove_unused_layers, clearance, solder_mask_margin, etc.) are silently skipped."
                ),
                "evidence": {
                    "expected_radius": 0.24,
                    "actual_radius": 0.3072,
                    "ratio": "0.32 vs expected 0.25",
                },
                "fix": "Add roundrect_rratio field to PadDef, parse it in _parse_pad(), use it in drawPadShape()",
            },
            {
                "id": "B2",
                "title": "PadDef missing critical fields",
                "severity": "high",
                "count_affected": 59,
                "description": (
                    "PadDef dataclass is missing fields for: roundrect_rratio (57 pads), "
                    "zone_connect (2 pads), property (18 pads), remove_unused_layers (16 pads), "
                    "clearance, solder_mask_margin, solder_paste_margin, thermal_width, "
                    "thermal_gap, options, primitives."
                ),
                "root_cause_file": "pcb_design/board_model.py:38",
                "evidence": {
                    "dropped_fields": [
                        "roundrect_rratio", "zone_connect", "property",
                        "remove_unused_layers"
                    ],
                },
                "fix": "Add all missing fields to PadDef dataclass and update _parse_pad() to read them",
            },
            {
                "id": "B3",
                "title": "Pad shape orientation differs from KiCanvas when footprint is rotated",
                "severity": "medium",
                "count_affected": "all pads when component.rotation ≠ 0",
                "description": (
                    "Our renderer computes padRotation = component.rotation + pad.rotation "
                    "(editor.js:646). KiCanvas computes shape rotation as pad.rotation ONLY "
                    "(R(-fp.rot) cancels R(fp.rot) in the transform chain). Difference is "
                    "Δ = -2 × fp.rot. When fp.rot = 90°, pads are rotated 180° opposite."
                ),
                "root_cause_file": "static/pcb_view/editor.js:646",
                "evidence": {
                    "our_formula": "padRotation = component.rotation + pad.rotation",
                    "kicanvas_formula": "padRotation = pad.at.rotation (fp.rot × -fp.rot cancels)",
                    "examples": [
                        {"fp_rot": 90, "our_rot": 90, "kicanvas_rot": 0, "diff": -180},
                        {"fp_rot": -45, "our_rot": -45, "kicanvas_rot": 45, "diff": 90},
                    ],
                },
                "note": "KiCad convention rotates pads with the footprint. Need to verify which behavior is desired.",
            },
            {
                "id": "B4",
                "title": "Duplicate pad keys cause collision in _findPadByKey",
                "severity": "medium",
                "count_affected": "pad 49 (18 instances), pad '' (9 instances)",
                "description": (
                    "buildPadKey() at utils.js:204 returns `${component.ref}:${pad.number}`. "
                    "Pad 49 has 18 instances sharing the same number; _findPadByKey at "
                    "editor.js:833 returns first match only. Hover/selection highlights "
                    "only the first instance of each duplicated pad number."
                ),
                "root_cause_file": "static/pcb_view/utils.js:204",
                "fix": "Include array index in pad key: `${component.ref}:${pad.number}[${index}]`",
            },
            {
                "id": "B5",
                "title": "Oval drill format not parsed",
                "severity": "low",
                "count_affected": "0 (no oval drills in this footprint)",
                "description": (
                    "The drill field can be a single float (round drill) or two floats "
                    "(oval drill: width, height). _parse_pad() only handles the single-float "
                    "case. Oval drills would be silently dropped."
                ),
                "root_cause_file": "pcb_design/pcb_import.py:130-132",
                "fix": "Add drill_oval_width and drill_oval_height fields to PadDef",
            },
            {
                "id": "B6",
                "title": "Hardcoded pad dimensions floor at 0.2mm",
                "severity": "low",
                "count_affected": "only if pad size < 0.2mm",
                "description": (
                    "drawPadShape() at utils.js:361-362 clamps width/height to minimum "
                    "0.2mm. While unlikely to affect real footprints, this silently "
                    "modifies geometry without warning."
                ),
                "root_cause_file": "static/pcb_view/utils.js:361-362",
            },
        ],
        "verified_correct": [
            "Parser extracts all 75 pads correctly (numeric fields match truth)",
            "Normalizer preserves count and values (0 deltas in Phase 2)",
            "World position computation matches KiCanvas (getComponentPadPosition)",
            "Drill rendering IS handled by _drawComponentPads (separate hole circle per thru-hole pad)",
            "Camera Y-flip is correct (KiCad Y-up → PIXI Y-down conversion)",
            "screenToWorld un-flips Y correctly (consistent with _applyCamera)",
            "Layer normalization works correctly (F.Cu, B.Cu, *.Cu, etc.)",
        ],
        "stage_by_stage_count_audit": {
            "sexpression": "75 pad entries (57 roundrect, 16 circle, 2 rect)",
            "parser": "75 parsed, 0 null → 4 fields dropped",
            "normalizer": "75 preserved, 0 value changes",
            "geometry_builder": "91 draw commands (75 pad shapes + 16 synthetically tracked drills)",
            "renderer": "166 expected PIXI Graphics (75 mask + 75 copper + 16 hole circles for thru-hole)",
            "children": "166 children in _footprintLayer (verified via code audit)",
        },
        "confidence_classification": {
            "Missing fields (B1, B2)": {
                "confidence": "HIGH",
                "evidence": "Phase 0 → Phase 1 field count comparison confirms dropped fields",
            },
            "Wrong roundrect radius (B1)": {
                "confidence": "HIGH",
                "evidence": "Phase 3 shows all 57 roundrect radii at 0.3072 vs expected 0.2400",
            },
            "Duplicate key collision (B4)": {
                "confidence": "HIGH",
                "evidence": "Phase 0 confirms 18 instances of pad 49 sharing the same key",
            },
            "Shape rotation (B3)": {
                "confidence": "MEDIUM",
                "evidence": "Phase 3.5 confirms Δ = -2 × fp.rot; needs KiCad behavior verification",
            },
            "Y-axis inversion": {
                "confidence": "RULED OUT",
                "evidence": "Camera Y-flip is correct; world positions match KiCanvas exactly",
            },
            "Missing thermal vias": {
                "confidence": "RULED OUT",
                "evidence": "All 16 thermal via pads are parsed and their drills are rendered by _drawComponentPads",
            },
        },
    }

    out_path = INVEST_DIR / "phase6_final_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Wrote {out_path}")
    print()
    print("=" * 68)
    print("  PCB RENDERER INVESTIGATION — FINAL REPORT")
    print("=" * 68)
    print()
    print(f"  Target: {report['target']}")
    print()
    print("  Bugs Found:")
    for bug in report["bugs"]:
        print(f"  [{bug['id']}] ({bug['severity'].upper()}) {bug['title']}")
        print(f"        Affects: {bug['count_affected']}")
        print(f"        File: {bug['root_cause_file']}")
        print()
    print(f"  Verified Correct: {len(report['verified_correct'])} items")
    for item in report["verified_correct"]:
        print(f"    ✓ {item}")
    print()
    print("  Stage-by-Stage Count Audit:")
    for stage, count in report["stage_by_stage_count_audit"].items():
        print(f"    {stage}: {count}")
    print()
    print("  Confidence Classification:")
    for issue, cls in report["confidence_classification"].items():
        print(f"    [{cls['confidence']}] {issue}")
    print()
    print("  First divergence from source of truth:")
    print("    Phase 1 (Parser): roundrect_rratio, zone_connect, property, remove_unused_layers")


if __name__ == "__main__":
    main()
