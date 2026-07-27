"""Subsystem Intent & Query Expander.

Parses subsystem information (name, function, bus, examples) and extracts electrical
intents (target voltage, regulator topology, connector interfaces, sensor buses) to
build enriched search queries for KiCad RAG component retrieval.
"""

from __future__ import annotations
import re


def expand_subsystem_query(subsystem_info: dict) -> list[str]:
    """Extract electrical intents and build enriched search queries for KiCad RAG."""
    name = (subsystem_info.get("subsystem", "") or "").strip()
    func = (subsystem_info.get("function", "") or "").strip()
    
    queries = [name] if name else []
    name_lower = name.lower()
    func_lower = func.lower()
    
    # 1. Power Regulation Intent
    if "regulation" in name_lower or "regulat" in func_lower or "power" in name_lower:
        if any(w in func_lower or w in name_lower for w in ("3.3v", "3v3", "3.3 v")):
            queries.insert(0, "AMS1117-3.3 AP2112K-3.3 3.3V LDO regulator")
        elif any(w in func_lower or w in name_lower for w in ("5v", "5.0v", "5 v")):
            queries.insert(0, "AMS1117-5.0 7805 5V LDO regulator")
        elif "ldo" in func_lower or "linear" in func_lower:
            queries.insert(0, "AMS1117-3.3 AP2112K-3.3 LDO regulator")
            
    # 2. Power Input / USB Intent
    if "power input" in name_lower or "usb" in func_lower or "usb" in name_lower:
        if "c" in func_lower or "type-c" in func_lower or "usb" in name_lower:
            queries.insert(0, "USB_C_Receptacle_USB2.0_16P USB-C connector")
            
    # 3. Temperature Sensing Intent
    if "temperature" in name_lower or "temp" in func_lower or "lm35" in name_lower or "lm35" in func_lower:
        if "lm35" in func_lower or "lm35" in name_lower or "analog" in func_lower or "analog" in name_lower:
            queries.insert(0, "LM35-LP LM35-N LM35 analog temperature sensor")
        elif "1-wire" in func_lower or "onewire" in func_lower or "ds18b20" in func_lower:
            queries.insert(0, "DS18B20 1-Wire temperature sensor")
        else:
            queries.insert(0, "LM35-LP TMP117xxYBG TMP1075DGK temperature sensor")
            
    # 4. Display Intent
    if "display" in name_lower or "oled" in func_lower or "screen" in func_lower:
        queries.insert(0, "Adafruit_SSD1306 OLED display I2C 128x64")
        
    # Deduplicate queries while preserving priority order
    seen = set()
    deduped = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            deduped.append(q)
            
    return deduped if deduped else [name]
