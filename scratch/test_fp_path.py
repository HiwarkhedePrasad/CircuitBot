import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kicad_rag.store import footprint_path_for

fp = "RF_Module:ESP32-C3-DevKitM-1"
p = footprint_path_for(fp)
print(f"Footprint: {fp}")
print(f"Path: {p}")
print(f"Exists: {p.is_file() if p else False}")
