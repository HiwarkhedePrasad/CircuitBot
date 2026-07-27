---
name: circuitbot-analyzer
description: Design decomposition knowledge for the CircuitBot analyze stage. Teaches how to break a PCB design prompt into functional subsystems, apply critical electrical rules (mandatory power regulation when voltage mismatch, AVR clock limitations at 3.3V), and handle user-specified part numbers. Trigger on: analyze stage LLM calls, subsystem decomposition, design requirement parsing, power architecture decisions.
---

# CircuitBot Analyzer Knowledge

Use these rules when decomposing a user's design request into functional subsystems for a PCB design.

## Mandatory Subsystem Rules

### Power Regulation (Voltage Mismatch Rule)
If any subsystem operates at a voltage lower than the primary power input, a **Power Regulation** subsystem is MANDATORY. Examples:
- USB 5V input + 3.3V MCU -> Power Regulation required (USB-C provides 5V, MCU needs 3.3V)
- Battery 3.7V + 3.3V MCU -> Power Regulation required (battery voltage varies)
- 12V input + 5V MCU -> Power Regulation required

When a dev board (ESP32-DevKit, WEMOS, etc.) is selected as the MCU, the dev board already has on-board regulation. The Power Regulation subsystem should still be created but the validator will handle dev-board redundancy.

### USB Power Input Rule
When USB-C/Type-C is mentioned for power:
1. Create a **Power Input** subsystem with `USB_C_Receptacle_USB2.0` as the example component
2. Create a **Power Regulation** subsystem with a 3.3V regulator (AMS1117-3.3) when the MCU runs at 3.3V

### USB-UART Bridge Rule
Only create a Programming/Debug subsystem with a USB-UART bridge when the MCU lacks native USB:
- **Bridge needed**: ESP32 (original), ESP8266, ATmega328P, classic STM32F103
- **No bridge needed** (native USB): ESP32-S3, ESP32-C3, ESP32-C6, ESP32-H2, RP2040, RP2350, SAMD21, SAMD51, NRF52840, STM32F0/F4/G4/H5/H7/H5 with USB, STM32U5

### AVR Clock Rule
ATmega328P and classic AVR MCUs need ≥4.5V to run at 16MHz. If the power rail is 3.3V:
- Use an 8 MHz crystal instead of 16 MHz
- Or use the internal 8 MHz RC oscillator (no crystal needed)
- AVR Dx series (AVR128DA28, AVR64DD) support 3.3V at up to 20MHz -- no restriction

## Subsystem Output Rules

### Exact Part Numbers
When the user names a specific part number, copy it verbatim as the first `example_components` entry for the matching subsystem. NEVER substitute with a generic term or different part.

### Supporting Passives
Do NOT create subsystems for decoupling capacitors or pull-up resistors -- these are injected automatically by the supporting parts generator.

### Clock/Crystal
Create a Clock/Oscillator subsystem when the selected MCU/IC needs an external clock source. Use `Device:Crystal` for passive crystals (ATmega, basic STM32) and `Oscillator` library filter for active oscillators (FPGAs, high-speed PHYs).

### Connector Search Rule
For physical connectors (USB, power jack, audio), include the exact KiCad symbol name:
- USB-C power: `USB_C_Receptacle_USB2.0`
- Barrel jack: `Barrel_Jack`
- Audio jack: `Audio_Jack`

## Subsystem Count Heuristic
A typical design needs 3-6 subsystems. If you're creating more than 8, you're over-decomposing. If fewer than 3, you might be missing essential blocks.

## User-Specified Parts Handling
- Each user-named part becomes its own subsystem with name `"User-specified ({part})"`
- Do NOT pool multiple user parts into one subsystem
- User-part subsystems should have `"bus": "any"` and `"function": "Parts explicitly requested by the user -- MUST be selected when matching"`
