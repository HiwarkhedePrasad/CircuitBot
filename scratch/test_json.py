import json

with open("test_e2e_result.json", "r") as f:
    data = json.load(f)

events = data.get("events", [])
for event in events:
    if len(event) > 1 and event[0] == "agent:layout_ready":
        board = event[1].get("board_model", {})
        components = board.get("components", [])
        print(f"Event agent:layout_ready has {len(components)} components:")
        for comp in components:
            ref = comp.get("ref", "")
            pads = comp.get("pads", [])
            graphics = comp.get("graphics", [])
            print(f"  Component {ref}: x={comp.get('x')}, y={comp.get('y')}, rot={comp.get('rotation')}, footprint={comp.get('footprint')}")
            print(f"    Pads count: {len(pads)}")
            if pads:
                print(f"      First pad: num={pads[0].get('number')}, x={pads[0].get('x')}, y={pads[0].get('y')}")
                print(f"      Last pad: num={pads[-1].get('number')}, x={pads[-1].get('x')}, y={pads[-1].get('y')}")
            print(f"    Graphics count: {len(graphics)}")
            for item in graphics[:3]:
                print(f"      Graphic item: {item}")
