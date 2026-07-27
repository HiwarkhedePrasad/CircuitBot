---
name: circuitbot-modify
description: Modification classification knowledge for the CircuitBot modify stage. Teaches how to classify user modification requests into types (value_change, part_swap, add_component, remove_component, net_modify, reroute) and extract targets and values. Trigger on: modify stage LLM calls, design modification classification, component change requests, user refinement intents.
---

# CircuitBot Modify Knowledge

Classify user modification requests into exactly one type and extract the target and value.

## Modification Types

### value_change
Change a component's value without changing the part.
- "Change R1 to 10k" -> target: R1, value: "10k"
- "Set C1 to 100nF" -> target: C1, value: "100nF"
- "Update the resistor value to 4.7k" -> target: R1 (best guess), value: "4.7k"
- "Make the pull-ups 10k instead of 4.7k" -> target: R1,R2 (all pull-ups), value: "10k"

### part_swap
Replace one part number with another.
- "Swap U1 for MCP1700" -> target: U1, value: "MCP1700"
- "Replace the ESP32 with an RP2040" -> target: U1 (the MCU), value: "RP2040"
- "Use TMP117 instead of DS18B20" -> target: U2 (the temp sensor), value: "TMP117"

### add_component
Add a new component to the design.
- "Add a 100nF bypass cap on VCC" -> target: VCC net, value: "100nF bypass capacitor"
- "Add a status LED" -> target: description "status indicator", value: "LED with resistor"
- "Add a 10k pull-up on SDA" -> target: SDA net, value: "10k pull-up resistor"
- "I need a programming header" -> target: description, value: "6-pin programming header"

### remove_component
Remove an existing component.
- "Remove R3" -> target: R3, value: empty
- "Get rid of the USB-UART bridge" -> target: U3 (the CP2102N), value: empty
- "Remove the LED, I don"t need it" -> target: D1 (the LED), value: empty

### net_modify
Change a connection between components.
- "Connect LED to pin 13 instead" -> target: LED, value: pin "13"
- "Move the sensor to I2C bus 1" -> target: sensor component, value: "I2C1"
- "Change the interrupt pin to GPIO4" -> target: the INT net, value: "GPIO4"

### reroute
Change trace routing or PCB layout parameters.
- "Make the power traces wider" -> target: VCC/VDD net, value: trace_width "0.5mm"
- "Route the USB lines as differential pair" -> target: USB D+/D-, value: "differential pair"
- "Increase clearance on high-voltage nets" -> target: all power nets, value: clearance "0.5mm"
- "Add thermal relief" -> target: power component, value: "thermal vias"

## Classification Rules

- If the user says CHANGE, SET, UPDATE, SWAP, REPLACE, REMOVE, ADJUST -> likely modify_design
- If value is just a number with units (10k, 100nF) -> value_change
- If value is a different part number (MCP1700, RP2040, TMP117) -> part_swap
- If user mentions ADD, PLACE, INSERT, PUT -> add_component (NOT design_pipeline when we"re in modify context)
- If user mentions REMOVE, DELETE, DROP, GET RID OF -> remove_component
- If user mentions CONNECT, ROUTE, WIRE -> net_modify or reroute
- If user mentions WIDER, THICKER, CLEARANCE, DIFFERENTIAL -> reroute

## Target Extraction
- Ref des (R1, C2, U3, D1) -> use directly as target ref
- Net name (VCC, GND, SDA, 3V3) -> use as target net
- Generic description ("the LED", "the sensor", "the resistor") -> use as target description, the system will match
- When multiple matching components exist, the system picks the most likely one
