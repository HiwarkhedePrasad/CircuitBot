---
name: circuitbot-netlist
description: Pin connection and netlist generation knowledge for the CircuitBot netlist stage. Teaches bus wiring patterns per component type, power distribution, pin matching conventions, and PWR_FLAG requirements. Trigger on: netlist stage LLM calls, signal pin wiring, bus connection generation, netlist batch processing.
---

# CircuitBot Netlist Knowledge

Apply these rules when wiring component pins into electrical nets. Use the pin names and etypes (electrical types) to determine connections.

## Bus Wiring Patterns

### I2C Bus (SDA/SCL)
1. All SDA pins -> one net named `I2C_SDA`
2. All SCL pins -> one net named `I2C_SCL`
3. Each pull-up resistor: one pin to the I2C_SDA or I2C_SCL net, the other pin to the I2C power rail (3V3)
4. Typical pin names: SDA, SCL, GPIO21(=SDA), GPIO22(=SCL), PIN_21, PIN_22, I2C0_SDA, I2C0_SCL
5. etype for I2C pins: typically "bidirectional" or "input"

### SPI Bus (MOSI/MISO/SCK/CS)
1. MOSI (Master Out Slave In) -> one shared net `SPI_MOSI`
2. MISO (Master In Slave Out) -> one shared net `SPI_MISO`
3. SCK (Serial Clock) -> one shared net `SPI_SCK`
4. Each device gets its own CS (Chip Select) net: `SPI_CS0`, `SPI_CS1`, etc.
5. Pull-ups on SPI lines are NOT needed (unlike I2C)
6. etype: MOSI = "output" (from master), "input" (on slave); CS = "output" (from master), "input" (on slave)

### UART (TX/RX)
1. Device A's TX -> Device B's RX (cross-over)
2. Device A's RX -> Device B's TX (cross-over)
3. Net names: `UART_TX`, `UART_RX` or `{DESCRIPTION}_TX`, `{DESCRIPTION}_RX`
4. etype: TX = "output", RX = "input"

### 1-Wire Bus
1. Data pin (DQ) -> one net `ONEWIRE_DATA`
2. Pull-up resistor from data net to power rail (3V3 or 5V)
3. For DS18B20: DQ pin is "bidirectional" or "input/output"

### I2S Audio Bus
1. I2S_BCK (Bit Clock) -> shared
2. I2S_WS (Word Select/LR Clock) -> shared
3. I2S_DIN (Data In) -> from microphone/codec to MCU
4. I2S_DOUT (Data Out) -> from MCU to DAC/codec

## Power Distribution

### Power Net Naming
- 3.3V rail -> `3V3` (NOT `VCC` or `3.3V`)
- 5V rail -> `5V` (NOT `VBUS` unless it's USB bus voltage)
- Ground -> `GND` (NOT `VSS`, `0V`, etc.)
- Battery -> `VBAT`
- USB voltage -> `VBUS`
- Main input -> `VIN`

### Pin Name Aliases for Power Matching
| Pin Name | Maps To |
|----------|---------|
| VDD, VCC, VDDIO, +3.3V, 3V3 | 3V3 power rail |
| VIN, +5V, 5V | 5V power rail |
| VBUS, USBVCC | VBUS (USB voltage) |
| VBAT, BATT+, BAT+ | VBAT |
| GND, VSS, AGND, DGND, EP, EPAD, 0V, PAD | GND |
| VREFP, VREF+ | Reference voltage (separate net) |
| VREFN, VREF- | Reference ground (GND) |

### Decoupling Capacitor Wiring
Each decoupling capacitor (C_Small):
1. Pin 1 -> the power net it's decoupling (3V3, 5V, etc.)
2. Pin 2 -> GND
These are passives with etype "passive".

## Crystal Oscillator Circuit

### Passive Crystal (ATmega, basic STM32, RP2040)
1. Crystal pin 1 -> MCU XTAL1/OSC_IN pin
2. Crystal pin 2 -> MCU XTAL2/OSC_OUT pin
3. Load cap 1: one pin to XTAL1 net, other pin to GND
4. Load cap 2: one pin to XTAL2 net, other pin to GND
5. Net names: `XTAL1`, `XTAL2`, `OSC_IN`, `OSC_OUT`

### Active Oscillator Module
1. Oscillator output -> MCU XTAL1/OSC_IN pin
2. Oscillator VCC -> 3V3
3. Oscillator GND -> GND
4. No load capacitors needed (active oscillator has internal drive)

## USB Wiring

### USB-C Receptacle (16-pin, USB 2.0)
- D+ -> MCU USB_DP pin
- D- -> MCU USB_DN pin
- VBUS -> 5V power net (VBUS)
- GND -> GND
- CC1, CC2 -> CC configuration nets (with 5.1kΩ pull-downs for UFP)
- SBU1, SBU2 -> optional, can leave unconnected
- Shield -> GND (via RC filter for EMC)

## Pin Matching Guidelines (by etype)

| Source etype | Target etype | Connection Valid? |
|-------------|-------------|-------------------|
| output | input | YES -- standard signal driver->receiver |
| output | bidirectional | YES -- output drives the bidirectional line |
| bidirectional | bidirectional | YES -- typical for I2C, GPIO buses |
| input | input | USUALLY NO -- nothing drives the line |
| output | output | NO -- two drivers fighting |
| passive | anything | YES -- passive (R, C) goes wherever needed |
| power_in | anything | NO -- power pin, handled by power domain pre-assignment |
| power_out | power_in | YES -- regulator output to device power |

## PWR_FLAG Rule
Power nets that have NO pin with etype "power_out" need a PWR_FLAG component from `power:PWR_FLAG`. This tells KiCad the net has a power source even if the source is implicit (e.g., a connector's VBUS pin with etype "passive" or "input").
