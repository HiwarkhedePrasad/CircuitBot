DEVICE_KNOWLEDGE = {
    "ESP32": {"voltage": 3.3, "wireless": True, "decoupling": True},
    "ESP8266": {"voltage": 3.3, "wireless": True},
    "STM32": {"voltage": 3.3, "family": "stm32"},
    "RP2040": {"voltage": 3.3, "family": "rp2040"},
    "ATmega328": {"voltage": 5.0},
    "ATmega": {"voltage": 5.0},
    "AMS1117-3.3": {"vin": 5.0, "vout": 3.3, "type": "regulator"},
    "AMS1117-5.0": {"vin": 7.0, "vout": 5.0, "type": "regulator"},
    "AMS1117": {"vin": 5.0, "vout": 3.3, "vague": True},
    "USB_C": {"vbus": 5.0, "type": "connector"},
    "DS18B20": {"voltage": "3.0-5.5", "bus": "1-wire"},
    "TMP117": {"voltage": 3.3, "bus": "i2c"},
    "MCP73831": {"type": "charger", "vout": 4.2},
    "TP4056": {"type": "charger"},
}


# Pin names that indicate power/GND roles (for SYV002, PGV004, Power Net Repair)
POWER_PIN_NAMES: dict[str, str] = {
    "VCC": "power_in",
    "VDD": "power_in",
    "VUSB": "power_in",
    "VBUS": "power_in",
    "VIN": "power_in",
    "V+": "power_in",
    "VOUT": "power_out",
    "VO": "power_out",
    "3V3": "power_in",
    "5V": "power_in",
    "GND": "power_in",
    "GNDD": "power_in",
    "GNDA": "power_in",
    "VSS": "power_in",
    "VEE": "power_in",
    "VREF": "passive",
}


def lookup_device(id_str: str, description: str = "") -> dict:
    canonical = id_str.split(":")[-1]
    for key, info in DEVICE_KNOWLEDGE.items():
        if key.lower() in canonical.lower():
            return dict(info)
    if description:
        desc_lower = description.lower()
        if "regulator" in desc_lower or "ldo" in desc_lower:
            return {"type": "regulator", "vague": True}
        if "mcu" in desc_lower or "microcontroller" in desc_lower:
            return {"voltage": 3.3, "decoupling": True}
        if "sensor" in desc_lower:
            return {"type": "sensor"}
        if "connector" in desc_lower or "usb" in desc_lower or "barrel" in desc_lower:
            return {"type": "connector"}
    return {}
