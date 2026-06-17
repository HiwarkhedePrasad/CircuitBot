import json
from agent.graph import agent_graph

prompt = "Design a simple LED blinking circuit using a NE555 timer IC in astable mode. Generate a complete electronic design including schematic, netlist, BOM, and PCB-ready component list. The circuit should operate from a 5V supply and blink a single LED at approximately 1 Hz. Include all required resistors, capacitors, power connections, decoupling capacitor, current-limiting resistor for the LED, and any components necessary for reliable operation. Verify that every component referenced in the schematic appears in the BOM and netlist, and that every net connection is electrically valid. Output the design in a structured format suitable for PCB generation."

def _emit(event, data):
    print(f"[{event}]", data.get("message", data) if isinstance(data, dict) else data)

config = {"configurable": {"emit": _emit}}
result = agent_graph.invoke({"prompt": prompt}, config)
