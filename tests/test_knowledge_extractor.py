"""Tests for the structured knowledge extractor.

Covers:
1. Pin-role classification (UART, I2C, SPI, ADC, POWER, etc.)
2. Interface detection from classified pin roles
3. Power rail extraction
4. Programming/boot pin detection
5. format_knowledge_for_prompt output
6. extract_knowledge (runtime) with pin_matrix
7. extract_knowledge_for_db (build-time) with raw pin lists
"""

from __future__ import annotations

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.knowledge_extractor import (
    _classify_pin_role,
    _detect_interfaces,
    _extract_power_rails,
    _extract_programming_pins,
    _extract_interface_pin_map,
    extract_knowledge,
    extract_knowledge_for_db,
    format_knowledge_for_prompt,
)


# ── _classify_pin_role ──────────────────────────────────────────────────────

class TestClassifyPinRole:
    def test_ground(self):
        for name in ("GND", "GROUND", "VSS", "PGND", "AGND", "V-"):
            assert _classify_pin_role(name) == "GROUND", f"expected GROUND for {name}"

    def test_power_in(self):
        for name in ("VDD", "VCC", "VIN", "3V3", "V+", "VPOS"):
            assert _classify_pin_role(name) == "POWER_IN", f"expected POWER_IN for {name}"

    def test_analog_out(self):
        assert _classify_pin_role("V_{OUT}", "output") == "ANALOG_OUT"

    def test_power_out(self):
        assert _classify_pin_role("VOUT", "power_out") == "POWER_OUT"

    def test_regulator_vout(self):
        assert _classify_pin_role("VOUT", "power_out") == "POWER_OUT"

    def test_sensor_vout(self):
        assert _classify_pin_role("V_{OUT}", "output") == "ANALOG_OUT"

    def test_uart_tx(self):
        for name in ("TXD0", "TXD", "TX", "UART_TX", "U0TXD"):
            assert _classify_pin_role(name) == "UART_TX", f"expected UART_TX for {name}"

    def test_uart_rx(self):
        for name in ("RXD0", "RXD", "RX", "UART_RX", "U0RXD"):
            assert _classify_pin_role(name) == "UART_RX", f"expected UART_RX for {name}"

    def test_i2c_sda(self):
        assert _classify_pin_role("SDA") == "I2C_SDA"
        assert _classify_pin_role("I2C_SDA") == "I2C_SDA"

    def test_i2c_scl(self):
        assert _classify_pin_role("SCL") == "I2C_SCL"

    def test_spi_mosi(self):
        assert _classify_pin_role("MOSI") == "SPI_MOSI"
        assert _classify_pin_role("SPI_MOSI") == "SPI_MOSI"

    def test_spi_miso(self):
        assert _classify_pin_role("MISO") == "SPI_MISO"

    def test_spi_sck(self):
        assert _classify_pin_role("SCK") == "SPI_SCK"
        assert _classify_pin_role("SCLK") == "SPI_SCK"

    def test_spi_cs(self):
        assert _classify_pin_role("CS") == "SPI_CS"
        assert _classify_pin_role("SS") == "SPI_CS"

    def test_adc_in(self):
        assert _classify_pin_role("ADC_IN0") == "ADC_IN"
        assert _classify_pin_role("ADC1") == "ADC_IN"
        assert _classify_pin_role("SENSOR_VP") == "ADC_IN"

    def test_enable(self):
        assert _classify_pin_role("EN") == "ENABLE"
        assert _classify_pin_role("EN_") == "ENABLE"

    def test_reset(self):
        assert _classify_pin_role("RESET") == "RESET"
        assert _classify_pin_role("RST") == "RESET"

    def test_boot(self):
        assert _classify_pin_role("BOOT") == "BOOT"
        assert _classify_pin_role("GPIO0") == "BOOT"

    def test_jtag(self):
        assert _classify_pin_role("TCK") == "JTAG_TCK"
        assert _classify_pin_role("TMS") == "JTAG_TMS"
        assert _classify_pin_role("TDI") == "JTAG_TDI"
        assert _classify_pin_role("TDO") == "JTAG_TDO"

    def test_swd(self):
        assert _classify_pin_role("SWDIO") == "SWD_IO"
        assert _classify_pin_role("SWCLK") == "SWD_CLK"

    def test_usb(self):
        assert _classify_pin_role("USB_DP") == "USB_DP"
        assert _classify_pin_role("USB_DN") == "USB_DN"
        assert _classify_pin_role("D+") == "USB_DP"
        assert _classify_pin_role("D-") == "USB_DN"

    def test_nc(self):
        assert _classify_pin_role("NC") == "NC"

    def test_empty_name_falls_back_to_etype(self):
        assert _classify_pin_role("", "power_in") == "POWER_IN"
        assert _classify_pin_role("", "output") == "DIGITAL_OUT"

    def test_gpio_fallback(self):
        assert _classify_pin_role("IO1") == "GPIO"
        assert _classify_pin_role("GPIO5") == "GPIO"


# ── _detect_interfaces ──────────────────────────────────────────────────────

class TestDetectInterfaces:
    def test_uart_detected(self):
        roles = {"1": "GROUND", "2": "POWER_IN", "3": "UART_TX", "4": "UART_RX"}
        assert "UART" in _detect_interfaces(roles)

    def test_i2c_detected(self):
        roles = {"1": "I2C_SDA", "2": "I2C_SCL"}
        assert "I2C" in _detect_interfaces(roles)

    def test_spi_detected(self):
        roles = {"1": "SPI_MOSI", "2": "SPI_MISO", "3": "SPI_SCK"}
        assert "SPI" in _detect_interfaces(roles)

    def test_spi_with_cs_detected(self):
        roles = {"1": "SPI_MOSI", "2": "SPI_MISO", "3": "SPI_SCK", "4": "SPI_CS"}
        detected = _detect_interfaces(roles)
        assert "SPI" in detected
        assert "SPI_CS" in detected

    def test_adc_detected(self):
        roles = {"1": "ADC_IN", "2": "ADC_IN"}
        assert "ADC" in _detect_interfaces(roles)

    def test_usb_detected(self):
        roles = {"1": "USB_DP", "2": "USB_DN"}
        assert "USB" in _detect_interfaces(roles)

    def test_no_interfaces(self):
        roles = {"1": "GROUND", "2": "POWER_IN"}
        assert _detect_interfaces(roles) == []


# ── _extract_power_rails ────────────────────────────────────────────────────

class TestExtractPowerRails:
    def test_3v3_detected(self):
        roles = {"a:1": "POWER_IN", "a:2": "GROUND"}
        pm = {"a:1": {"name": "VDD"}, "a:2": {"name": "GND"}}
        assert "3.3V" in _extract_power_rails(roles, pm)

    def test_multiple_rails_deduped(self):
        roles = {"a:1": "POWER_IN", "b:1": "POWER_IN"}
        pm = {"a:1": {"name": "VDD"}, "b:1": {"name": "VCC"}}
        rails = _extract_power_rails(roles, pm)
        assert rails == ["3.3V"]


# ── _extract_programming_pins ───────────────────────────────────────────────

class TestProgrammingPins:
    def test_enable_detected(self):
        roles = {"ESP32:3": "ENABLE"}
        pm = {"ESP32:3": {"name": "EN"}}
        assert _extract_programming_pins(roles, pm) == {"ENABLE": "3"}

    def test_boot_detected(self):
        roles = {"ESP32:0": "BOOT"}
        pm = {"ESP32:0": {"name": "GPIO0"}}
        assert _extract_programming_pins(roles, pm) == {"BOOT": "0"}


# ── extract_knowledge (runtime) ─────────────────────────────────────────────

class TestExtractKnowledge:
    def test_esp32_wroom_32d(self):
        comp = {"ref_des": "U1", "id_str": "RF_Module:ESP32-WROOM-32D"}
        pm = {
            "U1:1":  {"name": "GND",       "etype": "power_in"},
            "U1:2":  {"name": "VDD",       "etype": "power_in"},
            "U1:3":  {"name": "EN",        "etype": "input"},
            "U1:4":  {"name": "SENSOR_VP", "etype": "input"},
            "U1:5":  {"name": "SENSOR_VN", "etype": "input"},
            "U1:34": {"name": "RXD0",      "etype": "bidirectional"},
            "U1:35": {"name": "TXD0",      "etype": "bidirectional"},
        }
        k = extract_knowledge(comp, pm)
        assert "ADC" in k["interfaces"]
        assert "ENABLE" in k["programming_pins"]
        assert "3.3V" in k["power_rails"]
        assert k["analog_inputs"] == ["4", "5"]
        assert k["id_str"] == "RF_Module:ESP32-WROOM-32D"

    def test_ams1117(self):
        comp = {"ref_des": "U2", "id_str": "Regulator_Linear:AMS1117-3.3"}
        pm = {
            "U2:1": {"name": "GND",  "etype": "power_in"},
            "U2:2": {"name": "VOUT", "etype": "power_out"},
            "U2:3": {"name": "VIN",  "etype": "power_in"},
        }
        k = extract_knowledge(comp, pm)
        assert k["pin_roles"]["1"] == "GROUND"
        assert k["pin_roles"]["2"] == "POWER_OUT"
        assert k["pin_roles"]["3"] == "POWER_IN"

    def test_lm35(self):
        comp = {"ref_des": "U3", "id_str": "Sensor_Temperature:LM35-D"}
        pm = {
            "U3:1": {"name": "V_{OUT}", "etype": "output"},
            "U3:2": {"name": "NC",      "etype": "passive"},
            "U3:3": {"name": "NC",      "etype": "passive"},
            "U3:4": {"name": "GND",     "etype": "power_in"},
            "U3:5": {"name": "NC",      "etype": "passive"},
            "U3:6": {"name": "NC",      "etype": "passive"},
            "U3:7": {"name": "NC",      "etype": "passive"},
            "U3:8": {"name": "V+",      "etype": "power_in"},
        }
        k = extract_knowledge(comp, pm)
        assert k["pin_roles"]["1"] == "ANALOG_OUT", f"got {k['pin_roles']['1']}"
        assert k["pin_roles"]["4"] == "GROUND"
        assert k["pin_roles"]["8"] == "POWER_IN"

    def test_ch340g(self):
        comp = {"ref_des": "U4", "id_str": "Interface_USB:CH340G"}
        pm = {
            "U4:1": {"name": "GND",  "etype": "power_in"},
            "U4:2": {"name": "TXD",  "etype": "output"},
            "U4:3": {"name": "RXD",  "etype": "input"},
            "U4:4": {"name": "V3",   "etype": "power_in"},
            "U4:5": {"name": "UD+",  "etype": "bidirectional"},
            "U4:6": {"name": "UD-",  "etype": "bidirectional"},
            "U4:7": {"name": "XI",   "etype": "input"},
            "U4:8": {"name": "XO",   "etype": "output"},
            "U4:16": {"name": "VCC", "etype": "power_in"},
        }
        k = extract_knowledge(comp, pm)
        assert "UART" in k["interfaces"]
        assert k["interface_pins"]["UART"] == {"TX": "2", "RX": "3"}

    def test_ds18b20(self):
        comp = {"ref_des": "U5", "id_str": "Sensor_Temperature:DS18B20"}
        pm = {
            "U5:1": {"name": "GND", "etype": "power_in"},
            "U5:2": {"name": "DQ",  "etype": "bidirectional"},
            "U5:3": {"name": "VDD", "etype": "power_in"},
        }
        k = extract_knowledge(comp, pm)
        assert k["pin_roles"]["1"] == "GROUND"
        assert k["pin_roles"]["2"] == "BIDIRECTIONAL"
        assert k["pin_roles"]["3"] == "POWER_IN"

    def test_datasheet_summary_extraction(self):
        comp = {"ref_des": "U6", "id_str": "Sensor:Test"}
        pm = {"U6:1": {"name": "V_{OUT}", "etype": "output"}}
        k = extract_knowledge(comp, pm, datasheet_text="10 mV per degree C output. Supply voltage 2.7 V to 5.5 V.")
        # voltage regex captures standalone "2.7 V"
        assert "voltage" in k.get("datasheet_summary", ""), f"got {k['datasheet_summary']!r}"

    def test_existing_roles_reused(self):
        comp = {"ref_des": "U1", "id_str": "RF_Module:ESP32-WROOM-32D"}
        pm = {
            "U1:1":  {"name": "GND",  "etype": "power_in"},
            "U1:35": {"name": "TXD0", "etype": "bidirectional"},
        }
        existing = {"U1:35": "UART_TX"}
        k = extract_knowledge(comp, pm, existing_roles=existing)
        assert k["pin_roles"]["35"] == "UART_TX"


# ── extract_knowledge_for_db (build-time) ───────────────────────────────────

class TestExtractKnowledgeForDb:
    def test_esp32_pins(self):
        pins = [
            {"num": "1", "name": "GND", "type": "power_in"},
            {"num": "2", "name": "VDD", "type": "power_in"},
            {"num": "3", "name": "EN", "type": "input"},
            {"num": "4", "name": "SENSOR_VP", "type": "input"},
            {"num": "34", "name": "RXD0", "type": "bidirectional"},
            {"num": "35", "name": "TXD0", "type": "bidirectional"},
        ]
        k = extract_knowledge_for_db("RF_Module:ESP32-WROOM-32D", pins, "ESP32 module")
        assert "ADC" in k["interfaces"]
        assert k["programming_pins"].get("ENABLE") == "3"
        assert k["pin_roles"]["1"] == "GROUND"
        assert k["pin_roles"]["2"] == "POWER_IN"
        assert k["pin_roles"]["35"] == "UART_TX"


# ── format_knowledge_for_prompt ─────────────────────────────────────────────

class TestFormatForPrompt:
    def test_empty(self):
        assert format_knowledge_for_prompt({}) == ""

    def test_interfaces_only(self):
        k = {
            "interface_pins": {"UART": {"TX": "35", "RX": "34"}},
            "power_rails": ["3.3V"],
            "programming_pins": {"ENABLE": "3"},
            "datasheet_summary": "",
        }
        result = format_knowledge_for_prompt(k)
        assert "UART" in result
        assert "TX=35" in result
        assert "RX=34" in result
        assert "power=3.3V" in result
        assert "prog=ENABLE=3" in result

    def test_full(self):
        k = {
            "interface_pins": {
                "UART": {"TX": "35", "RX": "34"},
                "I2C": {"SDA": "21", "SCL": "22"},
            },
            "power_rails": ["3.3V"],
            "programming_pins": {"ENABLE": "3", "BOOT": "0"},
            "datasheet_summary": "voltage=3.3V",
        }
        result = format_knowledge_for_prompt(k)
        assert "UART" in result
        assert "I2C" in result
        assert "TX=35" in result
        assert "SDA=21" in result
        assert "power=3.3V" in result
        assert "prog=" in result
        assert "ds=voltage=3.3V" in result
