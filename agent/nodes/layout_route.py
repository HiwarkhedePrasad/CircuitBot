"""
Backward-compatibility shim — layout_route has been split into
schematic_layout + pcb_layout.  This module re-exports both for
external importers that haven't been updated yet.
"""
from agent.nodes.schematic_layout import schematic_layout_node
from agent.nodes.pcb_layout import pcb_layout_node

__all__ = ["schematic_layout_node", "pcb_layout_node"]
