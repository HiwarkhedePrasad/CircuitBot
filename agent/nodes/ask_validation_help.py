"""Human-in-the-loop node — asks user what to do when validation fails after retries.

Emits agent:validation_help event, then blocks on a threading.Event
until the user responds via the frontend. Returns state updates based on choice.
"""

from agent.utils import _emit, emit_assistant_message, emit_tool_event


def ask_validation_help_node(state, config):
    configurable = config.get("configurable", {})
    emit = configurable.get("emit")
    help_event = configurable.get("validation_help_event")
    help_result = configurable.get("validation_help_result")

    errors = state.get("validation_errors", [])
    warnings = state.get("validation_warnings", [])
    repair_failures = state.get("repair_failures", [])
    comp_count = len(state.get("selected_components", []))

    if emit:
        emit("agent:validation_help", {
            "errors": errors[:5],
            "warnings": warnings[:5],
            "repair_failures": repair_failures[:5],
            "component_count": comp_count,
            "retry_count": state.get("retry_count", 0),
        })

    msg = (
        f"Validation could not auto-fix {len(errors)} issue(s) after multiple retries. "
        "How would you like to proceed?"
    )
    emit_assistant_message(config, msg)
    emit_tool_event(config, "Validation Help", "running", "Awaiting user decision...")

    if help_event is not None:
        help_event.wait(timeout=300)

    action = "terminate"
    if help_result is not None:
        action = help_result.get("action", "terminate")

    if action == "retry":
        emit_tool_event(config, "Validation Help", "completed", "User chose to retry with relaxed search")
        return {
            "retry_count": 1,
            "repair_failures": [],
        }
    elif action == "skip":
        emit_tool_event(config, "Validation Help", "completed", "User chose to skip problematic components")
        comps = state.get("selected_components", [])
        repair_failures = state.get("repair_failures", [])
        comps = [c for c in comps if c.get("id_str", "") not in repair_failures]
        return {
            "selected_components": comps,
            "validation_errors": [],
            "validation_warnings": [],
        }
    elif action == "force":
        emit_tool_event(config, "Validation Help", "completed", "User chose to force continue despite errors")
        return {
            "validation_errors": [],
            "validation_warnings": [],
        }
    else:
        emit_tool_event(config, "Validation Help", "terminated", "User chose to terminate")
        return {
            "error": f"Session terminated by user after validation failures: {'; '.join(errors[:3])}",
        }
