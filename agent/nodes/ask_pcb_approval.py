"""Human-in-the-loop approval node — asks user whether to proceed to PCB layout.

Emits agent:pcb_approval event, then blocks on a threading.Event
until the user responds via the frontend. Returns pcb_approved=True/False.
"""

from agent.utils import _emit, emit_assistant_message, emit_tool_event


def ask_pcb_approval_node(state, config):
    configurable = config.get("configurable", {})
    emit = configurable.get("emit")
    approval_event = configurable.get("approval_event")
    approval_result = configurable.get("approval_result")

    comp_count = len(state.get("selected_components", []))
    wire_count = len(state.get("wire_paths", []))

    if emit:
        emit("agent:pcb_approval", {
            "message": "Schematic is complete. Proceed to PCB layout?",
            "component_count": comp_count,
            "wire_count": wire_count,
        })

    emit_assistant_message(config, "Schematic complete. Waiting for your decision on PCB layout...")
    emit_tool_event(config, "PCB Approval", "running", "Awaiting user decision...")

    if approval_event is not None:
        approval_event.wait(timeout=300)

    approved = False
    if approval_result is not None:
        approved = approval_result.get("approved", False)

    status = "approved" if approved else "skipped"
    emit_tool_event(config, "PCB Approval", "completed" if approved else "skipped",
                    f"PCB layout {status} by user")

    return {"pcb_approved": approved}
