---
name: circuitbot-clarify
description: Prompt clarification knowledge for the CircuitBot clarify stage. Teaches what design dimensions to check for completeness and what questions to ask when the user hasn't provided enough detail. Trigger on: clarify stage LLM calls, prompt completeness assessment, clarification question generation.
---

# CircuitBot Clarify Knowledge

Assess the user's design prompt for completeness across these dimensions.

## Six Design Dimensions

### 1. MCU/Processor
Is a specific platform mentioned?
- ESP32, RP2040, STM32, ATmega, SAMD, NRF, etc.
- If missing: ask "What MCU platform?" with options

### 2. Sensor/Input
Is a specific sensor type or part number mentioned?
- Temperature, humidity, pressure, motion, light, etc.
- Specific parts: DS18B20, TMP117, BME280, MPU6050, etc.
- If user says "sensor" but no type: ask for specifics

### 3. Power
How will the board be powered?
- USB (5V from USB-C or USB-A)
- Battery (LiPo 3.7V, Li-ion, coin cell)
- External supply (specific voltage, barrel jack, terminal block)
- Solar or energy harvesting
- If missing: always ask -- power architecture affects everything

### 4. Connectivity
Does the user need wireless?
- WiFi, BLE, LoRa, Zigbee, Thread, NFC, etc.
- Or just wired (USB, UART, I2C, SPI)
- If the user names ESP32: WiFi/BLE is implied
- If the user names RP2040/ATmega: wireless is NOT implied

### 5. Display/Output
Does the user want visual output?
- OLED display (SSD1306), LCD, e-ink, TFT
- Simple LED indicators
- Buzzer/speaker
- No output needed

### 6. Form Factor
Any size or physical constraints?
- Breadboard-friendly
- Compact/SMD
- Specific dimensions
- Mounting holes, connectors on specific edges

## Completeness Heuristic
- 3+ dimensions specified -> prompt is likely specific enough (skip clarification)
- Fewer than 3 -> ask questions about the missing dimensions
- When asking, provide concrete options + "No preference" for each question
- Never ask about something already specified in the prompt
- Maximum 5 questions total

## Question Template
Each question should:
1. Ask about ONE dimension only
2. Have 2-4 concrete, realistic options
3. Include "No preference" as an option
4. Be concise -- one sentence
