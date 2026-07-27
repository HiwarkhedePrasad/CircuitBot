"""Deduplicator — removes duplicate components immediately after selection.

Runs right after dependency expansion, before validation. This prevents
duplicate support parts from accumulating through the pipeline.
"""

from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _is_passive,
)
from uuid import uuid4

# MCU library prefixes — import from centralized registry
from agent.library_registry import MCU_LIBRARY_PREFIXES


def _is_mcu_component(c: dict) -> bool:
    """Check if a component is an MCU."""
    id_str = c.get("id_str", "")
    return any(id_str.startswith(prefix) for prefix in MCU_LIBRARY_PREFIXES)


def deduplicator_node(state, config):
    """Remove duplicate components from the selected list."""
    dedup_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Deduplicating components..."})
    emit_tool_event(config, "Deduplicator", "running", "Removing duplicates...")
    
    contract = _check_stage_contract("deduplicator", state, ["selected_components"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "deduplicator", {})
    
    comps = list(state.get("selected_components", []))
    original_count = len(comps)

    # 0. MCU dedup: when architecture is locked, keep only one MCU
    #    (the one matching primary_mcu or the first one found)
    primary_mcu = state.get("primary_mcu", "")
    architecture_frozen = state.get("architecture_frozen", False)
    if architecture_frozen and primary_mcu:
        from agent.knowledge.dependency_graph import get_mcu_family
        mcus = [c for c in comps if _is_mcu_component(c) and not c.get("builtin")]
        if len(mcus) > 1:
            # Keep the MCU that matches primary_mcu, or the first one
            best_mcu = None
            for m in mcus:
                family = get_mcu_family(m.get("id_str", ""))
                if family == primary_mcu:
                    best_mcu = m
                    break
            if best_mcu is None:
                best_mcu = mcus[0]
            # Remove all other MCUs
            removed_mcus = [m for m in mcus if m is not best_mcu]
            comps = [c for c in comps if c not in removed_mcus]
            for m in removed_mcus:
                _emit(config, "agent:log", {
                    "message": f"  MCU dedup: removed {m.get('ref_des', '?')} ({m.get('id_str', '?')}) — "
                               f"keeping {best_mcu.get('ref_des', '?')} ({best_mcu.get('id_str', '?')})"
                })

    # 1. Dedup by id_str (skip passives — multiple caps/resistors are normal)
    seen_ids: dict[str, str] = {}  # id_str -> ref_des
    deduped: list[dict] = []
    for c in comps:
        id_str = c.get("id_str", "")
        ref_des = c.get("ref_des", "")
        
        # Always keep builtin components
        if c.get("builtin"):
            deduped.append(c)
            continue
        
        # Always keep passives, switches, connectors, and components with unique descriptions
        cat = c.get("category", "")
        justification = c.get("justification", "")
        func_id = c.get("functional_id", "")
        if (
            _is_passive(id_str, cat)
            or cat in ("Switch", "Connector", "Device", "Power_Protection")
            or func_id
            or justification.startswith("Deterministically synthesized")
            or justification.startswith("Supporting part")
            or c.get("description")
        ):
            deduped.append(c)
            continue
        
        # Dedup non-passive by id_str
        if id_str in seen_ids:
            _emit(config, "agent:log", {
                "message": f"  Dedup: removed {ref_des} ({id_str}) — duplicate of {seen_ids[id_str]}"
            })
            continue
        
        seen_ids[id_str] = ref_des
        deduped.append(c)

    # 1b. Family-level dedup: same base part number (e.g., TMP117xxDRV vs TMP117xxYBG)
    seen_families: dict[str, str] = {}  # family -> ref_des of first seen
    family_deduped: list[dict] = []
    for c in deduped:
        if c.get("builtin"):
            family_deduped.append(c)
            continue
        cat = c.get("category", "")
        justification = c.get("justification", "")
        func_id = c.get("functional_id", "")
        if (
            _is_passive(c.get("id_str", ""), cat)
            or cat in ("Switch", "Connector", "Device", "Power_Protection")
            or func_id
            or justification.startswith("Deterministically synthesized")
            or justification.startswith("Supporting part")
            or c.get("description")
        ):
            family_deduped.append(c)
            continue
        # Extract family: library:PARTNAME → PARTNAME base (strip variant suffixes)
        id_str = c.get("id_str", "")
        _, _, part = id_str.partition(":")
        # Take the part name up to the first dash or underscore variant marker
        # e.g., "TMP117xxDRV" → "TMP117", "CP2102N-Axx-xQFN20" → "CP2102N"
        import re as _re
        base = _re.split(r'[-_]', part)[0] if part else id_str
        # Normalize: strip common suffixes like "xx", "N", variant letters
        # Use multiple passes to strip compound suffixes like "xxDRV" → "TMP117"
        family_key = base
        for _ in range(3):  # max 3 passes to handle compound suffixes
            new_key = _re.sub(r'(xx|XX|drv|DRV|ybg|YBG|n|N)$', '', family_key, flags=_re.IGNORECASE)
            if new_key == family_key:
                break
            family_key = new_key
        if not family_key:
            family_deduped.append(c)
            continue
        if family_key in seen_families:
            _emit(config, "agent:log", {
                "message": f"  Dedup: removed {c.get('ref_des', '?')} ({id_str}) — "
                           f"same family as {seen_families[family_key]} ({family_key})"
            })
            continue
        seen_families[family_key] = f"{c.get('ref_des', '?')} ({id_str})"
        family_deduped.append(c)
    deduped = family_deduped
    
    # 2. Dedup by subsystem (one primary component per subsystem)
    seen_subs: dict[str, str] = {}
    final_deduped: list[dict] = []
    for c in deduped:
        cat = c.get("category", "")
        justification = c.get("justification", "")
        func_id = c.get("functional_id", "")
        if (
            c.get("builtin")
            or _is_passive(c.get("id_str", ""), cat)
            or cat in ("Switch", "Connector", "Device", "Power_Protection")
            or func_id
            or justification.startswith("Auto-added by validator")
            or justification.startswith("Deterministically synthesized")
            or justification.startswith("Supporting part")
            or c.get("description")
        ):
            final_deduped.append(c)
            continue
        
        sub = c.get("subsystem", "")
        if not sub:
            final_deduped.append(c)
            continue
        
        if sub in seen_subs:
            _emit(config, "agent:log", {
                "message": f"  Dedup: removed {c.get('ref_des', '?')} ({c.get('id_str', '?')}) — "
                           f"subsystem '{sub}' already has {seen_subs[sub]}"
            })
            continue
        
        seen_subs[sub] = f"{c.get('ref_des', '?')} ({c.get('id_str', '?')})"
        final_deduped.append(c)
    
    removed_count = original_count - len(final_deduped)
    if removed_count:
        _emit(config, "agent:log", {
            "message": f"  Deduplication: {original_count} → {len(final_deduped)} components "
                       f"({removed_count} removed)"
        })
    else:
        _emit(config, "agent:log", {"message": "  No duplicates found"})
    
    emit_tool_event(config, "Deduplicator", "completed",
                    f"{len(final_deduped)} components ({removed_count} duplicates removed)")
    
    return _stage_result(state, "deduplicator", {
        "selected_components": final_deduped,
    })
