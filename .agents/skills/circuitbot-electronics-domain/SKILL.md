---
name: circuitbot-electronics-domain
description: Core electronics engineering domain knowledge for the CircuitBot PCB design pipeline. Covers component selection, KiCad library conventions, electrical design patterns, and pipeline-specific rules. Use this skill whenever generating or validating electronic designs -- it ensures the LLM selects appropriate components, wires buses correctly, respects module awareness, and follows KiCad conventions. Triggers on: component selection, schematic generation, netlist wiring, BOM validation, ESP32/STM32/RP2040/ATmega MCU selection, sensor matching, power architecture, I2C/SPI/UART bus wiring, decoupling capacitor placement, module redundancy detection.
---

# CircuitBot Electronics Domain

This skill encodes domain knowledge for the CircuitBot AI PCB design pipeline. The LLM should use these rules when analyzing designs, selecting components, validating BOMs, and generating netlists.

## 1. Component Selection Rules

### MCU Classification

Components from these library prefixes are valid MCUs/microcontrollers:
- `MCU_*` -- any library starting with `MCU_` (e.g. MCU_Espressif, MCU_ST, MCU_Microchip)
- `RF_Module:*` -- ESP32-WROOM modules, ESP32-C3-DevKitM-1, etc. ESP32 modules ARE valid MCUs even though their library is `RF_Module`, not `MCU_*`.
- `Module_*` -- generic module libraries

Bare RF chips (e.g. `RF_Module:ESP32-WROOM-32D` is a module, NOT a bare chip -- it has WROOM in the name) should NOT be confused with bare RF ICs.

An `RF_Module` component can serve as the primary MCU for a design. Do NOT flag it as "wrong type" just because it comes from `RF_Module` instead of `MCU_*`.

### Sensor Selection

When scoring temperature sensors:
- **TMP117** (I2C, ±0.1°C) -- highest precision, intended for medical/industrial. KiCad symbol: `Sensor_Temperature:TMP117xxYBG`.
- **TMP1075** (I2C, ±0.5°C) -- modern replacement for LM75. KiCad symbol: `Sensor_Temperature:TMP1075`.
- **DS18B20** (1-Wire, ±0.5°C) -- longer range, simpler wiring. KiCad symbol: `Sensor_Temperature:DS18B20`.
- **BME280** (I2C/SPI) -- temperature + humidity + pressure.
- **TMP102** (I2C) -- older, ±0.5°C. Avoid unless user specifically asks.

Do NOT substitute DS18B20 with TMP117 or vice versa when the user named a specific part.

### Power Regulator Selection

For 3.3V regulation on devkit/sensor boards (<500mA):
- Prefer: `Regulator_Linear:AMS1117-3.3` (SOT-223, up to 1A)
- Also good: `Regulator_Linear:AP2112K-3.3` (SOT-23-5, 600mA)
- Avoid: D2PAK (TO-263) or TO-220 packages unless >1.5A load

For 5V regulation:
- Use `Regulator_Linear:AMS1117-5.0` or `Regulator_Switching:*` for >500mA

### USB-C Connector Selection

- **`Connector:USB_C_Receptacle_USB2.0_16P`** -- standard USB 2.0 (16-pin, with D+/D-)
- `Connector:USB_C_Receptacle_PowerOnly` -- power-only (no data lines), use only for charging
- Do NOT select ESD protection ICs (TPD6S300A, USBLC6-2SC6) as the PRIMARY connector component

### Passive Component Conventions (KiCad)

Use EXACTLY these symbols unless there's a specific reason not to:
- Resistors: `Device:R_Small`
- Capacitors: `Device:C_Small`
- LEDs: `Device:LED`
- Inductors: `Device:L_Small`
- Diodes: `Device:D_Small`
- Polyfuses: `Device:Polyfuse`

### USB-UART Bridge Rules

A USB-UART bridge (CP2102N, CH340, FT230X) is ONLY needed when the MCU lacks native USB:
- **Needs bridge**: ESP32 (original), ESP8266, ATmega328P, classic STM32F103
- **Has native USB (no bridge needed)**: ESP32-S3, ESP32-C3, ESP32-C6, ESP32-H2, RP2040, RP2350, SAMD21, SAMD51, NRF52840, STM32F0/F4/G4/H5/H7 with USB, STM32U5

## 2. Pipeline-Specific Behavior

### User-Specified Parts

When the user explicitly names a part (e.g. "ESP32-WROOM-32D", "TMP117", "DS18B20"):
- The part MUST be selected. Score it highest regardless of other candidates.
- Do NOT replace a user-named part with a modern equivalent.
- User parts split into individual subsystems per-part (Phase 1 fix).

### Architecture-Locked MCU

When `architecture_frozen=True` and `primary_mcu` is set:
- The MCU subsystem candidates get a +3 score boost.
- The MCU MUST be present in the final selected components.
- If no MCU is selected, the pipeline halts with MCU_MISSING error.

### Module Awareness

Development boards (DEVKIT, NODEMCU, BOARD, BREAKOUT keywords) integrate:
- USB-UART bridge
- 3.3V voltage regulator
- USB-C connector
- Main crystal and load capacitors

Do NOT create separate subsystems for these when a dev board is selected.

BUT bare WROOM modules (ESP32-WROOM-32D, without DEVKIT/BOARD/NODEMCU) do NOT integrate regulation or USB bridge -- they need external support.

LEDs, buttons, switches, sensors, and connectors are NEVER integrated into any module -- always add them separately.

### MCU Families Compatibility

| MCU Family | Wireless | Native USB | Architecture |
|-----------|----------|-----------|--------------|
| ESP32, ESP32-S3, ESP32-C3, ESP32-C6, ESP32-H2 | WiFi+BLE | Yes (JTAG) | Xtensa/RISC-V |
| RP2040 | No | Yes (USB 1.1) | ARM Cortex-M0+ |
| RP2350 | No | Yes (USB) | ARM+RISC-V |
| ATmega328P | No | No | AVR |
| ATmega32U4 | No | Yes | AVR |
| STM32F103 | No | No (some SKUs) | ARM Cortex-M3 |
| STM32G0/G4/U5/H5 | No | Yes (USB) | ARM Cortex-M0+/M4/M33 |
| SAMD21/SAMD51 | No | Yes | ARM Cortex-M0+/M4 |

## 3. Electrical Design Patterns

### I2C Bus Wiring
- SDA and SCL lines each need a pull-up resistor (4.7kΩ typical) to the I2C power rail.
- All I2C devices share the same SDA and SCL nets.
- Pull-up resistors connect between SDA->VCC and SCL->VCC.

### SPI Bus Wiring
- MOSI, MISO, SCK shared across all devices.
- Each slave device gets its own CS/SS chip-select line.
- No pull-ups needed on SPI lines (unless specified).

### Crystal Oscillator Circuit
- Crystal connects between XTAL1 and XTAL2 pins.
- Two load capacitors: one from each XTAL pin to GND.
- Typical values: 12-22pF for 16MHz, 18-22pF for 8MHz, 12.5pF for 32.768kHz.

### Decoupling Capacitors
- One 100nF ceramic capacitor per IC power pin, placed as close to the pin as possible.
- One 10µF bulk capacitor per voltage rail.
- Decoupling cap: one pin to power net, one pin to GND.

## 4. KiCad Library Conventions

### Meaning of Library Prefixes

| Prefix | Contains |
|--------|----------|
| `Device:` | Generic passives (R, C, L, D, LED, Polyfuse) |
| `Connector:` | USB, headers, terminal blocks, audio jacks |
| `Connector_*:` | Specialized connector libraries |
| `Sensor_*:` | Temperature (TMP117, DS18B20), humidity, pressure, motion |
| `Regulator_Linear:` | LDOs (AMS1117, AP2112, MCP1700) |
| `Regulator_Switching:` | Buck/boost converters |
| `MCU_*:` | Microcontrollers (MCU_Espressif, MCU_ST, MCU_Microchip) |
| `RF_Module:` | Wireless modules including ESP32-WROOM, ESP32-DevKit, NRF24 |
| `Interface_USB:` | USB-UART bridges (CP2102, CH340, FT232) |
| `Interface_UART:` | UART/RS-232/RS-485 transceivers |
| `Battery_Management:` | Charger ICs (MCP73831, TP4056) |
| `power:` | PWR_FLAG, power symbols |
| `Display_*:` | OLED, LCD, e-ink displays |

### Pin Naming Variations

Common pin name aliases for netlist matching:
- Power: VDD, VCC, 3V3, +3.3V, VIN -- all power nets
- Ground: GND, VSS, AGND, DGND, EP, EPAD, 0V -- all GND
- I2C SDA = GPIO21/IO21/PIN_21 on some MCUs
- I2C SCL = GPIO22/IO22/PIN_22 on some MCUs
- UART TX = TXD, TX, UART_TXD
- UART RX = RXD, RX, UART_RXD
- Enable: EN, CHIP_EN, CE, CS
- Reset: RST, RESET, nRST, NRST
- Interrupt: INT, IRQ, nINT

## 5. Board Type Rules

### Board Type Determination
- `devkit` -- prototyping context, simple MCU+peripherals. Prefer DevKit modules over bare chips. Dev boards provide USB, regulator, bridge built-in.
- `bare_ic` -- production/minimal context. Bare ICs, need explicit support components.
- `custom_pcb` -- mixed, use judgement based on prompt.

### What "Provides" Means Per Board Type
- `devkit` provides: `usb_connector`, `voltage_regulator`, `usb_uart_bridge`, `main_crystal`
- `bare_ic` provides: nothing (everything must be added explicitly)
- `custom_pcb` provides: based on specific components selected
