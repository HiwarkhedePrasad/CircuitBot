import os
import threading
import traceback

from server.state import socketio, design_lock, LAST_DESIGN, _WIREBENDER_LAYOUT, _agent_events


def _run_agent(prompt: str, sid: str):
    """Background task that runs the LangGraph agent and pushes WS events."""
    approval_event = threading.Event()
    approval_result = {"approved": False}
    validation_help_event = threading.Event()
    validation_help_result = {"action": "terminate"}
    board_config_event = threading.Event()
    board_config_result = {"layer_count": 2}
    _agent_events[sid] = {
        "event": approval_event,
        "result": approval_result,
        "validation_help_event": validation_help_event,
        "validation_help_result": validation_help_result,
        "board_config_event": board_config_event,
        "board_config_result": board_config_result,
    }

    try:
        from agent.graph import agent_graph

        def ws_emit(event, data):
            socketio.emit(event, data, room=sid)

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

        board_model = result.get('board_model', None) or result.get('_board_model', None)
        with design_lock:
            wb = _WIREBENDER_LAYOUT
            LAST_DESIGN.clear()
            LAST_DESIGN.update({
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
            })

        if board_model:
            socketio.emit('agent:pcb_ready', {'board_model': board_model}, room=sid)
    except Exception as e:
        socketio.emit('agent:error', {'message': str(e)}, room=sid)
        print(f"Agent error: {e}")
        traceback.print_exc()
    finally:
        _agent_events.pop(sid, None)
