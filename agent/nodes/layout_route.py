"""
Backward-compatibility shim — layout_route has been split into
schematic_layout + pcb_layout.  This module re-exports both for
external importers that haven't been updated yet.
"""
from agent.nodes.placement import placement_node
from agent.nodes.routing import routing_node
from agent.nodes.pcb_layout import pcb_layout_node

__all__ = ["placement_node", "routing_node", "pcb_layout_node"]
