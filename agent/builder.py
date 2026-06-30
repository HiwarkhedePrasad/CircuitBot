from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import (
    analyze_node, research_node, select_node, validate_node,
    dispatch_node, netlist_node,
    placement_node, routing_node, pcb_layout_node,
    schematic_audit_node, ask_pcb_approval_node,
    schematic_repair_node,
)
from agent.utils import _route_after_validate, _route_after_pcb_approval, _route_after_erc

MAX_ERC_RETRIES = 3


def error_end_node(state, config):
    msg = state.get("error", "Unknown error")
    _emit_fn = config["configurable"].get("emit")
    if _emit_fn:
        _emit_fn("agent:error", {"message": msg})
    return {}


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("analyze", analyze_node)
    builder.add_node("research", research_node)
    builder.add_node("select", select_node)
    builder.add_node("validate", validate_node)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("netlist", netlist_node)
    builder.add_node("placement", placement_node)
    builder.add_node("routing", routing_node)
    builder.add_node("schematic_audit", schematic_audit_node)
    builder.add_node("ask_pcb_approval", ask_pcb_approval_node)
    builder.add_node("pcb_layout", pcb_layout_node)
    builder.add_node("schematic_repair", schematic_repair_node)
    builder.add_node("error_end", error_end_node)

    builder.set_entry_point("analyze")
    builder.add_edge("analyze", "research")
    builder.add_edge("research", "select")
    builder.add_edge("select", "validate")
    builder.add_conditional_edges("validate", _route_after_validate, {
        "select": "select",
        "dispatch": "dispatch",
        "error_end": "error_end",
    })
    builder.add_edge("dispatch", "netlist")
    builder.add_edge("netlist", "placement")
    builder.add_edge("placement", "routing")
    builder.add_edge("routing", "schematic_audit")
    builder.add_conditional_edges("schematic_audit", _route_after_erc, {
        "schematic_repair": "schematic_repair",
        "ask_pcb_approval": "ask_pcb_approval",
    })
    builder.add_edge("schematic_repair", "routing")
    builder.add_conditional_edges("ask_pcb_approval", _route_after_pcb_approval, {
        "pcb_layout": "pcb_layout",
        "end": END,
    })
    builder.add_edge("pcb_layout", END)
    builder.add_edge("error_end", END)

    return builder.compile()


agent_graph = build_graph()
