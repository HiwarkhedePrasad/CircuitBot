"""Parse example.kicad_pcb using the project's existing pcb_import parser
and write the result to pcb_parse_.json."""

import json
import sys
from pathlib import Path

# Add project root and dependencies to sys.path so we can import
# the existing parser modules without modifying code outside website/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "kicad-library-utils" / "common"))

from pcb_design.pcb_import import import_board  # noqa: E402

PCB_FILE = Path(__file__).resolve().parent / "example.kicad_pcb"
OUT_FILE = Path(__file__).resolve().parent / "pcb_parse_.json"


def _fix_component_refs(data: dict) -> None:
    """Extract ref/value from fp_text graphics when the parser missed them."""
    for comp in data.get("components", []):
        # If ref looks like a footprint path, try to find the real ref in graphics
        if "/" in comp["ref"] or comp["ref"] == comp["footprint"]:
            for g in comp.get("graphics", []):
                if g.get("kind") == "fp_text" and g.get("name") == "reference":
                    comp["ref"] = g["text"]
                    break
        # Same for value
        if not comp.get("value") or comp["value"] == comp["footprint"]:
            for g in comp.get("graphics", []):
                if g.get("kind") == "fp_text" and g.get("name") == "value":
                    comp["value"] = g["text"]
                    break


def main():
    print(f"Parsing {PCB_FILE} ...")
    model = import_board(str(PCB_FILE))
    data = model.to_dict()
    _fix_component_refs(data)

    # Remove the huge raw PCB text — it's redundant in the JSON
    data.pop("_pcbnew_content", None)

    # BoardModel.to_dict() doesn't serialize zones — add them manually
    data["zones"] = [
        {"net": z.net, "layer": z.layer, "priority": z.priority}
        for z in model.zones
    ]

    # Remove outline (shapely Polygon, not serializable)
    data.pop("outline", None)

    OUT_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT_FILE}  ({OUT_FILE.stat().st_size:,} bytes)")
    print(f"  components: {len(data.get('components', []))}")
    print(f"  traces:     {len(data.get('traces', []))}")
    print(f"  vias:       {len(data.get('vias', []))}")
    print(f"  nets:       {len(data.get('nets', []))}")
    print(f"  zones:      {len(data.get('zones', []))}")


if __name__ == "__main__":
    main()
