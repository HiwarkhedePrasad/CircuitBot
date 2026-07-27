"""Human-in-the-loop board configuration node — asks user for PCB layer count.

Emits agent:board_config event with layer options, then blocks on a threading.Event
until the user responds via the frontend. Returns layer_count.
"""

from agent.pipeline_tracker import update_pipeline_stage
from agent.utils import _emit, emit_assistant_message, emit_tool_event

DEFAULT_LAYER_COUNT = 2


def ask_board_config_node(state, config):
    configurable = config.get("configurable", {})
    emit = configurable.get("emit")
    board_config_event = configurable.get("board_config_event")
    board_config_result = configurable.get("board_config_result")

    if emit:
        emit("agent:board_config", {
            "message": "How many PCB layers do you need?",
            "options": [
                {"layers": 2, "label": "2-Layer", "description": "F.Cu + B.Cu — standard for most designs"},
                {"layers": 4, "label": "4-Layer", "description": "F.Cu + In1.Cu + In2.Cu + B.Cu — for complex routing"},
                {"layers": 6, "label": "6-Layer", "description": "F.Cu + In1-In4 + B.Cu — high-speed / RF designs"},
                {"layers": 8, "label": "8-Layer", "description": "F.Cu + In1-In6 + B.Cu — advanced multilayer"},
            ],
        })

    emit_assistant_message(config, "How many PCB layers do you need? (2, 4, 6, or 8)")
    emit_tool_event(config, "Board Config", "running", "Awaiting layer count selection...")

    if board_config_event is not None:
        update_pipeline_stage(config, "waiting", "Awaiting board layer selection")
        board_config_event.wait(timeout=300)
        update_pipeline_stage(config, "running", "Applying board configuration")

    layer_count = DEFAULT_LAYER_COUNT
    if board_config_result is not None:
        selected = board_config_result.get("layer_count", DEFAULT_LAYER_COUNT)
        if selected in (2, 4, 6, 8):
            layer_count = selected

    emit_tool_event(config, "Board Config", "completed",
                    f"{layer_count}-layer board selected")

    return {"layer_count": layer_count}
