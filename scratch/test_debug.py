import sys
sys.path.append('.')

from pathlib import Path
from pcb_design.pcb_import import _parse_footprint, parse_sexp

fp_path = Path("kicad-footprints/Connector_PinHeader_2.54mm.pretty/PinHeader_2x15_P2.54mm_Vertical.kicad_mod")
ast = parse_sexp(fp_path.read_text(encoding="utf-8"))
if isinstance(ast, list) and ast and ast[0] not in ("footprint", "module"):
    ast[0] = "footprint"
comp = _parse_footprint(ast)

print("All Graphics:")
for item in comp.graphics:
    if item['kind'] == 'fp_line':
        print(f"  Line: start={item['start']}, end={item['end']}, layer={item['layer']}")
