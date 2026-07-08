import sys
sys.path.append('.')

import json
from agent.nodes.pcb_layout import _hydrate_component_for_pcb

# Let's mock a component dictionary that would be generated
comp_mocker = {
    "ref_des": "J1",
    "footprint": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
    "id_str": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
}

pads, graphics, footprint = _hydrate_component_for_pcb(comp_mocker)

print(f"Footprint: {footprint}")
print("Pads:")
for p in pads[:5]:
    print(f"  Pad {p.number}: x={p.x}, y={p.y}")

print("Graphics:")
for g in graphics[:5]:
    print(g)
