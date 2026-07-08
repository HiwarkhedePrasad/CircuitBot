import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pcb_design.pcb_import import _parse_footprint, parse_sexp
from kicad_rag.store import footprint_path_for

fp_path = footprint_path_for("RF_Module:ESP32-C3-DevKitM-1")
ast = parse_sexp(fp_path.read_text(encoding="utf-8"))
if isinstance(ast, list) and ast and ast[0] not in ("footprint", "module"):
    ast[0] = "footprint"
parsed = _parse_footprint(ast)

print("Parsed footprint:", parsed.footprint)
print("Pads:")
for p in parsed.pads[:5]:
    print(f"  Pad {p.number}: x={p.x}, y={p.y}, width={p.width}, height={p.height}, shape={p.shape}, type={p.type}")
