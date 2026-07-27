---
name: circuitbot-reranker
description: Component scoring and selection knowledge for the CircuitBot reranker stage. Teaches per-category scoring criteria, library prefix rules, module awareness, and user-part priority. Trigger on: reranker stage LLM calls, candidate scoring, component selection, part ranking, score assignment for MCU/sensor/regulator/connector candidates.
---

# CircuitBot Reranker Knowledge

Apply these criteria when scoring component candidates for a subsystem. This supplements the general reranker prompt with stage-specific depth.

## Per-Category Scoring Criteria

### MCU Candidates (MCU_*, RF_Module:*)
| Score | Criteria |
|-------|----------|
| 9-10 | Matches the user's specified MCU family EXACTLY. ESP32-WROOM-32D when user asks for ESP32. Has required wireless/bus support. |
| 7-8 | Same family but different variant. ESP32-C3 when user asked for ESP32. Still has WiFi/BLE. |
| 4-6 | Different MCU family entirely but same architecture class. STM32 when user asked for ESP32 (both ARM, but no wireless). |
| 0-3 | Wrong type entirely. Non-MCU component in an MCU subsystem. Sensor, regulator, or connector selected for processing subsystem. |

**CRITICAL**: `RF_Module:*` components like `ESP32-WROOM-32D`, `ESP32-C3-DevKitM-1`, `WEMOS_C3_mini` ARE valid MCUs. Do NOT score them 0 just because they're in RF_Module library.

### Sensor Candidates (Sensor_*)
| Score | Criteria |
|-------|----------|
| 9-10 | Exact match for user-requested sensor. TMP117 when user asked for high-precision I2C. DS18B20 when user asked for 1-Wire. |
| 7-8 | Same measurement type but different interface or precision. BME280 (pressure+humidity+temp) for a "temperature sensor" request. |
| 4-6 | Related sensor type but different physical quantity. Humidity sensor when temperature was requested. Accelerometer when temperature was requested. |
| 0-3 | Completely wrong type. LED, regulator, connector, MCU in a sensor subsystem. |

### Connector Candidates (Connector_*)
| Score | Criteria |
|-------|----------|
| 9-10 | Exact connector type requested. USB-C 16-pin for USB-C subsystem. AVR-ISP-6 for ICSP programming header. |
| 7-8 | Same connector class but different variant. 1x06 pin header when 1x04 was asked. PowerOnly USB-C when full featured was asked (note: penalize this!). |
| 4-6 | Different connector type but same general purpose. Terminal block where connector was asked. |
| 0-3 | Non-connector component. ESD protection IC, regulator, MCU, resistor scored for a connector subsystem. |

### Power Regulation Candidates
| Score | Criteria |
|-------|----------|
| 9-10 | Exact output voltage regulator. AMS1117-3.3 for 3.3V. AMS1117-5.0 for 5V. Compact package (SOT-223, SOT-23-5). |
| 7-8 | Correct voltage but different package or slightly overkill. D2PAK regulator correctly outputting 3.3V (functionally correct, physically too large). |
| 4-6 | Wrong voltage but same regulator family. 5V regulator when 3.3V was needed. |
| 0-3 | Completely wrong. Switching regulator where linear was needed. Non-regulator component. |

## Library Prefix Hard Rules
- Connector subsystems MUST select from `Connector_*` or `Connector:` libraries. Zero-out everything else.
- MCU subsystems MUST select from `MCU_*`, `MCU:`, or `RF_Module:` libraries.
- A USB-C Power Input subsystem MUST have a connector as the primary component. ESD protection ICs (TPD6S300A) are supporting components only.

## Module Awareness
- Dev boards (DEVKIT, NODEMCU, BOARD, BREAKOUT in name) already provide USB, regulation, bridge. Score separate USB/regulator/bridge components 0 with SKIPPED justification.
- Bare modules (WROOM, MINI without DEVKIT) do NOT provide regulation or USB bridge -- score supporting components normally.
- LEDs, buttons, switches, sensors, connectors are NEVER integrated into any module. Score them normally regardless.
- An MCU having ADC pins does NOT mean it "covers" a temperature sensor -- score the sensor normally.

## User-Requested Parts
- When the user named a specific part number, that exact part MUST score highest (9-10).
- Do NOT replace a user-named part with a modern equivalent.
- User-requested parts are NEVER suppressed by module awareness.
