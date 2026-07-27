import os

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import (
    analyze_node, research_node, deepresearch_node, select_node,
    symbol_compatibility_node, validate_node,
    dispatch_node, netlist_node,
    placement_node, routing_node, pcb_layout_node,
    schematic_audit_node, ask_pcb_approval_node,
    schematic_repair_node,
    symbol_validate_node,
    structural_net_validate_node, structural_net_repair_node,
    power_net_repair_node,
    connectivity_validate_node, connectivity_repair_node,
    validate_repair_node,
    ask_validation_help_node,
    ask_board_config_node,
    # New pipeline nodes
    architecture_planner_node,
    capability_resolver_node,
    dependency_expander_node,
    deduplicator_node,
    constraint_checker_node,
    repair_node as new_repair_node,
    freeze_component_list_node,
    pin_marker_node,
)
from agent.nodes.clarify import clarify_node
from agent.nodes.modify import classify_modification_node, apply_modification_node
from agent.nodes.design_review import design_review_node
from agent.nodes.llm_judge import llm_judge_node
from agent.nodes.datasheet_search import datasheet_search_node
from agent.nodes.connection_search import connection_search_node
from agent.pipeline_tracker import add_tracked_node
from agent.utils import _route_after_validate, _route_after_validation_help, _route_after_pcb_approval, _route_after_erc

MAX_ERC_RETRIES = 3

# Feature flag: set to True to use the new pipeline
USE_NEW_PIPELINE = os.getenv("USE_NEW_PIPELINE", "true").lower() == "true"


def error_end_node(state, config):
    msg = state.get("error")
    if not msg:
        failures = state.get("fatal_errors") or state.get("repairable_errors") or []
        if failures:
            details = []
            for failure in failures[:3]:
                if isinstance(failure, dict):
                    details.append(failure.get("message") or failure.get("code", "constraint failure"))
                else:
                    details.append(str(failure))
            msg = "Constraint gate failed: " + "; ".join(details)
        else:
            msg = "Pipeline stopped without a reported failure reason"
    _emit_fn = (config or {}).get("configurable", {}).get("emit")
    if _emit_fn:
        _emit_fn("agent:error", {"message": msg})
    return {"error": msg}


def _always_proceed(state):
    return "next"


def _route_after_constraint_check(state, config=None) -> str:
    """Route after constraint_checker: fatal → halt, repairable → repair, else → dispatch.
    """
    fatal = state.get("fatal_errors", [])
    repairable = state.get("repairable_errors", [])
    passes_used = state.get("repair_passes_used", 0)

    if fatal:
        return "error_end"
    if repairable and passes_used < 2:
        return "repair"
    if repairable and passes_used >= 2:
        return "error_end"
    return "dispatch"


def _route_after_repair(state, config=None) -> str:
    """Route after repair: back to the node that triggered repair."""
    source = state.get("repair_source", "constraint_checker")
    return source if source in ("constraint_checker", "post_validate") else "constraint_checker"


def _route_after_dispatch(state, config=None) -> str:
    """Do not synthesize a schematic from a substituted or missing symbol."""
    return "error_end" if state.get("error") else "pin_marker"


def _route_after_pcb_layout(state, config=None) -> str:
    """Do not run advisory review after PCB construction has already failed."""
    return "error_end" if state.get("error") else "design_review"


# ── Legacy Pipeline ──────────────────────────────────────────────────────────

def build_legacy_graph() -> StateGraph:
    """Build the legacy pipeline graph (pre-refactor)."""
    builder = StateGraph(AgentState)

    add_tracked_node(builder, "clarify", clarify_node)
    add_tracked_node(builder, "analyze", analyze_node)
    add_tracked_node(builder, "deepresearch", deepresearch_node)
    add_tracked_node(builder, "research", research_node)
    add_tracked_node(builder, "select", select_node)
    add_tracked_node(builder, "symbol_compatibility", symbol_compatibility_node)
    add_tracked_node(builder, "validate", validate_node)
    add_tracked_node(builder, "dispatch", dispatch_node)
    add_tracked_node(builder, "symbol_validate", symbol_validate_node)
    add_tracked_node(builder, "netlist", netlist_node)
    add_tracked_node(builder, "power_net_repair", power_net_repair_node)
    add_tracked_node(builder, "structural_net_validate", structural_net_validate_node)
    add_tracked_node(builder, "structural_net_repair", structural_net_repair_node)
    add_tracked_node(builder, "placement", placement_node)
    add_tracked_node(builder, "routing", routing_node)
    add_tracked_node(builder, "connectivity_validate", connectivity_validate_node)
    add_tracked_node(builder, "connectivity_repair", connectivity_repair_node)
    add_tracked_node(builder, "schematic_audit", schematic_audit_node)
    add_tracked_node(builder, "schematic_repair", schematic_repair_node)
    add_tracked_node(builder, "ask_pcb_approval", ask_pcb_approval_node)
    add_tracked_node(builder, "ask_board_config", ask_board_config_node)
    add_tracked_node(builder, "pcb_layout", pcb_layout_node)
    add_tracked_node(builder, "design_review", design_review_node)
    add_tracked_node(builder, "llm_judge", llm_judge_node)
    add_tracked_node(builder, "datasheet_search", datasheet_search_node)
    add_tracked_node(builder, "connection_search", connection_search_node)
    add_tracked_node(builder, "validate_repair", validate_repair_node)
    add_tracked_node(builder, "ask_validation_help", ask_validation_help_node)
    builder.add_node("error_end", error_end_node)

    builder.set_entry_point("clarify")
    builder.add_edge("clarify", "analyze")
    builder.add_edge("analyze", "deepresearch")
    builder.add_edge("deepresearch", "research")
    builder.add_edge("research", "select")
    builder.add_edge("select", "datasheet_search")
    builder.add_edge("datasheet_search", "symbol_compatibility")
    builder.add_edge("symbol_compatibility", "validate")
    builder.add_conditional_edges("validate", _route_after_validate, {
        "validate_repair": "validate_repair",
        "ask_validation_help": "ask_validation_help",
        "dispatch": "dispatch",
        "error_end": "error_end",
    })
    builder.add_edge("validate_repair", "validate")
    builder.add_conditional_edges("ask_validation_help", _route_after_validation_help, {
        "validate_repair": "validate_repair",
        "dispatch": "dispatch",
        "error_end": "error_end",
    })
    builder.add_conditional_edges("dispatch", _route_after_dispatch, {
        "pin_marker": "symbol_validate",
        "error_end": "error_end",
    })
    builder.add_edge("symbol_validate", "connection_search")
    builder.add_edge("connection_search", "netlist")
    builder.add_edge("netlist", "power_net_repair")
    builder.add_edge("power_net_repair", "structural_net_validate")
    builder.add_edge("structural_net_validate", "structural_net_repair")
    builder.add_edge("structural_net_repair", "placement")
    builder.add_edge("placement", "routing")
    builder.add_edge("routing", "connectivity_validate")
    builder.add_edge("connectivity_validate", "connectivity_repair")
    builder.add_edge("connectivity_repair", "schematic_audit")
    builder.add_conditional_edges("schematic_audit", _route_after_erc, {
        "schematic_repair": "schematic_repair",
        "ask_pcb_approval": "ask_pcb_approval",
        "error_end": "error_end",
    })
    builder.add_edge("schematic_repair", "routing")
    builder.add_conditional_edges("ask_pcb_approval", _route_after_pcb_approval, {
        "pcb_layout": "ask_board_config",
        "end": END,
    })
    builder.add_edge("ask_board_config", "pcb_layout")
    builder.add_conditional_edges("pcb_layout", _route_after_pcb_layout, {
        "design_review": "design_review",
        "error_end": "error_end",
    })
    builder.add_edge("design_review", "llm_judge")
    builder.add_edge("llm_judge", END)
    builder.add_edge("error_end", END)

    return builder.compile()


# ── New Pipeline ─────────────────────────────────────────────────────────────

def build_new_graph() -> StateGraph:
    """Build the refactored pipeline graph.

    Flow:
    clarify → analyze → research → select
    → architecture_planner → capability_resolver
    → dependency_expander → deduplicator
    → constraint_checker → validate (LLM) → repair (loop, max 2)
    → freeze_components → datasheet_search → symbol_compatibility
    → dispatch → symbol_validate → connection_search
    → netlist → power_net_repair → structural_net_validate
    → structural_net_repair → placement → routing
    → connectivity_validate → connectivity_repair → schematic_audit
    → schematic_repair → ask_pcb_approval → pcb_layout → design_review
    """
    builder = StateGraph(AgentState)

    # ── Stage 1-2: Requirement parsing & architecture ──
    add_tracked_node(builder, "clarify", clarify_node)
    add_tracked_node(builder, "analyze", analyze_node)
    add_tracked_node(builder, "deepresearch", deepresearch_node)
    add_tracked_node(builder, "research", research_node)
    add_tracked_node(builder, "architecture_planner", architecture_planner_node)
    add_tracked_node(builder, "capability_resolver", capability_resolver_node)

    # ── Stage 3-5: Component selection & expansion ──
    add_tracked_node(builder, "select", select_node)
    add_tracked_node(builder, "dependency_expander", dependency_expander_node)
    add_tracked_node(builder, "deduplicator", deduplicator_node)

    # ── Stage 6-9: Validation loop (constraint check → repair → LLM validate → re-check) ──
    add_tracked_node(builder, "constraint_checker", constraint_checker_node)
    add_tracked_node(builder, "repair", new_repair_node)
    add_tracked_node(builder, "validate", validate_node)  # LLM validation (existing)

    # ── Stage 9: Freeze component list ──
    add_tracked_node(builder, "freeze_components", freeze_component_list_node)
    add_tracked_node(builder, "post_validate_constraint_checker", constraint_checker_node)

    # ── Stage 10-14: Existing downstream pipeline ──
    add_tracked_node(builder, "datasheet_search", datasheet_search_node)
    add_tracked_node(builder, "symbol_compatibility", symbol_compatibility_node)
    add_tracked_node(builder, "dispatch", dispatch_node)
    add_tracked_node(builder, "pin_marker", pin_marker_node)
    add_tracked_node(builder, "symbol_validate", symbol_validate_node)
    add_tracked_node(builder, "connection_search", connection_search_node)
    add_tracked_node(builder, "netlist", netlist_node)
    add_tracked_node(builder, "power_net_repair", power_net_repair_node)
    add_tracked_node(builder, "structural_net_validate", structural_net_validate_node)
    add_tracked_node(builder, "structural_net_repair", structural_net_repair_node)
    add_tracked_node(builder, "placement", placement_node)
    add_tracked_node(builder, "routing", routing_node)
    add_tracked_node(builder, "connectivity_validate", connectivity_validate_node)
    add_tracked_node(builder, "connectivity_repair", connectivity_repair_node)
    add_tracked_node(builder, "schematic_audit", schematic_audit_node)
    add_tracked_node(builder, "schematic_repair", schematic_repair_node)
    add_tracked_node(builder, "ask_pcb_approval", ask_pcb_approval_node)
    add_tracked_node(builder, "ask_board_config", ask_board_config_node)
    add_tracked_node(builder, "pcb_layout", pcb_layout_node)
    add_tracked_node(builder, "design_review", design_review_node)
    add_tracked_node(builder, "llm_judge", llm_judge_node)
    add_tracked_node(builder, "validate_repair", validate_repair_node)
    add_tracked_node(builder, "ask_validation_help", ask_validation_help_node)
    builder.add_node("error_end", error_end_node)

    # ── Edges ──

    # Stage 1-2: Requirement parsing
    builder.set_entry_point("clarify")
    builder.add_edge("clarify", "analyze")
    builder.add_edge("analyze", "deepresearch")
    builder.add_edge("deepresearch", "research")
    builder.add_edge("research", "architecture_planner")
    builder.add_edge("architecture_planner", "capability_resolver")

    # Stage 3-5: Component selection & expansion
    builder.add_edge("capability_resolver", "select")
    builder.add_edge("select", "dependency_expander")
    builder.add_edge("dependency_expander", "deduplicator")

    # Stage 6-8: Pre-validation loop (constraint check → repair)
    builder.add_edge("deduplicator", "constraint_checker")
    builder.add_conditional_edges("constraint_checker", _route_after_constraint_check, {
        "repair": "repair",
        "dispatch": "validate",
        "error_end": "error_end",
    })
    builder.add_conditional_edges("repair", _route_after_repair, {
        "constraint_checker": "constraint_checker",
        "post_validate": "post_validate_constraint_checker",
    })

    # LLM validation (existing) → post-validate constraint check
    builder.add_conditional_edges("validate", _route_after_validate, {
        "validate_repair": "validate_repair",
        "ask_validation_help": "ask_validation_help",
        "dispatch": "post_validate_constraint_checker",
        "error_end": "error_end",
    })
    builder.add_edge("validate_repair", "validate")

    # Post-validate constraint check → repair loop (validates LLM additions)
    builder.add_conditional_edges("post_validate_constraint_checker", _route_after_constraint_check, {
        "repair": "repair",
        "dispatch": "freeze_components",
        "error_end": "error_end",
    })

    # Stage 9: Freeze
    builder.add_conditional_edges("freeze_components", _freeze_route, {
        "dispatch": "datasheet_search",
        "error_end": "error_end",
    })

    # Stage 10-14: Existing downstream
    builder.add_edge("datasheet_search", "symbol_compatibility")
    builder.add_edge("symbol_compatibility", "dispatch")
    builder.add_conditional_edges("dispatch", _route_after_dispatch, {
        "pin_marker": "pin_marker",
        "error_end": "error_end",
    })
    builder.add_edge("pin_marker", "symbol_validate")
    builder.add_edge("symbol_validate", "connection_search")
    builder.add_edge("connection_search", "netlist")
    builder.add_edge("netlist", "power_net_repair")
    builder.add_edge("power_net_repair", "structural_net_validate")
    builder.add_edge("structural_net_validate", "structural_net_repair")
    builder.add_edge("structural_net_repair", "placement")
    builder.add_edge("placement", "routing")
    builder.add_edge("routing", "connectivity_validate")
    builder.add_edge("connectivity_validate", "connectivity_repair")
    builder.add_edge("connectivity_repair", "schematic_audit")
    builder.add_conditional_edges("schematic_audit", _route_after_erc, {
        "schematic_repair": "schematic_repair",
        "ask_pcb_approval": "ask_pcb_approval",
        "error_end": "error_end",
    })
    builder.add_edge("schematic_repair", "routing")
    builder.add_conditional_edges("ask_pcb_approval", _route_after_pcb_approval, {
        "pcb_layout": "ask_board_config",
        "end": END,
    })
    builder.add_edge("ask_board_config", "pcb_layout")
    builder.add_conditional_edges("pcb_layout", _route_after_pcb_layout, {
        "design_review": "design_review",
        "error_end": "error_end",
    })
    builder.add_edge("design_review", "llm_judge")
    builder.add_edge("llm_judge", END)
    builder.add_edge("error_end", END)

    # Validation help routing
    builder.add_conditional_edges("ask_validation_help", _route_after_validation_help, {
        "validate_repair": "post_validate_constraint_checker",
        "dispatch": "freeze_components",
        "error_end": "error_end",
    })

    return builder.compile()


def _freeze_route(state, config=None) -> str:
    """Route after freeze: success → continue pipeline, error → halt."""
    if state.get("error"):
        return "error_end"
    return "dispatch"


# ── Build the active graph ───────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build the active pipeline graph based on feature flag."""
    if USE_NEW_PIPELINE:
        return build_new_graph()
    return build_legacy_graph()


agent_graph = build_graph()


# ── Modify Graph (separate lightweight graph for design modifications) ──────

def build_modify_graph() -> StateGraph:
    """Build a lightweight graph for design modifications."""
    builder = StateGraph(AgentState)
    builder.add_node("classify", classify_modification_node)
    builder.add_node("apply", apply_modification_node)
    builder.set_entry_point("classify")
    builder.add_edge("classify", "apply")
    builder.add_edge("apply", END)
    return builder.compile()


modify_graph = build_modify_graph()
