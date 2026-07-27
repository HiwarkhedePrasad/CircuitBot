"""RapidFuzz fallback matcher and OOD (Out-of-Distribution) guardrail.

When the RAG pipeline returns zero or low-confidence results, this module
provides a fuzzy string matching fallback against known components, and an
OOD guardrail that detects queries outside the known domain before search.

References:
    Khandakar227/circuitlm/agent_system/src/erc/checkers.py (fuzzy.py pattern)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Fuzzy corpus ──────────────────────────────────────────────────────────
# Built lazily from the kicad_rag SQLite DB + component_catalog + aliases.

_CORPUS: list[dict] | None = None

# Known aliases for common parts that wouldn't match via id_str alone
_KNOWN_ALIASES: dict[str, str] = {
    "AMS1117": "Regulator_Linear:AMS1117-3.3",
    "AMS1117-3.3": "Regulator_Linear:AMS1117-3.3",
    "AMS1117-5.0": "Regulator_Linear:AMS1117-5.0",
    "LM35": "Sensor_Temperature:LM35-LP",
    "LM35DZ": "Sensor_Temperature:LM35-LP",
    "ESP32": "MCU_Espressif:ESP32-WROOM-32E",
    "ESP32-S3": "MCU_Espressif:ESP32-S3-WROOM-1",
    "ESP32-C3": "MCU_Espressif:ESP32-C3-MINI-1",
    "ESP8266": "MCU_Espressif:ESP8266-ESP01",
    "STM32F103": "MCU_STMicroelectronics:STM32F103C8",
    "STM32F411": "MCU_STMicroelectronics:STM32F411CE",
    "STM32F401": "MCU_STMicroelectronics:STM32F401CC",
    "RP2040": "MCU_Raspberry_Pi:RP2040",
    "RP2350": "MCU_Raspberry_Pi:RP2350",
    "ATMEGA328P": "MCU_Microchip:ATMEGA328P",
    "ATTINY85": "MCU_Microchip:ATTINY85",
    "CP2102": "Interface_USB:CP2102",
    "CP2104": "Interface_USB:CP2104",
    "CH340": "Interface_USB:CH340",
    "CH340G": "Interface_USB:CH340",
    "FT232": "Interface_USB:FTDI_UART",
    "FT232RL": "Interface_USB:FTDI_UART",
    "DS18B20": "Sensor_Temperature:DS18B20",
    "TMP117": "Sensor_Temperature:TMP117",
    "BME280": "Sensor_Temperature:BME280",
    "BMP280": "Sensor_Temperature:BMP280",
    "MPU6050": "Sensor_Motion:MPU6050",
    "SSD1306": "Display_Graphic:SSD1306",
    "OLED 128x64": "Display_Graphic:SSD1306",
    "NE555": "Timer:NE555P",
    "NE555P": "Timer:NE555P",
    "LM7805": "Regulator_Linear:LM7805",
    "LM7805CT": "Regulator_Linear:LM7805",
    "AMS1117-1.8": "Regulator_Linear:AMS1117-1.8",
    "USBLC6-2": "Power_Protection:USBLC6-2SC6",
    "USBLC6-2SC6": "Power_Protection:USBLC6-2SC6",
    "TPD6S300A": "Power_Protection:TPD6S300A",
    "BSS138": "Transistor_FET:BSS138",
    "2N7002": "Transistor_FET:2N7002",
    "1N4148": "Diode:1N4148",
    "1N4007": "Diode:1N4007",
    "BC547": "Transistor_BJT:BC547",
    "BC548": "Transistor_BJT:BC548",
    "2N2222": "Transistor_BJT:2N2222",
    "WROOM-32": "MCU_Espressif:ESP32-WROOM-32E",
    "WROOM-32E": "MCU_Espressif:ESP32-WROOM-32E",
    "MAX31865": "Sensor_Temperature:MAX31865",
    "MCP23017": "Interface_GPIO:MCP23017",
    "MCP4725": "Analog_DAC:MCP4725",
    "PCF8574": "Interface_GPIO:PCF8574",
    "PCF8575": "Interface_GPIO:PCF8575",
}

# ── Corpus building ───────────────────────────────────────────────────────


def _load_corpus() -> list[dict]:
    """Lazy-build the fuzzy match corpus from SQLite + catalog + aliases.

    Entries with the same ``id_str`` are merged (aliases are unioned) so
    that catalog descriptions enrich DB entries and vice versa.
    """
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS

    corpus_map: dict[str, dict] = {}

    def _add(id_str: str, text: str, category: str, source: str, aliases: list[str]):
        if id_str in corpus_map:
            existing = corpus_map[id_str]
            existing.setdefault("aliases", [])
            for a in aliases:
                if a not in existing["aliases"]:
                    existing["aliases"].append(a)
            if source in ("kicad_rag",) and existing.get("source") == "catalog":
                existing["source"] = "catalog+kicad_rag"
            return
        corpus_map[id_str] = {
            "id_str": id_str,
            "text": text,
            "category": category,
            "source": source,
            "aliases": list(aliases),
        }

    # 1) From component_catalog
    try:
        from agent.knowledge.component_catalog import KNOWN_COMPONENTS
        for key, entry in KNOWN_COMPONENTS.items():
            id_str = entry.get("id_str", "")
            if id_str:
                _add(
                    id_str,
                    entry.get("description", key),
                    entry.get("category", ""),
                    "catalog",
                    [key],
                )
            for alt in entry.get("alternatives", []):
                if alt and alt != id_str:
                    _add(
                        alt,
                        f"Alternative for {key}",
                        entry.get("category", ""),
                        "catalog_alt",
                        [key],
                    )
    except Exception as e:
        logger.warning("Failed to load component_catalog for fuzzy corpus: %s", e)

    # 2) From SQLite DB
    try:
        import sqlite3
        from kicad_rag.constants import SQLITE_PATH
        if SQLITE_PATH.is_file():
            con = sqlite3.connect(SQLITE_PATH)
            rows = con.execute(
                "SELECT id_str, text, footprint FROM symbols"
            ).fetchall()
            con.close()
            for id_str, text, _ in rows:
                cat = id_str.split(":")[0] if ":" in id_str else ""
                aliases = _extract_aliases(id_str)
                _add(id_str, text or id_str, cat, "kicad_rag", aliases)
            logger.info("Loaded %d symbols from kicad_rag DB", len(rows))
    except Exception as e:
        logger.warning("Failed to load kicad_rag DB for fuzzy corpus: %s", e)

    # 3) From known aliases
    for alias, id_str in _KNOWN_ALIASES.items():
        _add(
            id_str,
            f"{alias} — {id_str}",
            id_str.split(":")[0] if ":" in id_str else "",
            "alias",
            [alias],
        )

    _CORPUS = sorted(corpus_map.values(), key=lambda c: c["id_str"])
    logger.info("Fuzzy corpus built: %d entries", len(_CORPUS))
    return _CORPUS


def _extract_aliases(id_str: str) -> list[str]:
    """Extract common aliases from an id_str (e.g. part name without prefix)."""
    aliases = []
    if ":" in id_str:
        part = id_str.split(":", 1)[1]
        aliases.append(part)
        base = re.split(r"[-_]", part)[0]
        if base != part:
            aliases.append(base)
    return aliases


# ── Fuzzy matching ────────────────────────────────────────────────────────


def fuzzy_fallback(
    query: str,
    top_k: int = 5,
    min_score: float = 60.0,
    category_filter: str | None = None,
) -> list[dict]:
    """Fallback fuzzy search using RapidFuzz.

    Returns candidates with ``id_str``, ``score`` (0-100), ``text``, and
    ``source``.  Only returns results above *min_score*.
    """
    corpus = _load_corpus()
    if not corpus:
        return []

    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        logger.warning("rapidfuzz not installed — fuzzy fallback unavailable")
        return []

    # Filter by category if specified
    candidates = corpus
    if category_filter:
        cat_lower = category_filter.lower()
        candidates = [
            c for c in corpus
            if cat_lower in c.get("category", "").lower()
            or any(cat_lower in a.lower() for a in c.get("aliases", []))
        ]
        if not candidates:
            candidates = corpus  # fall back to full corpus if filter matches nothing

    choices = {
        c["id_str"]: (
            f"{c['id_str']} {c.get('text', '')} "
            f"{' '.join(c.get('aliases', []))} "
            f"{c.get('category', '')}"
        )
        for c in candidates
    }

    results = process.extract(
        query, choices,
        scorer=fuzz.token_sort_ratio,
        limit=top_k * 2,
    )

    out = []
    for match_str, score, _ in results:
        if score < min_score:
            continue
        cid = match_str
        entry = next((c for c in corpus if c["id_str"] == cid), None)
        if entry is None:
            continue
        out.append({
            "id_str": cid,
            "score": round(score / 10.0, 1),  # normalize to 0-10 scale
            "score_raw": round(score, 1),
            "text": entry.get("text", ""),
            "category": entry.get("category", ""),
            "source": entry.get("source", "fuzzy"),
            "justification": f"Fuzzy fallback (token_sort_ratio={score:.0f})",
        })
        if len(out) >= top_k:
            break

    return out


def fuzzy_search_exact(query: str) -> Optional[dict]:
    """Try exact match against all known id_strs (fast, no RapidFuzz)."""
    corpus = _load_corpus()
    if not corpus:
        return None
    q = query.strip().upper()
    # Exact id_str match
    for c in corpus:
        if c["id_str"].upper() == q:
            return {
                "id_str": c["id_str"],
                "score": 10,
                "text": c.get("text", ""),
                "category": c.get("category", ""),
                "source": "fuzzy_exact",
                "justification": "Exact fuzzy corpus match",
            }
        # Exact alias match
        if any(a.upper() == q for a in c.get("aliases", [])):
            return {
                "id_str": c["id_str"],
                "score": 10,
                "text": c.get("text", ""),
                "category": c.get("category", ""),
                "source": "fuzzy_alias",
                "justification": "Exact alias match in fuzzy corpus",
            }
    return None


def fuzzy_search_partial(query: str) -> list[dict]:
    """Quick partial substring match against the corpus (no RapidFuzz needed)."""
    corpus = _load_corpus()
    if not corpus:
        return []
    q = query.strip().upper()
    tokens = [t for t in re.split(r"[\s\-_/:]+", q) if len(t) >= 3]
    if not tokens:
        return []

    results: list[tuple[dict, int]] = []
    for c in corpus:
        text = (c["id_str"] + " " + c.get("text", "") + " "
                + " ".join(c.get("aliases", []))).upper()
        match_count = sum(1 for t in tokens if t in text)
        if match_count >= min(2, len(tokens)) or any(
            len(t) >= 4 and t in c["id_str"].upper() for t in tokens
        ):
            score = round(match_count / len(tokens) * 10, 1) if tokens else 0
            results.append((c, max(score, 5)))
    results.sort(key=lambda x: -x[1])
    return [
        {
            "id_str": c["id_str"],
            "score": min(s, 10),
            "text": c.get("text", ""),
            "category": c.get("category", ""),
            "source": "fuzzy_partial",
            "justification": f"Partial substring match (score={s})",
        }
        for c, s in results[:5]
    ]


# ── OOD Guardrail ──────────────────────────────────────────────────────────

_OOD_CATEGORIES: list[str] = [
    "Regulator_Linear", "Regulator_Switching",
    "MCU_Espressif", "MCU_STMicroelectronics", "MCU_Raspberry_Pi",
    "Sensor_Temperature", "Sensor_Motion", "Sensor_Environmental",
    "Connector", "Interface_USB", "Interface_UART",
    "Device", "Diode", "Transistor_FET", "Transistor_BJT",
    "Display_Graphic", "Timer",
    "Amplifier_Operational", "Analog_ADC", "Analog_DAC",
    "Power_Protection", "Battery_Management",
    "Filter", "Oscillator", "Crystal",
]

# Category-level keywords for fast guardrail pass
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "regulator": ["regulator", "ldo", "buck", "boost", "vreg", "voltage regulator",
                  "step-down", "step-up", "power supply", "3.3v", "5v", "1.8v",
                  "3v3", "vin", "vout"],
    "mcu": ["mcu", "microcontroller", "esp32", "stm32", "rp2040", "arduino",
            "processor", "cpu", "wroom", "devkit", "soc", "module", "chip"],
    "sensor": ["sensor", "temperature", "humidity", "pressure", "motion",
               "accelerometer", "gyroscope", "imu", "proximity", "tof", "laser",
               "ultrasonic", "pir", "photodiode", "thermistor", "thermocouple",
               "halleffect", "hall", "magnetometer"],
    "connector": ["connector", "header", "usb", "jack", "terminal", "socket",
                  "plug", "receptacle", "pins", "pin header", "screw"],
    "interface": ["uart", "usb-uart", "usb to uart", "serial", "bridge",
                  "cp210", "ch340", "ft232", "transceiver", "can", "rs485"],
    "passive": ["resistor", "capacitor", "inductor", "led", "diode",
                "transistor", "mosfet", "bjt", "crystal",
                "ceramic", "electrolytic", "ferrite", "polyfuse", "fuse",
                "resonator", "potentiometer", "trim pot"],
    "display": ["display", "oled", "lcd", "screen", "graphic", "ssd1306",
                "tft", "eink", "seven segment"],
    "timer": ["timer", "ne555", "555", "oscillator", "astable", "monostable"],
    "opamp": ["opamp", "operational amplifier", "amplifier", "comparator",
              "differential"],
    "protection": ["esd", "protection", "tvs", "polyfuse", "fuse",
                   "suppressor"],
    "wireless": ["rf", "radio", "antenna", "wifi", "ble", "bluetooth",
                 "zigbee", "lora", "modem", "sim", "cellular", "gps",
                 "gnss", "nfc", "433mhz", "2.4ghz", "sub-ghz"],
    "audio": ["audio", "speaker", "buzzer", "microphone", "mic", "amplifier",
              "codec", "dac"],
}

# Generic part families that would never be OOD (Device:*, etc.)
_GENERIC_PREFIXES = frozenset([
    "Device", "Connector", "Diode",
])


def _category_from_query(query: str) -> str | None:
    """Map a user query to a known category, or None if unrecognized."""
    q = query.lower().strip()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    # Check if it looks like a part number (uppercase + digits)
    if re.search(r'[A-Z]{2,}\d+', query):
        return "part_number"
    return None


def is_ood_quick(query: str) -> bool:
    """Fast keyword-based OOD check — no model loading required.

    Returns True if the query looks out-of-domain (no matching category).
    """
    if not query or not query.strip():
        return True
    return _category_from_query(query) is None


def describe_ood(query: str) -> str:
    """Return a human-readable explanation of why query was flagged OOD."""
    cat = _category_from_query(query)
    if cat is None:
        return (
            f"Query '{query}' does not match any known component category. "
            f"Known categories: regulator, mcu, sensor, connector, interface, "
            f"passive, display, timer, opamp, protection, wireless, audio."
        )
    if cat == "part_number":
        return f"Query '{query}' looks like a part number — will search KiCad library directly."
    return ""


# ── Embedding-based OOD (higher accuracy, requires model) ──────────────────

_OOD_MODEL = None
_OOD_CENTROIDS: dict[str, "np.ndarray"] | None = None


def _ensure_ood_model():
    """Lazy-load fastembed for OOD detection."""
    global _OOD_MODEL
    if _OOD_MODEL is not None:
        return
    try:
        from fastembed import TextEmbedding
        _OOD_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    except Exception as e:
        logger.debug("OOD model load failed: %s", e)


def _compute_centroids():
    """Compute category centroids from the corpus."""
    global _OOD_CENTROIDS
    if _OOD_CENTROIDS is not None:
        return
    _ensure_ood_model()
    if _OOD_MODEL is None:
        return

    corpus = _load_corpus()
    cat_texts: dict[str, list[str]] = {}
    for c in corpus:
        cat = c.get("category", "")
        if cat in _GENERIC_PREFIXES:
            continue
        if cat not in cat_texts:
            cat_texts[cat] = []
        cat_texts[cat].append(f"{c['id_str']} {c.get('text', '')}")

    import numpy as np
    _OOD_CENTROIDS = {}
    for cat, texts in cat_texts.items():
        if not texts:
            continue
        try:
            vecs = list(_OOD_MODEL.embed(texts[:50]))  # sample first 50
            if vecs:
                _OOD_CENTROIDS[cat] = np.mean(np.array(vecs), axis=0)
        except Exception:
            continue


def is_ood_embedding(query: str, threshold: float = 0.35) -> tuple[bool, float, str]:
    """Embedding-based OOD detection.

    Returns:
        (is_out_of_domain, min_distance, closest_category)
    """
    _compute_centroids()
    _ensure_ood_model()
    if _OOD_MODEL is None or _OOD_CENTROIDS is None:
        return is_ood_quick(query), 0.0, ""

    import numpy as np
    try:
        qvec = np.array(list(_OOD_MODEL.embed([query]))[0], dtype=np.float32)
    except Exception:
        return is_ood_quick(query), 0.0, ""

    min_dist = float("inf")
    closest_cat = ""
    for cat, centroid in _OOD_CENTROIDS.items():
        dist = np.linalg.norm(qvec - centroid)
        if dist < min_dist:
            min_dist = dist
            closest_cat = cat

    is_ood = min_dist > threshold
    return is_ood, round(min_dist, 4), closest_cat


# ── Public convenience API ─────────────────────────────────────────────────


def search_with_fallback(
    query: str,
    top_k: int = 5,
    category_filter: str | None = None,
) -> list[dict]:
    """Try exact → fuzzy → partial fallback chain, returning candidates."""
    # Level 1: exact match
    exact = fuzzy_search_exact(query)
    if exact:
        return [exact]

    # Level 2: RapidFuzz token_sort_ratio
    fuzzy_results = fuzzy_fallback(query, top_k=top_k, min_score=60.0,
                                   category_filter=category_filter)
    if fuzzy_results:
        return fuzzy_results

    # Level 3: partial substring match (no dependencies)
    partial = fuzzy_search_partial(query)
    return partial


def guardrail_check(query: str) -> dict:
    """Run OOD guardrail and return structured result.

    Uses keyword-based detection as the primary signal (fast, accurate for
    known categories).  Embedding-based centroid distance is included for
    diagnostics / future tuning but does NOT override the keyword result.

    Returns:
        {
            "is_ood": bool,
            "distance": float,
            "category": str,
            "message": str,
        }
    """
    is_ood_kw = is_ood_quick(query)
    cat_kw = _category_from_query(query) or "unknown"
    _, dist, cat_emb = is_ood_embedding(query)
    return {
        "is_ood": is_ood_kw,
        "distance": float(dist),
        "category": cat_emb or cat_kw,
        "message": describe_ood(query) if is_ood_kw else "",
    }
