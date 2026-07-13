import json
import time
import uuid
import re

from server.state import rag, design_lock, session_manager

MAX_SESSION_AGE_SECONDS = 3600  # 1 hour — sessions older than this are evicted


class ChatSession:
    def __init__(self):
        self.chat_history = []
        self.thought_stream = []
        self.board_model = None
        self.proposals = {}
        self.last_active = time.time()
        # Learning: user preferences and correction history
        self.preferences = {
            "preferred_parts": {},      # {component_type: preferred_part_id}
            "rejected_parts": [],       # list of part IDs user rejected
            "preferred_values": {},     # {component_type: preferred_value}
            "correction_count": 0,      # how many times user modified a design
            "design_patterns": [],      # patterns the user tends to use
        }


CHAT_SESSIONS: dict[str, ChatSession] = {}


def _evict_stale_sessions():
    """Remove sessions older than MAX_SESSION_AGE_SECONDS to prevent memory leak."""
    now = time.time()
    stale = [sid for sid, sess in CHAT_SESSIONS.items()
             if now - sess.last_active > MAX_SESSION_AGE_SECONDS]
    for sid in stale:
        CHAT_SESSIONS.pop(sid, None)


def record_user_correction(session: ChatSession, mod_type: str, target: dict, value: dict):
    """Record a user correction to learn preferences."""
    session.preferences["correction_count"] += 1

    if mod_type == "value_change":
        ref = target.get("ref", "")
        val = value.get("value", "")
        if ref and val:
            # Extract component type from ref (e.g., "R1" -> "resistor", "C2" -> "capacitor")
            comp_type = _ref_to_type(ref)
            if comp_type:
                session.preferences["preferred_values"][comp_type] = val

    elif mod_type == "part_swap":
        ref = target.get("ref", "")
        part_id = value.get("part_id", "")
        if ref and part_id:
            comp_type = _ref_to_type(ref)
            if comp_type:
                session.preferences["preferred_parts"][comp_type] = part_id

    elif mod_type == "remove_component":
        ref = target.get("ref", "")
        if ref:
            session.preferences["rejected_parts"].append(ref)


def get_user_preferences(session: ChatSession) -> dict:
    """Get user preferences for use in component selection."""
    return dict(session.preferences)


def _ref_to_type(ref: str) -> str:
    """Convert a reference designator prefix to a component type."""
    import re
    match = re.match(r"^([A-Za-z]+)", ref)
    if not match:
        return ""
    prefix = match.group(1).upper()
    type_map = {
        "R": "resistor", "C": "capacitor", "L": "inductor",
        "D": "diode", "Q": "transistor", "U": "ic",
        "J": "connector", "SW": "switch", "Y": "crystal",
        "F": "fuse", "BT": "battery", "M": "motor",
        "LS": "speaker", "LED": "led",
    }
    return type_map.get(prefix, prefix.lower())


def _create_empty_board_model():
    return {
        "components": [],
        "traces": [],
        "vias": [],
        "nets": [],
        "outline_segments": [],
        "_render_from_model": True,
    }


def _get_or_create_chat_session(session_id: str) -> ChatSession:
    _evict_stale_sessions()
    session = CHAT_SESSIONS.get(session_id)
    if session is None:
        session = ChatSession()
        ds = session_manager.get_or_create(session_id)
        with design_lock:
            existing_board = ds.get_design().get("board_model")
        session.board_model = json.loads(json.dumps(existing_board)) if existing_board else _create_empty_board_model()
        CHAT_SESSIONS[session_id] = session
    else:
        session.last_active = time.time()
    return session


def _prune_legacy_mock_history(session: ChatSession) -> None:
    session.chat_history = [
        msg for msg in session.chat_history
        if not (isinstance(msg, dict) and msg.get("role") == "assistant" and "Mock response" in str(msg.get("content")))
    ]


def _proposal_type_matches(query: str, result) -> bool:
    query_upper = query.upper()
    id_str = str(getattr(result, "id_str", "") or "")
    text = str(getattr(result, "text", "") or "")
    haystack = f"{id_str} {text}".upper()

    if any(token in query_upper for token in ("STATUS LED", "INDICATOR", " LED")):
        return "LED" in haystack and "RGB" not in haystack
    if any(token in query_upper for token in ("BUTTON", "TACTILE", "SWITCH", "PUSHBUTTON", "PUSH BUTTON")):
        return any(token in haystack for token in ("SW_PUSH", "SWITCH", "BUTTON", "TACTILE")) and "RGB" not in haystack
    if "ESP32" in query_upper:
        return "ESP32" in haystack
    return True


def _proposal_search_query(text: str) -> str:
    text_lower = text.lower().strip()
    if "status led" in text_lower or text_lower == "led":
        return "generic status led"
    if any(token in text_lower for token in ("button", "switch", "pushbutton", "push button", "tactile")):
        return "tactile push button switch"
    return text


def _load_real_footprint_geometry(symbol_id: str | None, explicit_footprint: str | None = None) -> dict | None:
    try:
        footprint = explicit_footprint or ""
        if symbol_id:
            from agent.tools import fetch_footprint

            info = fetch_footprint(symbol_id) or {}
            footprint = footprint or info.get("footprint", "")
        if not footprint:
            return None

        from kicad_rag.store import footprint_path_for
        from pcb_design.pcb_import import _parse_footprint, parse_sexp

        fp_path = footprint_path_for(footprint)
        if not fp_path.is_file():
            return None
        ast = parse_sexp(fp_path.read_text(encoding="utf-8"))
        if isinstance(ast, list) and ast and ast[0] not in ("footprint", "module"):
            ast[0] = "footprint"
        parsed = _parse_footprint(ast)
        if not parsed:
            return None
        return {
            "footprint": footprint,
            "pads": [
                {
                    "num": str(p.number),
                    "number": str(p.number),
                    "name": str(p.number),
                    "x": p.x,
                    "y": p.y,
                    "width": p.width,
                    "height": p.height,
                    "shape": p.shape,
                    "type": p.type,
                    "rotation": p.rotation,
                    "drill": p.drill,
                    "drill_width": p.drill_width,
                    "drill_offset_x": p.drill_offset_x,
                    "drill_offset_y": p.drill_offset_y,
                    "roundrect_rratio": p.roundrect_rratio,
                    "rect_delta_x": p.rect_delta_x,
                    "rect_delta_y": p.rect_delta_y,
                    "layers": p.layers,
                    "targetNet": "",
                }
                for p in parsed.pads
            ],
            "graphics": parsed.graphics,
        }
    except Exception as exc:
        print(f"Footprint geometry load failed for {symbol_id or explicit_footprint}: {exc}")
        return None


def _build_component_proposal_from_query(text: str):
    query = _proposal_search_query(text)
    results = rag.search(query, k=8)
    if not results:
        return None
    filtered = [r for r in results if _proposal_type_matches(text, r)]
    best = filtered[0] if filtered else results[0]
    pin_defs = rag.pins(best.id_str) or []
    if not pin_defs:
        pin_defs = [{"num": "1", "name": "P1"}, {"num": "2", "name": "P2"}]

    pin_count = len(pin_defs)
    half_span = max((pin_count - 1) * 0.6 / 2, 0.0)
    pins = []
    for idx, pin in enumerate(pin_defs):
        offset_y = half_span - idx * 0.6
        side = -1 if idx % 2 == 0 else 1
        pins.append({
            "num": str(pin.get("number") or pin.get("num") or idx + 1),
            "name": str(pin.get("name") or pin.get("label") or f"P{idx + 1}"),
            "x": side * 1.4,
            "y": offset_y,
            "width": 0.6,
            "height": 0.7,
            "targetNet": "",
        })

    proposal = {
        "type": "add_component",
        "component": {
            "name": getattr(best, "text", "") or best.id_str.split(":")[-1],
            "symbol_id": best.id_str,
            "footprint": getattr(best, "footprint", "") or "",
            "body": {
                "width": 3.2 if pin_count > 4 else 2.4,
                "height": max(1.6, pin_count * 0.5),
            },
            "pins": pins,
        },
        "id": str(uuid.uuid4()),
    }
    geometry = _load_real_footprint_geometry(best.id_str, proposal["component"]["footprint"])
    if geometry:
        proposal["component"]["footprint"] = geometry.get("footprint", proposal["component"]["footprint"])
        logical_pins = proposal["component"]["pins"]
        logical_by_num = {
            str(pin.get("num", pin.get("number", ""))): pin
            for pin in logical_pins
        }
        merged_pins = []
        for pad in geometry.get("pads", []):
            pad_num = str(pad.get("num", pad.get("number", "")))
            logical_pin = logical_by_num.get(pad_num)
            if logical_pin:
                merged_pad = pad.copy()
                if "name" in logical_pin:
                    merged_pad["name"] = logical_pin["name"]
                if "targetNet" in logical_pin:
                    merged_pad["targetNet"] = logical_pin["targetNet"]
                merged_pins.append(merged_pad)
            else:
                merged_pins.append(pad)
        proposal["component"]["pins"] = merged_pins or logical_pins
        proposal["component"]["graphics"] = geometry.get("graphics", [])
        pad_xs = [float(p.get("x", 0) or 0) for p in proposal["component"]["pins"]]
        pad_ys = [float(p.get("y", 0) or 0) for p in proposal["component"]["pins"]]
        if pad_xs and pad_ys:
            proposal["component"]["body"] = {
                "width": max(max(pad_xs) - min(pad_xs) + 1.8, 2.4),
                "height": max(max(pad_ys) - min(pad_ys) + 1.8, 1.8),
            }
    return proposal
