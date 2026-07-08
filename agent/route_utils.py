from agent.llm_utils import MAX_VALIDATION_RETRIES


def _check_stage_contract(stage: str, state, required: list[str]) -> str | None:
    for field in required:
        if state.get(field) is None:
            return f"{stage}: missing required input '{field}'"
    return None


def _stage_result(state, stage: str, outputs: dict) -> dict:
    outputs["_stage"] = stage
    return outputs


def _route_after_validate(state, config=None) -> str:
    errors = state.get("validation_errors", [])
    retry_count = state.get("retry_count", 0)
    if errors and retry_count >= MAX_VALIDATION_RETRIES:
        return "ask_validation_help"
    if state.get("error"):
        return "error_end"
    if errors and retry_count < MAX_VALIDATION_RETRIES:
        return "validate_repair"
    return "dispatch"


def _route_after_validation_help(state, config=None) -> str:
    if state.get("error"):
        return "error_end"
    errors = state.get("validation_errors", [])
    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_VALIDATION_RETRIES and errors:
        return "validate_repair"
    if not errors:
        return "dispatch"
    return "error_end"


def _route_after_pcb_approval(state, config=None) -> str:
    if state.get("pcb_approved", False):
        return "pcb_layout"
    return "end"


def _route_after_erc(state, config=None) -> str:
    erc = state.get("_erc_results", {})
    errors = erc.get("errors", []) if erc else []
    fixable_types = {"pin_not_connected", "unconnected_wire_endpoint",
                     "wire_dangling", "power_pin_not_driven"}
    has_fixable = any(
        e.get("type") in fixable_types for e in errors
    )
    retries = state.get("_erc_retries", 0)
    if has_fixable and retries < 3:
        return "schematic_repair"
    return "ask_pcb_approval"
