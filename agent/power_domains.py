"""Canonical voltage-domain mapping for power-net classification.

Keeps power rails distinct so VCC (5V) and 3V3 (3.3V) are never
merged into the same net, preventing accidental power-rail shorts.

Extensions per design:
    Pass ``extra_domains: dict[str, str]`` to ``classify()`` to add
    project-specific rails (e.g. ``1V8→1.8V``, ``12V→12V``).
"""

from __future__ import annotations

# ── Canonical rail mapping ──────────────────────────────────────────────
# Left: pin names seen on components.  Right: the stable net name.
# Keys are uppercase.  Order matters: first match wins.

_DEFAULT_DOMAINS: dict[str, str] = {
    "GND":   "GND",
    "VSS":   "GND",
    "VEE":   "GND",
    "VIN":   "VIN",
    "VCC":   "VCC",       # keep distinct from 3V3 — usually 5V or battery
    "VDD":   "VDD",       # keep distinct on purpose (may be 1.8V, 3V3, 5V)
    "VBUS":  "VBUS",
    "5V":    "5V",
    "5V0":   "5V",
    "3V3":   "3V3",
    "3.3V":  "3V3",
    "3V0":   "3V3",
    "1V8":   "1V8",
    "1.8V":  "1V8",
    "VBAT":  "VBAT",
    "VSYS":  "VSYS",
    "VUSB":  "VUSB",
    "AVCC":  "AVCC",
    "AVDD":  "AVDD",
    "DVDD":  "DVDD",
    "VREFP": "VREFP",
    "VREFN": "VREFN",
}

# Nets that are always power/ground (PUBLIC — import from here)
POWER_NETS: set[str] = {
    "GND", "VIN", "VCC", "VDD", "VBUS", "5V", "3V3", "3V0",
    "1V8", "VBAT", "VSYS", "VUSB", "AVCC", "AVDD", "DVDD",
    "VREFP", "VREFN", "VEE", "VSS", "VOUT", "V+", "V-",
    "+3.3V", "+5V", "3.3V", "5V0",
}
GND_NETS: set[str] = {
    "GND", "VSS", "VEE", "GROUND", "AGND", "DGND", "PGND",
    "GNDA", "GNDD", "EP", "EPAD", "0V", "SHIELD",
}


def classify(pin_name: str, *,
             extra_domains: dict[str, str] | None = None) -> str | None:
    """Return the canonical rail name for *pin_name*, or ``None``."""
    cleaned = pin_name.strip().upper().lstrip("+").lstrip("-")
    # Merge extra domains first so they take priority
    domains = dict(_DEFAULT_DOMAINS)
    if extra_domains:
        for k, v in extra_domains.items():
            domains[k.upper().lstrip("+").lstrip("-")] = v
    return domains.get(cleaned, None)


def is_power(pin_name: str, *,
             extra_domains: dict[str, str] | None = None) -> bool:
    """Return ``True`` if *pin_name* is a known power/ground rail."""
    rail = classify(pin_name, extra_domains=extra_domains)
    if rail is None:
        return False
    return rail in POWER_NETS


def is_gnd(pin_name: str) -> bool:
    cleaned = pin_name.strip().upper().lstrip("+").lstrip("-")
    return cleaned in GND_NETS


def get_trace_width(rail: str) -> float | None:
    """Return recommended trace width (mm) for a power rail, or ``None``."""
    table: dict[str, float] = {
        "GND":  0.5,
        "VIN":  0.8,
        "5V":   0.5,
        "3V3":  0.5,
        "VBAT": 0.8,
        "VSYS": 0.5,
        "VUSB": 0.5,
    }
    return table.get(rail)
