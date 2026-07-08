"""
Backward-compatibility shim — re-exports everything from the refactored
agent/utils.py, agent/nodes/, and agent/builder.py so that existing
imports (e.g. ``from agent.graph import agent_graph``) continue to work.
"""
from agent.builder import agent_graph, build_graph
from agent.nodes import (
    analyze_node, research_node, select_node, validate_node,
    dispatch_node, netlist_node,
    placement_node, routing_node, pcb_layout_node,
    schematic_audit_node, ask_pcb_approval_node,
    ask_validation_help_node,
)
from agent.utils import (
    MAX_LLM_RETRIES, MAX_VALIDATION_RETRIES, MAX_BATCH_PINS,
    GND_NET_NAMES, POWER_NET_NAMES, POWER_ETYPES,
    AgentLLMError,
    _emit, _clean_json, _call_llm, _call_llm_with_tools,
    _check_stage_contract, _stage_result,
    _is_gnd_net, _is_power_net,
    _extract_part_numbers, _is_passive, _ref_prefix_for,
    _canonical_signal_name, _resolve_hallucinated_pin,
    _merge_net, _make_signal_batches,
    _generate_nets_fallback,
    _parse_sexpr_to_ops, _extract_pins_from_ops, _get_attr,
    _route_after_validate, _route_after_validation_help, _route_after_pcb_approval, _sanitize_data,
    PIN_ALIASES, COMPLEMENTARY_PAIRS,
)

__all__ = [
    "agent_graph", "build_graph",
    "analyze_node", "research_node", "select_node", "validate_node",
    "dispatch_node", "netlist_node",
    "placement_node", "routing_node", "pcb_layout_node",
    "ask_validation_help_node",
    "MAX_LLM_RETRIES", "MAX_VALIDATION_RETRIES", "MAX_BATCH_PINS",
    "GND_NET_NAMES", "POWER_NET_NAMES", "POWER_ETYPES",
    "AgentLLMError",
    "_emit", "_clean_json", "_call_llm", "_call_llm_with_tools",
    "_check_stage_contract", "_stage_result",
    "_is_gnd_net", "_is_power_net",
    "_extract_part_numbers", "_is_passive", "_ref_prefix_for",
    "_canonical_signal_name", "_resolve_hallucinated_pin",
    "_merge_net", "_make_signal_batches",
    "_generate_nets_fallback",
    "_parse_sexpr_to_ops", "_extract_pins_from_ops", "_get_attr",
    "_route_after_validate", "_route_after_validation_help", "_route_after_pcb_approval", "_sanitize_data",
    "PIN_ALIASES", "COMPLEMENTARY_PAIRS",
]
