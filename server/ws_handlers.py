import json
import uuid

from flask import request
from flask_socketio import emit

from server.state import app, socketio, rag, design_lock, LAST_DESIGN, _WIREBENDER_LAYOUT, _agent_events
from server.chat import (
    CHAT_SESSIONS, _get_or_create_chat_session, _create_empty_board_model,
    _prune_legacy_mock_history, _build_component_proposal_from_query,
)
from server.agent_runner import _run_agent

HELP_TEXT = """I'm CircuitBot, an AI PCB design assistant. Here's what I can do:

**Add a component** — Say "add a 10k resistor", "I need an ATMega328P", or "place an LED"
**Design a circuit** — Say "design a fan controller PCB", "create a power supply", or "build a sensor board"
**Find a component** — Say "find me a temperature sensor", "search for a 5V regulator"
**Get help** — Say "help" or "what can you do"

I can turn your natural language descriptions into KiCad schematics and PCB layouts."""

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
    entry = _agent_events.pop(request.sid, None)
    if entry:
        entry["result"]["approved"] = False
        entry["event"].set()


# ── Agent Approval Events ───────────────────────────────────────────────


@socketio.on('agent:pcb_approve')
def handle_pcb_approve(data):
    entry = _agent_events.pop(request.sid, None)
    if entry:
        entry["result"]["approved"] = data.get("approved", False)
        entry["event"].set()


@socketio.on('agent:validation_help_response')
def handle_validation_help(data):
    entry = _agent_events.get(request.sid)
    if entry:
        help_event = entry.get("validation_help_event")
        help_result = entry.get("validation_help_result")
        if help_result is not None:
            help_result["action"] = data.get("action", "terminate")
        if help_event is not None:
            help_event.set()


# ── Agent Generate ──────────────────────────────────────────────────────


@socketio.on('agent:generate')
def handle_agent_generate(data):
    prompt = (data or {}).get('prompt', '')
    if not prompt:
        emit('agent:error', {'message': 'No prompt provided.'})
        return

    emit('agent:log', {'message': 'Agent starting...'})
    socketio.start_background_task(_run_agent, prompt, request.sid)


# ── Chat Events ─────────────────────────────────────────────────────────


@socketio.on('chat:message')
def handle_chat_message(data):
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
        socketio.start_background_task(_run_agent, text, request.sid)
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
        'proposals': list(session.proposals.values()),
        'board_model': session.board_model,
    })


@socketio.on('chat:commit_proposal')
def handle_commit_proposal(data):
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

    comp_count = len([c for c in board_model["components"] if c.get("ref", "").startswith("R")])
    ref = f"R{comp_count + 1}"

    component = proposal.get("component", {})
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
        LAST_DESIGN["board_model"] = json.loads(json.dumps(board_model))
        _WIREBENDER_LAYOUT["board_model"] = json.loads(json.dumps(board_model))

    emit('tscircuit:board-model-updated', {'board_model': board_model})
    emit('chat:reply', {'text': f"Successfully placed {ref} at ({x:.2f}, {y:.2f})."})
