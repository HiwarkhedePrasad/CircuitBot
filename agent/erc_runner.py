"""KiCad Electrical Rules Check (ERC) runner via kicad-cli.

Writes a .kicad_sch string to a temp file, runs kicad-cli ERC
with JSON output, and returns structured results.
"""

import json
import os
import re
import subprocess
import tempfile

KICAD_CLI = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Programs", "KiCad", "10.0", "bin", "kicad-cli.exe",
)

# Pattern for "Symbol U1 Pin 2 [GND, Power input, Line]"
_ITEM_DESC_RE = re.compile(
    r"^Symbol\s+(?P<ref>\S+)\s+Pin\s+(?P<pin_num>\S+)\s+\[(?P<name>[^,]+)"
)

FIXABLE_TYPES = {
    "pin_not_connected",
    "unconnected_wire_endpoint",
    "wire_dangling",
    "power_pin_not_driven",
}

# These are warnings about library versions — not fixable in our pipeline
NON_FIXABLE_TYPES = {
    "lib_symbol_mismatch",
    "lib_symbol_issues",
}


def _parse_item_description(desc: str) -> dict:
    """Parse 'Symbol U1 Pin 2 [GND, Power input, Line]' into structured data."""
    m = _ITEM_DESC_RE.match(desc)
    if m:
        return {
            "ref_des": m.group("ref"),
            "pin_num": m.group("pin_num"),
            "pin_name": m.group("name").strip(),
        }
    return {}


def run_kicad_erc(sch_text: str) -> dict | None:
    """Run KiCad ERC on a schematic string.

    Args:
        sch_text: The full .kicad_sch file content as a string.

    Returns:
        Dict with keys:
            errors: list[dict] — each error with type, description,
                    position, parsed item info, and severity.
            warnings: list[dict] — same structure for warnings.
            total_errors: int
            total_warnings: int
            fixable_count: int
            fixable: list[dict] — errors of fixable types with
                     pin_key (e.g. "U1:2"), net hint, position.
        None if kicad-cli is not available or fails.
    """
    if not os.path.isfile(KICAD_CLI):
        return None

    tmp_sch = None
    tmp_out = None
    try:
        tmp_sch = tempfile.NamedTemporaryFile(
            suffix=".kicad_sch", mode="w", encoding="utf-8", delete=False
        )
        tmp_sch.write(sch_text)
        tmp_sch.close()

        tmp_out = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w+", delete=False
        )
        tmp_out.close()

        result = subprocess.run(
            [KICAD_CLI, "sch", "erc",
             "--format", "json",
             "--severity-all",
             "-o", tmp_out.name,
             tmp_sch.name],
            capture_output=True, text=True, timeout=60,
        )

        with open(tmp_out.name, "r", encoding="utf-8") as f:
            raw = json.load(f)

        errors = []
        warnings = []
        fixable = []

        for sheet in raw.get("sheets", []):
            for v in sheet.get("violations", []):
                entry = {
                    "type": v.get("type", ""),
                    "severity": v.get("severity", ""),
                    "description": v.get("description", ""),
                    "items": [],
                }
                for item in v.get("items", []):
                    parsed = _parse_item_description(
                        item.get("description", "")
                    )
                    pos = item.get("pos", {})
                    entry["items"].append({
                        "description": item.get("description", ""),
                        "position": pos,
                        "parsed": parsed,
                        "uuid": item.get("uuid", ""),
                    })

                if entry["severity"] == "error":
                    errors.append(entry)
                    if entry["type"] in FIXABLE_TYPES:
                        for it in entry["items"]:
                            p = it.get("parsed", {})
                            if p.get("ref_des") and p.get("pin_num"):
                                fixable.append({
                                    "type": entry["type"],
                                    "pin_key": f"{p['ref_des']}:{p['pin_num']}",
                                    "ref_des": p["ref_des"],
                                    "pin_num": p["pin_num"],
                                    "pin_name": p.get("pin_name", ""),
                                    "position": it.get("position", {}),
                                    "description": entry["description"],
                                })
                elif entry["severity"] == "warning":
                    warnings.append(entry)

        return {
            "errors": errors,
            "warnings": warnings,
            "total_errors": len(errors),
            "total_warnings": len(warnings),
            "fixable_count": len(fixable),
            "fixable": fixable,
        }

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"error": str(e), "errors": [], "warnings": [], "total_errors": 0, "total_warnings": 0, "fixable_count": 0, "fixable": []}
    finally:
        for p in (tmp_sch, tmp_out):
            if p is not None:
                try:
                    os.unlink(p.name)
                except OSError:
                    pass
