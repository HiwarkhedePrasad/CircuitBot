import json
import time
import uuid

from server.state import rag, design_lock, LAST_DESIGN, _WIREBENDER_LAYOUT


class ChatSession:
    def __init__(self):
        self.chat_history = []
        self.board_model = None
        self.proposals = {}
        self.last_active = time.time()


CHAT_SESSIONS: dict[str, ChatSession] = {}


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
    session = CHAT_SESSIONS.get(session_id)
    if session is None:
        session = ChatSession()
        with design_lock:
            existing_board = LAST_DESIGN.get("board_model")
        session.board_model = json.loads(json.dumps(existing_board)) if existing_board else _create_empty_board_model()
        CHAT_SESSIONS[session_id] = session
    return session


def _prune_legacy_mock_history(session: ChatSession) -> None:
    session.chat_history = [
        msg for msg in session.chat_history
        if not (isinstance(msg, dict) and msg.get("role") == "assistant" and "Mock response" in str(msg.get("content")))
    ]


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
    results = rag.search(text, k=1)
    if not results:
        return None
    best = results[0]
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
