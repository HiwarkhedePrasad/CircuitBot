import sys
sys.path.append('.')

from server import app, LAST_DESIGN

# Mock LAST_DESIGN
LAST_DESIGN.clear()
LAST_DESIGN.update({
    "selected_components": [
        {
            "id": "J1",
            "ref_des": "J1",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
            "id_str": "Connector_PinHeader_2.54mm:PinHeader_2x15_P2.54mm_Vertical",
        }
    ],
    "component_placements": [
        {
            "ref_des": "J1",
            "x": 30.48,
            "y": 20.32,
            "rotation": 0.0
        }
    ],
    "wire_paths": []
})

client = app.test_client()
res = client.get('/api/pcb_enriched_board_model')
print("Status Code:", res.status_code)
data = res.get_json()

if "board_model" in data:
    comp = data["board_model"]["components"][0]
    print("Enriched J1:")
    print("  Ref:", comp["ref"])
    print("  X:", comp["x"], "Y:", comp["y"])
    print("  Pads count:", len(comp["pads"]))
    for p in comp["pads"][:3]:
        print(f"    Pad {p['number']}: x={p['x']}, y={p['y']}")
    print("  Graphics count:", len(comp["graphics"]))
    for g in comp["graphics"][:5]:
        print(f"    Graphic kind={g['kind']}: start={g.get('start')}, end={g.get('end')}, x={g.get('x')}, y={g.get('y')}")
else:
    print("Error:", data)
