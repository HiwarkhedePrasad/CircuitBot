"""Canonical Component Taxonomy Index.

Maps subsystem intents to industry-standard KiCad 8.0 symbols to grant RRF rank
boosts during RAG hybrid search.
"""

from __future__ import annotations

CANONICAL_TAXONOMY: dict[str, list[str]] = {
    "power_regulation_3v3": [
        "Regulator_Linear:AMS1117-3.3",
        "Regulator_Linear:AP2112K-3.3",
        "Regulator_Linear:MIC5219-3.3YM5",
    ],
    "power_input_usb_c": [
        "Connector:USB_C_Receptacle_USB2.0_16P",
    ],
    "temp_sensor_i2c": [
        "Sensor_Temperature:TMP117xxYBG",
        "Sensor_Temperature:TMP117xxDRV",
        "Sensor_Temperature:TMP1075DGK",
    ],
    "temp_sensor_onewire": [
        "Sensor_Temperature:DS18B20",
    ],
    "mcu_esp32": [
        "RF_Module:ESP32-C3-MINI-1",
        "RF_Module:ESP32-S3-WROOM-1",
        "RF_Module:ESP32-WROOM-32D",
        "MCU_Espressif:ESP32-C3",
    ],
}


def get_canonical_symbols(query: str, library_filter: str = "") -> list[str]:
    """Get list of canonical symbol id_strs matching the given query."""
    q = query.lower()
    matches = []
    
    if "3.3v" in q or "3v3" in q or "regulation" in q:
        matches.extend(CANONICAL_TAXONOMY["power_regulation_3v3"])
    if "usb" in q or "power input" in q:
        matches.extend(CANONICAL_TAXONOMY["power_input_usb_c"])
    if "tmp117" in q or "temperature" in q or "temp" in q:
        matches.extend(CANONICAL_TAXONOMY["temp_sensor_i2c"])
    if "ds18b20" in q or "1-wire" in q:
        matches.extend(CANONICAL_TAXONOMY["temp_sensor_onewire"])
    if "esp32" in q:
        matches.extend(CANONICAL_TAXONOMY["mcu_esp32"])
        
    return matches
