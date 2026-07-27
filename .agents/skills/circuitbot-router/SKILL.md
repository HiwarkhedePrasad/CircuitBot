---
name: circuitbot-router
description: Intent classification knowledge for the CircuitBot prompt_router stage. Teaches how to classify user messages into add_component, design_pipeline, modify_design, component_query, help, or other. Covers edge cases, compound requests, and fuzzy boundaries. Trigger on: prompt_router stage LLM calls, user intent classification, routing decisions between add_component vs design_pipeline.
---

# CircuitBot Router Knowledge

Classify user messages into the correct intent category. The four hardest categories to distinguish are `add_component`, `design_pipeline`, `modify_design`, and `component_query`.

## Category Definitions

### add_component vs design_pipeline
This is the most common ambiguity. The key distinction:
- **add_component**: User wants to ADD one or a few specific parts to an existing or new board. The focus is on the part, not the full system.
  - "add a 10k resistor" -- component-level, single part
  - "add a 100nF capacitor and an LED" -- multiple parts but still component-level
  - "add an ATMega328P" -- component-level even though it's an IC
  
- **design_pipeline**: User wants a FULL circuit/system designed from scratch. Multiple functions, interconnected subsystems.
  - "design a fan controller PCB" -- system-level, multiple functions
  - "create a power supply circuit" -- system-level
  - "build a multi-channel environmental monitor with sensors and display" -- system-level
  - "make a USB-to-UART adapter" -- system-level (single function but needs multiple interconnected parts)
  - "design a board with an ESP32 and temperature sensor" -- system-level (ESP32 + sensor = system)

**Tiebreaker**: When in doubt, prefer `design_pipeline` over `add_component`. A user describing multiple parts and their interaction is designing a system, not just adding components.

### modify_design
The user wants to change something in an EXISTING design. Key triggers:
- "Change R1 to 10k" -- specific ref des + new value
- "Swap U1 for MCP1700" -- specific ref des + new part
- "Add a bypass cap on VCC" -- adding to an existing design
- "Remove R3" -- deleting from existing
- "Connect LED to pin 13" -- rewiring
- "Make the power traces wider" -- layout change
- "Why did it add so many capacitors?" -- complaint about existing design -> modify_design
- "There are too many LEDs" -- complaint -> modify_design

### component_query
User wants INFORMATION about a component, NOT to add it or design with it:
- "Find me a temperature sensor for I2C" -- research/query
- "What's a DS18B20?" -- informational
- "Show me BME280 specs" -- datasheet request
- "Search for a 5V regulator" -- search request
- "Tell me about the ESP32-C3" -- informational

### help
User needs assistance with the tool itself:
- "What can you do?" -- tool capabilities
- "How does this work?" -- usage questions
- "Help me get started" -- onboarding
- "What commands are available?" -- reference

### other
Greetings, small talk, ambiguous, or anything that doesn't fit above:
- "hello", "hi", "good morning" -- greetings
- "thanks", "thank you" -- acknowledgments
- Random text, gibberish, test messages

## Edge Cases

| Message | Correct Intent | Reasoning |
|---------|---------------|-----------|
| "add a DS18B20 temperature sensor" | add_component | User wants to add ONE sensor, not a full design |
| "make me a board with an ESP32 and a temperature sensor and an OLED display" | design_pipeline | Multiple interacting subsystems: MCU + sensor + display |
| "I need a USB to UART adapter" | design_pipeline | Single function but needs multiple parts working together |
| "add a USB-C connector to my design" | modify_design | "to my design" implies existing design |
| "what sensors work at 3.3V?" | component_query | Asking for information, not adding anything |
| "how do I connect a DS18B20?" | component_query | Asking for connection guidance |
| "can you make the PCB smaller?" | modify_design | Changing existing design parameters |
| "the ESP32 keeps disappearing" | modify_design | Complaint about existing behavior -- implies modification |
| "design a PCB" (nothing else) | design_pipeline | Vague but clearly a design request |

## Extracted Components
- Always extract part numbers and component names mentioned
- For add_component: list the parts the user wants added
- For design_pipeline: list parts mentioned as context (even if generic)
- For modify_design: list the target parts
- For component_query: list the parts asked about
