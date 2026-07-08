import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import rag
results = rag.search("esp32 devkit", k=5)
for idx, r in enumerate(results):
    print(f"Result {idx + 1}:")
    print(f"  id_str: {r.id_str}")
    print(f"  text: {r.text}")
    print(f"  footprint: {getattr(r, 'footprint', None)}")
    print(f"  pins: {len(r.pins)} pins")
    # print pads
    print(f"  pads: {len(r.pads) if getattr(r, 'pads', None) else 0} pads")
