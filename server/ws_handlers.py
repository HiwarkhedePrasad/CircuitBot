import copy
import json
import uuid

from flask import request
from flask_socketio import emit

from server.state import app, socketio, rag, design_lock, session_manager, _sid_to_chat, _sid_to_chat_lock
from server.chat import (
    CHAT_SESSIONS, _get_or_create_chat_session, _create_empty_board_model,
    _prune_legacy_mock_history, _build_component_proposal_from_query,
    _evict_stale_sessions,
)
from server.agent_runner import _run_agent


def _find_design_session(sid):
    """Find DesignSession by Socket.IO sid, checking both sid and mapped chat session ID.

    After T1 unification, the DesignSession may be keyed by chat_session_id
    (localStorage) rather than request.sid (Socket.IO). This helper checks both.
    """
    ds = session_manager.get(sid)
    if ds:
        return ds
    with _sid_to_chat_lock:
        chat_id = _sid_to_chat.get(sid)
    if chat_id:
        ds = session_manager.get(chat_id)
        if ds:
            return ds
    return None


def _find_agent_events(ds, sid):
    """Find agent events entry by Socket.IO sid, checking both sid and mapped chat session ID."""
    entry = ds.agent_events.get(sid)
    if entry:
        return entry
    with _sid_to_chat_lock:
        chat_id = _sid_to_chat.get(sid)
    if chat_id:
        entry = ds.agent_events.get(chat_id)
        if entry:
            return entry
    return None


def _run_modify(text, routing, sid, session_id):
    """Run the modify pipeline in a background thread.

    NOTE: Uses socketio.emit(event, data, room=sid) — NOT emit() — because
    this runs in a background thread with no request context. The thread-local
    emit() from Flask-SocketIO only works inside request handlers.
    """
    from agent.builder import modify_graph

    ds = session_manager.get_or_create(session_id)

    with design_lock:
        current_design = copy.deepcopy(ds.get_design())

    initial_state = {
        "prompt": text,
        "original_design": current_design,
        "selected_components": current_design.get("selected_components", []),
        "board_model": current_design.get("board_model"),
        "nets": current_design.get("nets", []),
        "modification_type": routing.get("modification_type"),
        "modification_target": routing.get("target"),
        "modification_value": routing.get("value"),
    }

    try:
        result = modify_graph.invoke(initial_state)

        with design_lock:
            ds.set_design({
                "selected_components": result.get("selected_components", []),
            })
            if result.get("board_model"):
                ds.set_design({"board_model": result["board_model"]})
            if result.get("nets"):
                ds.set_design({"nets": result["nets"]})

        mod_type = result.get("modification_type", "unknown")
        target = result.get("modification_target", {})
        value = result.get("modification_value", {})
        ref = target.get("ref", "")
        net = target.get("net", "")
        target_str = ref or net or "design"

        # Record user correction for learning
        from server.chat import CHAT_SESSIONS, record_user_correction
        chat_session = CHAT_SESSIONS.get(session_id)
        if chat_session:
            record_user_correction(chat_session, mod_type, target, value)

        if mod_type == "value_change":
            reply = f"Changed {target_str} to {value.get('value', 'new value')}"
        elif mod_type == "part_swap":
            reply = f"Swapped {target_str} to {value.get('part_id', 'new part')}"
        elif mod_type == "add_component":
            reply = f"Added {value.get('description', 'component')}"
        elif mod_type == "remove_component":
            reply = f"Removed {target_str}"
        elif mod_type == "net_modify":
            reply = f"Modified connections for {target_str}"
        elif mod_type == "reroute":
            reply = f"Updated routing for {target_str}"
        else:
            reply = "Design modified successfully"

        socketio.emit('chat:reply', {'text': reply}, room=sid)
        socketio.emit('tscircuit:board-model-updated', {
            'board_model': ds.get_design().get('board_model')
        }, room=sid)
    except Exception as exc:
        print(f"Modify pipeline failed: {exc}")
        socketio.emit('chat:reply', {
            'text': f"Modification failed: {str(exc)}"
        }, room=sid)

HELP_TEXT = """I'm CircuitBot, an AI PCB design assistant. Here's what I can do:

**Design a circuit** — Say "design a fan controller PCB", "create a power supply", or "build a sensor board"
**Add a component** — Say "add a 10k resistor", "I need an ATMega328P", or "place an LED"
**Modify a design** — Say "change R1 to 10k", "swap U1 for MCP1700", "add a bypass cap", or "remove R3"
**Find a component** — Say "find me a temperature sensor", "search for a 5V regulator"
**Get help** — Say "help" or "what can you do"

After a design is generated, I'll review it and suggest improvements like adding bypass capacitors or protection circuits."""

CLARIFICATION_TEXT = """I'm not sure what you'd like me to do. Here are some things I can help with:

- **Add a component** — e.g., "add a 10k resistor" or "place an LED with resistor"
- **Design a full circuit** — e.g., "design a fan controller board" or "create a USB power supply"
- **Search for a part** — e.g., "find me a temperature sensor" or "search for BME280"

What would you like?"""


# ── Connection Events ───────────────────────────────────────────────────


@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    # Clean up agent events — check both sid and mapped chat session ID (T1 fix)
    with _sid_to_chat_lock:
        chat_id = _sid_to_chat.pop(request.sid, None)
    # Find DesignSession by either key and signal any pending agent events
    ds = _find_design_session(request.sid)
    if ds:
        entry = _find_agent_events(ds, request.sid)
        if entry:
            entry["result"]["approved"] = False
            entry["event"].set()
    # Evict stale ChatSessions to prevent memory leak (T7 fix)
    _evict_stale_sessions()


# ── Agent Approval Events ───────────────────────────────────────────────


@socketio.on('agent:pcb_approve')
def handle_pcb_approve(data):
    ds = _find_design_session(request.sid)
    if ds:
        entry = _find_agent_events(ds, request.sid)
        if entry:
            entry["result"]["approved"] = data.get("approved", False)
            entry["event"].set()


@socketio.on('agent:validation_help_response')
def handle_validation_help(data):
    ds = _find_design_session(request.sid)
    if ds:
        entry = _find_agent_events(ds, request.sid)
        if entry:
            help_event = entry.get("validation_help_event")
            help_result = entry.get("validation_help_result")
            if help_result is not None:
                help_result["action"] = data.get("action", "terminate")
            if help_event is not None:
                help_event.set()


@socketio.on('agent:board_config')
def handle_board_config(data):
    ds = _find_design_session(request.sid)
    if ds:
        entry = _find_agent_events(ds, request.sid)
        if entry:
            board_config_event = entry.get("board_config_event")
            board_config_result = entry.get("board_config_result")
            if board_config_result is not None:
                board_config_result["layer_count"] = data.get("layer_count", 2)
            if board_config_event is not None:
                board_config_event.set()


# ── Agent Generate (legacy direct endpoint) ─────────────────────────────


@socketio.on('agent:generate')
def handle_agent_generate(data):
    prompt = (data or {}).get('prompt', '')
    if not prompt:
        emit('agent:error', {'message': 'No prompt provided.'})
        return

    emit('agent:log', {'message': 'Agent starting...'})
    # No chat_session_id — this is a direct API call without chat context
    socketio.start_background_task(_run_agent, prompt, request.sid, None)


# ── Chat Events ─────────────────────────────────────────────────────────


@socketio.on('chat:message')
def handle_chat_message(data):
    data = data or {}
    session_id = data.get("session_id")
    text = data.get("text", "").strip()

    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    if not text:
        emit('agent:error', {'message': 'No message provided'})
        return

    session = _get_or_create_chat_session(session_id)
    _prune_legacy_mock_history(session)
    session.chat_history.append({"role": "user", "content": text})

    from agent.prompt_router import route_prompt
    try:
        routing = route_prompt(text)
    except Exception as exc:
        print(f"route_prompt failed: {exc}")
        emit('chat:reply', {'text': "I couldn't understand that request. Try rephrasing or type 'help' for guidance."})
        return

    if routing["intent"] == "design_pipeline" and routing["confidence"] >= 0.7:
        socketio.emit('agent:log', {'message': 'Agent starting...'}, room=request.sid)
        # Pass chat session_id so agent stores design under the same key (T1 fix)
        socketio.start_background_task(_run_agent, text, request.sid, session_id)
        return

    if routing["intent"] == "add_component" and routing["confidence"] >= 0.7:
        components = routing.get("extracted_components", [])
        if not components:
            components = [text]
        found_count = 0
        for comp_name in components:
            try:
                proposal = _build_component_proposal_from_query(comp_name)
                if proposal:
                    session.proposals[proposal["id"]] = proposal
                    emit('chat:proposal', proposal)
                    found_count += 1
            except Exception as exc:
                print(f"Component proposal lookup failed for '{comp_name}': {exc}")
        if found_count == 0:
            emit('chat:reply', {'text': f"Could not find a component matching '{text}' in the library. Try running the full design pipeline instead or use a different search term."})
        return

    if routing["intent"] == "modify_design" and routing["confidence"] >= 0.7:
        # Check both chat session and WebSocket session for design state
        ds = session_manager.get_or_create(session_id)
        if not ds.get_design():
            # Also check the WebSocket connection's session (agent stores design there)
            ws_ds = session_manager.get(request.sid)
            if ws_ds and ws_ds.get_design():
                ds = ws_ds
                session_id = request.sid  # Use the WS session for modifications
            else:
                emit('chat:reply', {'text': "No design to modify. Create a design first by describing what you want to build."})
                return
        socketio.emit('agent:log', {'message': f'Modifying design: {text}'}, room=request.sid)
        socketio.start_background_task(
            _run_modify, text, routing, request.sid, session_id
        )
        return

    if routing["intent"] == "component_query" and routing["confidence"] >= 0.7:
        components = routing.get("extracted_components", [])
        query = components[0] if components else text
        results = rag.search(query, k=3)
        if results:
            info_lines = [f"Found components matching '{query}':"]
            for r in results:
                desc = (r.text or "")[:150]
                info_lines.append(f"  - {r.id_str}: {desc}")
            info_lines.append("")
            info_lines.append(f'Tip: type "add {results[0].id_str.split(":")[-1]}" to place one on the board.')
            emit('chat:reply', {'text': "\n".join(info_lines)})
        else:
            emit('chat:reply', {'text': f"No components found matching '{query}'. Try a different search term."})
        return

    if routing["intent"] == "help" and routing["confidence"] >= 0.6:
        emit('chat:reply', {'text': HELP_TEXT})
        return

    emit('chat:reply', {'text': CLARIFICATION_TEXT})


@socketio.on('chat:reject_proposal')
def handle_reject_proposal(data):
    data = data or {}
    session_id = data.get("session_id")
    proposal_id = data.get("proposal_id")
    if session_id in CHAT_SESSIONS:
        session = CHAT_SESSIONS[session_id]
        session.proposals.pop(proposal_id, None)
        session.chat_history.append({
            "role": "system",
            "content": f"The user REJECTED the previous proposal {proposal_id}. The board state has NOT changed."
        })


@socketio.on('chat:resume')
def handle_chat_resume(data):
    session_id = (data or {}).get("session_id")
    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    session = _get_or_create_chat_session(session_id)
    _prune_legacy_mock_history(session)
    emit('chat:state', {
        'history': list(session.chat_history),
        'thought_stream': list(session.thought_stream),
        'proposals': list(session.proposals.values()),
        'board_model': session.board_model,
    })


@socketio.on('chat:commit_proposal')
def handle_commit_proposal(data):
    data = data or {}
    session_id = data.get("session_id")
    proposal_id = data.get("id")
    x = float(data.get("x", 0) or 0)
    y = float(data.get("y", 0) or 0)

    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    session = _get_or_create_chat_session(session_id)
    proposal = session.proposals.pop(proposal_id, None)
    if proposal is None:
        emit('agent:error', {'message': 'Proposal not found or already handled'})
        return

    board_model = session.board_model or _create_empty_board_model()
    board_model.setdefault("components", [])
    board_model.setdefault("traces", [])
    board_model.setdefault("vias", [])
    board_model.setdefault("nets", [])
    board_model.setdefault("outline_segments", [])
    board_model["_render_from_model"] = True

    component = data.get("component") or proposal.get("component", {})
    symbol_id = component.get("symbol_id", "")

    # Determine ref designator prefix based on component type
    name_upper = (component.get("name", "") + " " + symbol_id).upper()
    if any(k in name_upper for k in ["RESISTOR", "R_SMALL", ":R_"]):
        prefix = "R"
    elif any(k in name_upper for k in ["CAPACITOR", "C_SMALL", ":C_"]):
        prefix = "C"
    elif any(k in name_upper for k in ["LED", "DIODE", ":D_"]):
        prefix = "D"
    elif any(k in name_upper for k in ["CONNECTOR", "USB", "JACK"]):
        prefix = "J"
    elif any(k in name_upper for k in ["INDUCTOR", "L_SMALL"]):
        prefix = "L"
    elif any(k in name_upper for k in ["SWITCH", "BUTTON", "TACTILE"]):
        prefix = "SW"
    else:
        prefix = "U"

    # Find next available ref number
    existing_refs = {c.get("ref", "") for c in board_model["components"]}
    ref_num = 1
    while f"{prefix}{ref_num}" in existing_refs:
        ref_num += 1
    ref = f"{prefix}{ref_num}"

    pads = []
    for pin in component.get("pins", []):
        pads.append({
            "num": pin.get("num", pin.get("number", "")),
            "number": pin.get("number", pin.get("num", "")),
            "net": pin.get("targetNet", ""),
            "x": pin.get("x", 0),
            "y": pin.get("y", 0),
            "width": pin.get("width", 0.6),
            "height": pin.get("height", 0.7),
            "shape": pin.get("shape", "rect"),
            "type": pin.get("type", "smd"),
            "rotation": pin.get("rotation", 0),
            "drill": pin.get("drill"),
            "drill_width": pin.get("drill_width"),
            "drill_offset_x": pin.get("drill_offset_x", 0),
            "drill_offset_y": pin.get("drill_offset_y", 0),
            "roundrect_rratio": pin.get("roundrect_rratio"),
            "rect_delta_x": pin.get("rect_delta_x", 0),
            "rect_delta_y": pin.get("rect_delta_y", 0),
            "layers": pin.get("layers", ["F.Cu"]),
        })

    new_comp = {
        "ref": ref,
        "name": component.get("name", "AI Component"),
        "footprint": component.get("footprint", ""),
        "x": x,
        "y": y,
        "rotation": 0,
        "layer": "F.Cu",
        "pads": pads,
        "graphics": component.get("graphics", []),
    }

    board_model["components"].append(new_comp)
    existing_nets = {net.get("name") for net in board_model["nets"] if isinstance(net, dict)}
    for pad in pads:
        net_name = pad.get("net")
        if net_name and net_name not in existing_nets:
            board_model["nets"].append({"name": net_name})
            existing_nets.add(net_name)

    session.board_model = board_model
    session.chat_history.append({
        "role": "assistant",
        "content": f"Placed {ref} at ({x:.2f}, {y:.2f}).",
    })

    with design_lock:
        ds = session_manager.get_or_create(session_id)
        ds.set_design({"board_model": copy.deepcopy(board_model)})
        ds.set_layout({"board_model": copy.deepcopy(board_model)})

    emit('tscircuit:board-model-updated', {'board_model': board_model})
    emit('chat:reply', {'text': f"Placed {ref} ({component.get('name', 'component')}) on the board. You can view it in the current tab or switch views."})
