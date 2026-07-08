import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import _build_component_proposal_from_query
import json

prop = _build_component_proposal_from_query("esp32 devkit")
print("Proposal name:", prop["component"]["name"])
print("Footprint:", prop["component"]["footprint"])
print("Pads count:", len(prop["component"]["pins"]))
print("Pads:")
for p in prop["component"]["pins"][:5]:
    print(f"  Pad {p.get('num')}: x={p.get('x')}, y={p.get('y')}, width={p.get('width')}, height={p.get('height')}, name={p.get('name')}")
