import os
import threading
import traceback

from server.state import socketio, design_lock, session_manager, _sid_to_chat, _sid_to_chat_lock
from server.chat import _get_or_create_chat_session


def _run_agent(prompt: str, sid: str, chat_session_id: str | None = None):
    """Background task that runs the LangGraph agent and pushes WS events."""
    # Store the sid→chat_session_id mapping for disconnect cleanup
    if chat_session_id:
        with _sid_to_chat_lock:
            _sid_to_chat[sid] = chat_session_id

    # Use chat_session_id as primary key if available, fall back to sid
    primary_key = chat_session_id or sid
    ds = session_manager.get_or_create(primary_key)

    # Register event slot IMMEDIATELY under design_lock to prevent
    # race condition: disconnect handler must see this before any yield point
    approval_event = threading.Event()
    approval_result = {"approved": False}
    validation_help_event = threading.Event()
    validation_help_result = {"action": "terminate"}
    board_config_event = threading.Event()
    board_config_result = {"layer_count": 2}
    events_entry = {
        "event": approval_event,
        "result": approval_result,
        "validation_help_event": validation_help_event,
        "validation_help_result": validation_help_result,
        "board_config_event": board_config_event,
        "board_config_result": board_config_result,
    }
    with design_lock:
        ds.agent_events[primary_key] = events_entry
        # Also store under sid so approval handlers (which use request.sid) can find it
        if chat_session_id and sid != chat_session_id:
            ds.agent_events[sid] = events_entry

    try:
        from agent.graph import agent_graph

        def ws_emit(event, data):
            socketio.emit(event, data, room=sid)
            if event == "agent:thought_stream" and chat_session_id:
                session = _get_or_create_chat_session(chat_session_id)
                session.thought_stream.append(data)
                if len(session.thought_stream) > 2000:
                    session.thought_stream = session.thought_stream[-1500:]
            elif event == "chat:reply" and chat_session_id:
                session = _get_or_create_chat_session(chat_session_id)
                session.chat_history.append({"role": "assistant", "content": data.get("text", "")})

        run_id = os.urandom(3).hex()
        config = {
            "configurable": {
                "emit": ws_emit,
                "run_id": run_id,
                "approval_event": approval_event,
                "approval_result": approval_result,
                "validation_help_event": validation_help_event,
                "validation_help_result": validation_help_result,
                "board_config_event": board_config_event,
                "board_config_result": board_config_result,
            }
        }
        socketio.emit("agent:log", {"message": f"Run {run_id} started"}, room=sid)
        result = agent_graph.invoke({"prompt": prompt}, config)

        wb = ds.get_layout()
        board_model = result.get('board_model', None) or result.get('_board_model', None) or wb.get('board_model', None)
        design_data = {
            'selected_components': result.get('selected_components', []),
            'component_ops': result.get('component_ops', {}),
            'component_placements': wb.get('component_placements') or result.get('component_placements', []),
            'wire_paths': wb.get('wire_paths') or result.get('wire_paths', []),
            'power_labels': wb.get('power_labels') or result.get('power_labels', []),
            'pin_matrix': result.get('pin_matrix', {}),
            'netlist': result.get('netlist', []),
            'nets': result.get('nets', []),
            'power_pins': result.get('power_pins', []),
            'board_model': board_model,
        }
        with design_lock:
            ds.set_design(design_data)

        # ── Persisted event: signals that export routes will succeed ──
        comp_count = len(design_data.get('selected_components', []))
        socketio.emit('agent:persisted', {
            'component_count': comp_count,
            'has_board_model': board_model is not None,
        }, room=sid)
        socketio.emit('agent:done', {
            'message': (
                f"Design complete: {comp_count} components. "
                f"Export ready — use the Export buttons above."
            ),
        }, room=sid)

        # Emit design review suggestions (after persistence)
        review_suggestions = result.get("review_suggestions", [])
        if review_suggestions:
            for suggestion in review_suggestions:
                socketio.emit('agent:review_suggestion', suggestion, room=sid)
            socketio.emit('agent:review_complete', {
                'count': len(review_suggestions)
            }, room=sid)

        # Also emit pcb_ready here (in case pcb_layout was skipped or graph returned early)
        if board_model and not result.get('_pcb_ready_emitted'):
            socketio.emit('agent:pcb_ready', {'board_model': board_model}, room=sid)
    except Exception as e:
        socketio.emit('agent:error', {'message': str(e)}, room=sid)
        print(f"Agent error: {e}")
        traceback.print_exc()
    finally:
        with design_lock:
            ds.agent_events.pop(primary_key, None)
            if chat_session_id and sid != chat_session_id:
                ds.agent_events.pop(sid, None)
        # Clean up sid→chat mapping
        if chat_session_id:
            with _sid_to_chat_lock:
                _sid_to_chat.pop(sid, None)
