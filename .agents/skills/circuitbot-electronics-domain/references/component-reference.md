# Component Reference Tables

## Common KiCad Symbols Reference

| Category | Description | Exact KiCad ID |
|----------|------------|----------------|
| Resistor | Generic SMD | `Device:R_Small` |
| Capacitor | Generic SMD | `Device:C_Small` |
| LED | Generic indicator | `Device:LED` |
| Inductor | Generic SMD | `Device:L_Small` |
| Diode | Generic SMD | `Device:D_Small` |
| Polyfuse | Overcurrent protection | `Device:Polyfuse` |
| USB-C 2.0 | 16-pin with data | `Connector:USB_C_Receptacle_USB2.0_16P` |
| USB-C PowerOnly | No data lines | `Connector:USB_C_Receptacle_PowerOnly` |
| AVR-ISP-6 | ICSP programming header | `Connector:AVR-ISP-6` |
| 1x04 Pin Header | UART header | `Connector:Conn_01x04_Pin` |
| 1x06 Pin Header | Programming header | `Connector:Conn_01x06_Pin` |
| 1x08 Pin Header | Extended header | `Connector:Conn_01x08_Pin` |
| 3.3V LDO | AMS1117 (1A) | `Regulator_Linear:AMS1117-3.3` |
| 3.3V LDO | AP2112K (600mA) | `Regulator_Linear:AP2112K-3.3` |
| 5.0V LDO | AMS1117 (1A) | `Regulator_Linear:AMS1117-5.0` |
| I2C Temp Sensor | ±0.1°C precision | `Sensor_Temperature:TMP117xxYBG` |
| I2C Temp Sensor | ±0.5°C modern | `Sensor_Temperature:TMP1075` |
| 1-Wire Temp Sensor | ±0.5°C | `Sensor_Temperature:DS18B20` |
| Temp+Humidity | I2C/SPI | `Sensor_Temperature:BME280` |
| USB-UART Bridge | CP2102N | `Interface_USB:CP2102N` |
| USB-UART Bridge | CH340G (alternative) | `Interface_USB:CH340G` |
| LiPo Charger | MCP73831 | `Battery_Management:MCP73831` |
| PWR Flag | Power net marker | `power:PWR_FLAG` |

## MCU Compatibility Matrix

| Part Number | Library | Wireless | Native USB | Family |
|------------|---------|----------|------------|--------|
| ESP32-WROOM-32D | `RF_Module:ESP32-WROOM-32D` | WiFi+BLE | No (needs bridge) | ESP32 |
| ESP32-C3-DevKitM-1 | `RF_Module:ESP32-C3-DevKitM-1` | WiFi+BLE | Yes | ESP32-C3 |
| ESP32-S3-WROOM-1 | `RF_Module:ESP32-S3-WROOM-1` | WiFi+BLE | Yes | ESP32-S3 |
| ESP32-C6-DevKitC-1 | `RF_Module:ESP32-C6-DevKitC-1` | WiFi+BLE+Zigbee | Yes | ESP32-C6 |
| WEMOS_C3_mini | `RF_Module:WEMOS_C3_mini` | WiFi+BLE | Yes | ESP32-C3 |
| RP2040 | `MCU_*` or as module | No | Yes | RP2040 |
| RP2350 | `MCU_*` or as module | No | Yes | RP2350 |
| ATmega328P | `MCU_Microchip:ATmega328P` | No | No | AVR |
| ATmega32U4 | `MCU_Microchip:ATmega32U4` | No | Yes | AVR |
| STM32F030 | `MCU_ST:STM32F030` | No | No | STM32 |
| SAMD21G | `MCU_Microchip_SAMD:SAMD21G` | No | Yes | SAMD |

## Generation Preference Tiebreaker (general requests only)

When the user did NOT name a specific part, prefer these in ties:

| Category | Prefer (modern) | Avoid (legacy) |
|----------|----------------|----------------|
| AVR MCU | ATmega4809 | ATmega328P, ATmega168 |
| Raspberry Pi MCU | RP2350 | RP2040 |
| ESP32 MCU | ESP32-S3 or ESP32-C6 | ESP32 (original), ESP8266 |
| ARM MCU (ST) | STM32U5, STM32H5, STM32G4 | STM32F103, STM32F4 |
| ARM MCU (Microchip) | SAMD51, SAMD21 | SAMD11 |
| I2C temp (±0.1°C) | TMP117 | TMP102, DS1631 |
| I2C temp (±0.5°C) | TMP1075 | LM75, TMP175 |
| USB-C ESD + CC | TPD6S300A | discrete USBLC6-2SC6 + CC |
| USB-UART bridge | CP2102N, CH340E/K, FT230X | FT232RL (obsolete DIP) |
| Accelerometer | LIS3DH, LSM6DSO | ADXL345, MPU6050 |
| Magnetometer | LIS3MDL | HMC5883L |
