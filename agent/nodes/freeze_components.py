"""Freeze Component List — locks the component list before netlist generation.

After this node runs, no subsequent node may modify the component list.
This prevents the validate→repair loop from corrupting the design
after netlist generation has started.
"""

from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)
from uuid import uuid4
import copy


class FrozenComponentList:
    """Wrapper that raises on any mutation attempt."""
    
    def __init__(self, original: list[dict]):
        self._original = original
    
    def __getitem__(self, key):
        return self._original[key]
    
    def __setitem__(self, key, value):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def __len__(self):
        return len(self._original)
    
    def __iter__(self):
        return iter(self._original)
    
    def __contains__(self, item):
        return item in self._original
    
    def append(self, value):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def remove(self, value):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def extend(self, values):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def pop(self, index=-1):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def insert(self, index, value):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")
    
    def clear(self):
        raise RuntimeError("Component list is frozen — cannot modify after freeze")


def freeze_component_list_node(state, config):
    """Freeze the component list — no further modifications allowed."""
    freeze_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Freezing component list..."})
    emit_assistant_message(config, "Locking component list before netlist generation...")
    emit_tool_event(config, "Freeze Components", "running", "Freezing component list...")
    
    # Check for unresolved errors
    fatal = state.get("fatal_errors", [])
    repairable = state.get("repairable_errors", [])
    if fatal or repairable:
        _emit(config, "agent:log", {
            "message": f"  Cannot freeze: {len(fatal)} fatal + {len(repairable)} repairable errors remain"
        })
        return _stage_result(state, "freeze_components", {
            "error": "Cannot freeze component list with unresolved errors"
        })
    
    comps = state.get("selected_components", [])
    
    # Deep copy for immutable snapshot
    frozen = copy.deepcopy(comps)
    
    # Wrap original with mutation guard
    guarded = FrozenComponentList(comps)
    
    _emit(config, "agent:log", {
        "message": f"  Frozen {len(comps)} components — no further modifications allowed"
    })
    
    emit_tool_event(config, "Freeze Components", "completed",
                    f"{len(comps)} components frozen")
    emit_assistant_message(config, f"Component list frozen: {len(comps)} components locked.")
    
    return _stage_result(state, "freeze_components", {
        "component_list_frozen": True,
        "selected_components": guarded,
    })
