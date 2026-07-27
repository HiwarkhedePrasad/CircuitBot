---
name: circuitbot-validator
description: Component validation knowledge for the CircuitBot validate stage. Teaches electrical validation rules, part family integrity checks, wireless-feature matching, USB-UART bridge redundancy detection, and module awareness rules. Trigger on: validate stage LLM calls, BOM validation, component correctness checking, missing component detection.
---

# CircuitBot Validator Knowledge

Apply these rules when validating selected components against the user's design request.

## Part Family Integrity

If the user's prompt names a specific MCU family, the selected MCU MUST belong to the same family:
- Prompt says "ESP32-C3" -> component must be ESP32-family (wireless MCU). ATmega32U4 is a MISMATCH.
- Prompt says "ATmega328P" -> component must be AVR-family. STM32F411 is a MISMATCH.
- Exception: bare RF chips replaced by modules that include them (ESP32 replaced by ESP32-WROOM) are acceptable.

## Wireless-Feature Check

If the user prompt specifies wireless capability -- either by naming an ESP32 or by explicitly mentioning WiFi/BLE/LoRa/Zigbee -- the selected MCU MUST support that protocol OR a separate wireless transceiver (NRF24L01, ESP8266, RFM95) must also be in the component list.

A non-wireless MCU (ATmega, bare STM32, RP2040) with NO separate wireless chip is a FATAL MISMATCH.

## USB-UART Bridge Redundancy

Flag a bridge as **REDUNDANT** when the MCU has native USB:
- ESP32-S3, ESP32-C3, ESP32-C6, ESP32-H2: native USB-serial-JTAG
- RP2040, RP2350: native USB
- SAMD21, SAMD51: native USB
- NRF52840: native USB
- STM32F0/F4/G4/H5/H7 with USB: native USB
- STM32U5: native USB

Flag a missing bridge as an **ERROR** when the MCU lacks native USB:
- ESP32 (original), ESP8266: no native USB -> need bridge
- ATmega328P: no native USB -> need bridge + programming header
- Classic STM32F103: no native USB -> need bridge

Use `suggested_query="USB to UART bridge CP2102N"` and `library_filter="Interface_USB"`.

## Atomic Component Rule

When listing missing components, break them into single, atomic parts. NEVER bundle:
- "LED and resistor" -> two entries: one for LED, one for resistor
- "Connector with CC resistors" -> one entry for connector, separate entries for resistors

## Module Redundancy

When a DevKit/dev board (DEVKIT, NODEMCU, BOARD, BREAKOUT keywords) is selected, the following are REDUNDANT and should be flagged:
- USB-UART bridge (CP2102N, CH340, FT230X/FT232)
- 3.3V voltage regulator (AMS1117 on the board)
- USB-C receptacle (board has its own)
- Main crystal (board has its own)
- Crystal load capacitors

But when a bare WROOM module (ESP32-WROOM-32D without DEVKIT/BOARD) is selected, the above are NOT redundant -- they must all be added externally.

## Common Errors to Flag

| Pattern | What's Wrong |
|---------|-------------|
| Sensor_Gas selected for "moisture sensing" | Wrong sensor type entirely |
| Interface_UART selected as "capacitor" | Completely wrong category |
| USB PD controller as "USB-C connector" | PD controller is not a physical connector |
| ATmega328P with no crystal | ATmega needs external clock (>4.5V for 16MHz) |
| ESP32 (original) with no USB-UART bridge | Original ESP32 has no native USB |
| BME280 selected but MCU listed as "RP2040 with no I2C" | BME280 needs I2C -- verify MCU pin summary lists I2C |

## Power Flag Check
Power nets that have no power-output pin type component need a PWR_FLAG. Common cases:
- 3V3 net fed by AMS1117-3.3 (pin etype is "passive", not "power_out") -> flag needed
- Always flag 3V3, 5V, VBAT nets if no power source with power_out etype exists

## Board Type Awareness
- `devkit`: Pre-built modules expected. Separate USB/regulator/bridge are likely redundant.
- `bare_ic`: All support components must be explicit. Nothing is "integrated".
- `custom_pcb`: Mixed -- check each component individually against the actual design.
