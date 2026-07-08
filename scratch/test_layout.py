import sys
sys.path.append('.')

import json
from agent.nodes.pcb_layout import pcb_layout_node

state = {
    "selected_components": [
        {
            "id": "J1",
            "ref_des": "J1",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
            "id_str": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
        }
    ],
    "netlist": []
}

config = {
    "configurable": {
        "emit": lambda *args: None,
        "emit_assistant_message": lambda *args: None,
        "emit_tool_event": lambda *args: None
    }
}

# Run pcb_layout_node
res = pcb_layout_node(state, config)

if isinstance(res, dict):
    board = res["board_model"]
else:
    board = res.to_dict()

# Save to file
with open("scratch/placed_board.json", "w") as f:
    json.dump(board, f, indent=2)

print("Placed J1 component:")
comp = board["components"][0]
print(f"  Ref: {comp['ref']}")
print(f"  Footprint: {comp['footprint']}")
print(f"  Rotation: {comp['rotation']}")
print(f"  Pads count: {len(comp['pads'])}")
if comp['pads']:
    print("  First 3 pads:")
    for p in comp['pads'][:3]:
        print(f"    Pad {p['number']}: x={p['x']}, y={p['y']}")
print(f"  Graphics count: {len(comp['graphics'])}")
if comp['graphics']:
    print("  First 3 graphics:")
    for g in comp['graphics'][:3]:
        print(f"    Graphic: {g}")
