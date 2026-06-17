from langgraph.graph import StateGraph

from agent.state import AgentState
from agent.nodes import (
    analyze_node, research_node, select_node, validate_node,
    dispatch_node, netlist_node, layout_route_node,
)
from agent.utils import _route_after_validate


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("analyze", analyze_node)
    builder.add_node("research", research_node)
    builder.add_node("select", select_node)
    builder.add_node("validate", validate_node)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("netlist", netlist_node)
    builder.add_node("layout_route", layout_route_node)

    builder.set_entry_point("analyze")
    builder.add_edge("analyze", "research")
    builder.add_edge("research", "select")
    builder.add_edge("select", "validate")
    builder.add_conditional_edges("validate", _route_after_validate, {
        "select": "select",
        "dispatch": "dispatch",
    })
    builder.add_edge("dispatch", "netlist")
    builder.add_edge("netlist", "layout_route")

    return builder.compile()


agent_graph = build_graph()
