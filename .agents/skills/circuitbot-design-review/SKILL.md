---
name: circuitbot-design-review
description: Design review knowledge for the CircuitBot design_review stage. Teaches what to look for when reviewing a completed circuit design across power, signal, protection, cost, and layout categories. Trigger on: design_review stage LLM calls, circuit design review, improvement suggestions, PCB design audit.
---

# CircuitBot Design Review Knowledge

When reviewing a completed circuit design, check these patterns and suggest improvements. Focus on issues that actually matter -- skip nitpicks that won't affect real-world performance.

## Power Category

### Check For These Issues
- **Missing decoupling capacitors**: Every IC needs at least one 100nF ceramic per power pin. Count them.
- **Bulk capacitance**: Each voltage rail needs a 10-47µF electrolytic/tantalum.
- **Power budget**: Sum all IC current draws and verify the regulator can supply 1.5-2x the total.
- **Reverse polarity protection**: If board has barrel jack or battery input, is there a protection diode or PFET?
- **Power path sequencing**: Does any IC require power-on sequencing (FPGAs, some RF ICs)?
- **Voltage drop**: For high-current nets (>500mA), check trace width is adequate.

### Common Missing Parts
- Input bulk cap on regulator input (10µF typical)
- Output cap on regulator output (10-22µF typical)
- Ferrite bead for sensitive analog/RF supplies

## Signal Category

### Check For These Issues
- **Missing pull-up resistors**: I2C buses MUST have pull-ups (4.7kΩ typical on 3.3V). Check counts.
- **Missing pull-down resistors**: USB-C CC pins need 5.1kΩ pull-downs for UFP mode.
- **Bus loading**: I2C bus with many devices -- is total capacitance within limits? (>400pF needs buffer)
- **Level shifting**: Are 5V peripherals connected to 3.3V MCU? Needs level shifter.
- **Series termination**: High-speed signals (>10MHz) -- are there series resistors for signal integrity?
- **Unconnected pins**: Review pin list for important function pins left hanging (EN, RST, INT).

### Common Issues
- I2C with no pull-ups
- DS18B20 with no pull-up on DQ line
- USB D+/D- lines swapped or without ESD protection
- Crystal load caps wrong value for chosen crystal

## Protection Category

### Check For These Issues
- **ESD protection**: USB ports and external connectors should have ESD protection.
- **Overcurrent**: Power input should have polyfuse or other current limiting.
- **Overvoltage**: Is the regulator's max input voltage higher than the supply?
- **Thermal**: Are power components adequately sized for dissipation? Check regulator power (Vin - Vout) × I.
- **Inductive kick**: Motors, relays, solenoids -- need flyback diode across inductive loads.

## Cost Category

### Check For These Issues
- **Part consolidation**: Can multiple regulators be consolidated? Can different-valued resistors be normalized?
- **Package overkill**: D2PAK/TO-220 for <500mA circuit? Suggest SOT-223 or SOT-23-5.
- **Unnecessary bridge**: USB-UART bridge when MCU has native USB -- remove.
- **Unnecessary crystal**: MCU can run on internal oscillator for non-timing-critical designs.

## Layout Category

### Check For These Issues
- **Decoupling capacitor placement**: Should be as close as possible to IC power pins, same side of board.
- **USB differential pairs**: D+/D- should be routed together with controlled impedance (~90Ω) for high-speed.
- **Analog vs digital separation**: Sensitive analog traces should not run near switching power traces.
- **Keep-out zones**: Antenna areas, connectors, mounting holes.
- **Thermal relief**: Power components need adequate copper pour and thermal vias.

## Suggestion Severity
- **High**: Electrical error -- design WILL NOT work without this fix (missing pull-ups, wrong voltage, no regulator)
- **Medium**: Electrical concern -- design MAY have issues (marginal power budget, no ESD)
- **Low**: Improvement -- nice to have (cost savings, layout hints, alternative parts)

## Output Format
Each suggestion should include:
1. **category**: power/signal/protection/cost/layout
2. **severity**: high/medium/low
3. **description**: What the issue is
4. **suggestion**: What to do about it
5. **target**: The component ref or net it applies to (or null if general)

Maximum 5 suggestions. If the design looks good, return empty suggestions array.
