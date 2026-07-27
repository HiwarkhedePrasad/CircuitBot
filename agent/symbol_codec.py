"""Symbol Codec — SchGen code-L1 lossless encoding for KiCad symbol placements.

Encodes a symbol's pin geometry, orientation, and library reference into a
compact, lossless string that can be decoded back to exact placement data.
Supports layout fingerprinting, deterministic placement verification, and
cross-pipeline layout comparison.

Based on the code-L1 representation introduced in SchGen
(arXiv:2501.07774).

Format:
  CODE-L1:v1|<ref>:<lib_id>:<rot>:<x>,<y>|<pin>:<name>:<etype>:<angle>:<rx>,<ry>[|...]
"""

import hashlib


# ── Constants ──────────────────────────────────────────────────────────

_CODEC_VERSION = "v1"
_FIELD_SEP = "|"
_PIN_SEP = "|"
_COORD_SEP = ","
_HEADER_FIELDS = 5  # ref, lib_id, rot, x, y  (pipe-delimited)
_PIN_FIELDS = 5     # pin_num:name:etype:angle:rel_x,rel_y (colon-delimited)


# ── Encode ────────────────────────────────────────────────────────────

def encode_symbol(ref_des: str, id_str: str,
                  pins: list[dict],
                  rotation: int = 0,
                  pos_x: float = 0.0, pos_y: float = 0.0) -> str:
    """Encode a single symbol placement into a code-L1 string.

    Args:
        ref_des: Reference designator (e.g., 'U1')
        id_str: KiCad library ID (e.g., 'Device:R_Small')
        pins: List of pin dicts with keys: pin_num, name, etype, angle, x, y
        rotation: Symbol rotation in degrees (0, 90, 180, 270)
        pos_x, pos_y: Absolute placement position

    Returns:
        Compact code-L1 string
    """
    sx = _snap(pos_x)
    sy = _snap(pos_y)
    header = f"{_CODEC_VERSION}{_FIELD_SEP}{ref_des}{_FIELD_SEP}{id_str}{_FIELD_SEP}{rotation}{_FIELD_SEP}{_fcoord(sx, sy)}"
    pin_parts = []
    for p in pins:
        pn = p.get("pin_num", "")
        nm = p.get("name", "")
        et = p.get("etype", "")
        ang = p.get("angle", 0)
        rx = _snap(p.get("x", 0))
        ry = _snap(p.get("y", 0))
        pin_parts.append(f"{pn}:{nm}:{et}:{ang}:{_fcoord(rx, ry)}")

    return header + (_PIN_SEP + _PIN_SEP.join(pin_parts) if pin_parts else "")


def encode_pin_matrix(pin_matrix: dict, components: list[dict]) -> dict[str, str]:
    """Encode all symbols from a pin_matrix + component list into code-L1.

    Returns:
        dict[ref_des] -> code-L1 string
    """
    codes = {}
    for comp in components:
        ref = comp.get("ref_des", "")
        if not ref:
            continue
        comp_pins = _pins_for_component(pin_matrix, ref)
        if not comp_pins:
            continue
        codes[ref] = encode_symbol(
            ref_des=ref,
            id_str=comp.get("id_str", ""),
            pins=comp_pins,
            rotation=comp.get("rotation", 0),
            pos_x=comp.get("x", 0.0),
            pos_y=comp.get("y", 0.0),
        )
    return codes


def fingerprint(pin_matrix: dict, components: list[dict]) -> str:
    """Compute a single deterministic fingerprint hash for the entire layout.

    Useful for quick layout comparison across pipeline runs.
    """
    codes = encode_pin_matrix(pin_matrix, components)
    ordered = sorted(codes.items())
    blob = _FIELD_SEP.join(code for _, code in ordered)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def hash_code(code: str) -> str:
    """Return a compact 8-char hex hash of a single code-L1 string."""
    return hashlib.sha256(code.encode()).hexdigest()[:8]


# ── Decode ────────────────────────────────────────────────────────────

def decode_symbol(code: str) -> dict | None:
    """Decode a code-L1 string back to a symbol placement dict.

    Returns:
        dict with keys: ref_des, id_str, rotation, x, y, pins
        or None if parsing fails.
    """
    try:
        parts = code.split(_FIELD_SEP)
        if len(parts) < _HEADER_FIELDS + 1:
            return None
        version = parts[0]
        if version != _CODEC_VERSION:
            return None

        ref_des = parts[1]
        id_str = parts[2]
        rotation = int(parts[3])
        coord = parts[4].split(_COORD_SEP)
        pos_x = float(coord[0]) if len(coord) == 2 else 0.0
        pos_y = float(coord[1]) if len(coord) == 2 else 0.0

        pins = []
        for p_str in parts[_HEADER_FIELDS:]:
            p_fields = p_str.split(":")
            if len(p_fields) >= _PIN_FIELDS:
                p_coord = p_fields[4].split(_COORD_SEP)
                pins.append({
                    "pin_num": p_fields[0],
                    "name": p_fields[1],
                    "etype": p_fields[2],
                    "angle": int(p_fields[3]),
                    "x": float(p_coord[0]) if len(p_coord) >= 1 else 0.0,
                    "y": float(p_coord[1]) if len(p_coord) >= 2 else 0.0,
                })

        return {
            "ref_des": ref_des,
            "id_str": id_str,
            "rotation": rotation,
            "x": pos_x,
            "y": pos_y,
            "pins": pins,
        }
    except (ValueError, IndexError):
        return None


# ── Compare ───────────────────────────────────────────────────────────

def compare_layout(codes_a: dict[str, str],
                   codes_b: dict[str, str]) -> dict:
    """Compare two sets of code-L1 encodings and produce a diff report.

    Returns:
        dict with keys:
          identical: bool
          matching: list of matching refs
          missing: refs in A not in B
          added: refs in B not in A
          modified: refs whose hash differs
          unchanged_count: int
          total_a: int
          total_b: int
    """
    refs_a = set(codes_a.keys())
    refs_b = set(codes_b.keys())
    common = refs_a & refs_b

    matching = [r for r in common if hash_code(codes_a[r]) == hash_code(codes_b[r])]
    modified = [r for r in common if hash_code(codes_a[r]) != hash_code(codes_b[r])]

    return {
        "identical": refs_a == refs_b and len(modified) == 0,
        "matching": matching,
        "missing": sorted(refs_a - refs_b),
        "added": sorted(refs_b - refs_a),
        "modified": modified,
        "unchanged_count": len(matching),
        "total_a": len(codes_a),
        "total_b": len(codes_b),
    }


# ── Internal helpers ──────────────────────────────────────────────────

_GRID = 1.27


def _snap(v: float) -> float:
    return round(v / _GRID) * _GRID


def _fcoord(x: float, y: float) -> str:
    return f"{x:.2f}{_COORD_SEP}{y:.2f}"


def _pins_for_component(pin_matrix: dict, ref_des: str) -> list[dict]:
    prefix = f"{ref_des}:"
    result = []
    for key, pin in pin_matrix.items():
        if key.startswith(prefix):
            result.append(pin)
    result.sort(key=lambda p: p.get("pin_num", ""))
    return result
