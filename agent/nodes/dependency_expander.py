"""Dependency Expander — builds ownership graph and injects support parts.

This node runs after component selection. It:
1. Builds an ownership graph (which component provides which capability)
2. Checks if required support components are present
3. Injects missing support components (caps, resistors, etc.)

This replaces the support_parts injection that was in select_node.
"""

from agent.knowledge.dependency_graph import (
    get_mcu_family,
    get_requirements,
    get_owned_capabilities,
)
from agent.support_rules import resolve_fallback_symbol
from agent.tools import search_components
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _ref_prefix_for, _is_passive,
)
from uuid import uuid4


def _check_support_present(component: dict, support_desc: str, comps: list[dict]) -> bool:
    """Check if a required support component is already present.
    
    Uses structured matching: category + value keywords, not loose word overlap.
    """
    desc_lower = support_desc.lower()
    # Extract key identifiers: component type words (capacitor, resistor, etc.)
    # and value words (100nF, 4.7k, etc.)
    support_words = [w for w in desc_lower.split() if len(w) > 3]
    for c in comps:
        c_desc = (c.get("description", "") or "").lower()
        c_id = (c.get("id_str", "") or "").lower()
        c_cat = (c.get("category", "") or "").lower()
        # Require at least 2 keyword matches for a positive identification
        # to avoid false positives from single common words like "pull"
        match_count = sum(1 for kw in support_words
                         if kw in c_desc or kw in c_id or kw in c_cat)
        if match_count >= 2:
            return True
    return False


def _next_ref_des(ref_prefix: str, comps: list[dict]) -> str:
    """Compute the next available ref_des for a given prefix (e.g. 'U' -> 'U5')."""
    existing_nums = set()
    for c in comps:
        r = c.get("ref_des", "")
        pfx = "".join(ch for ch in r if ch.isalpha()) or "U"
        num = "".join(ch for ch in r if ch.isdigit())
        if pfx == ref_prefix and num:
            existing_nums.add(int(num))
    next_num = 1
    while next_num in existing_nums:
        next_num += 1
    return f"{ref_prefix}{next_num}"


def _make_support_comp(
    id_str: str,
    ref: str,
    support_spec: dict,
    for_component: str,
    chosen: dict | None = None,
) -> dict:
    """Build the support component dict from a chosen or fallback component."""
    category = id_str.split(":")[0] if ":" in id_str else "Device"
    description = support_spec.get("description", chosen.get("text", "") if chosen else "")
    return {
        "id_str": id_str,
        "ref_des": ref,
        "category": category,
        "description": description,
        "footprint": (chosen.get("footprint", "") if chosen else ""),
        "pads": (chosen.get("pads", []) if chosen else []),
        "justification": f"Required support for {for_component}: {support_spec.get('description', '')}",
        "datasheet_text": "",
        "for_component": for_component,
    }


def _inject_support_component(
    support_spec: dict,
    comps: list[dict],
    for_component: str,
    config,
) -> dict | None:
    """Search for and create a support component entry."""
    query = support_spec.get("search_query", "")
    preferred = support_spec.get("preferred_id_str", "")
    lib_filter = support_spec.get("library_filter", "")
    
    # If preferred_id_str is set but no library_filter, infer from preferred
    if preferred and not lib_filter:
        inferred_lib = preferred.split(":")[0] if ":" in preferred else ""
        if inferred_lib:
            lib_filter = inferred_lib
    
    try:
        candidates = search_components(query, k=5, library_filter=lib_filter or None)
    except Exception as e:
        _emit(config, "agent:log", {"message": f"  Support search failed for '{query}': {e}"})
        if preferred:
            return _fallback_support_comp(preferred, support_spec, for_component, comps, config)
        return None
    
    # Filter out MCU/module components from support searches
    _SUPPORT_BLOCKED_LIBS = frozenset({"MCU", "RF_Module", "Processor"})
    _SUPPORT_BLOCKED_KEYWORDS = frozenset({
        "WROOM", "DEVKIT", "MINI", "MODULE", "NODEMCU", "WEMOS",
        "BREAKOUT", "BOARD", "ESP32", "STM32", "RP2040", "RP2350",
    })
    filtered_candidates = []
    for c in candidates:
        cid = (c.get("id_str", "") or "").upper()
        lib = cid.split(":")[0] if ":" in cid else ""
        if lib in _SUPPORT_BLOCKED_LIBS:
            continue
        if any(kw in cid for kw in _SUPPORT_BLOCKED_KEYWORDS):
            continue
        filtered_candidates.append(c)

    chosen = None
    if preferred:
        for c in filtered_candidates:
            if c["id_str"] == preferred:
                chosen = c
                break
    
    # If no preferred match, try compatible IDs
    if not chosen and not preferred:
        compatible = support_spec.get("compatible", [])
        for compat_id in compatible:
            for c in filtered_candidates:
                if c["id_str"] == compat_id:
                    chosen = c
                    preferred = compat_id
                    break
            if chosen:
                break
    
    if not chosen and preferred:
        return _fallback_support_comp(preferred, support_spec, for_component, comps, config)
    
    if not chosen and filtered_candidates:
        chosen = filtered_candidates[0]
    
    if not chosen:
        return None
    
    ref_prefix = _ref_prefix_for(chosen["id_str"], chosen["id_str"].split(":")[0])
    ref = _next_ref_des(ref_prefix, comps)
    return _make_support_comp(chosen["id_str"], ref, support_spec, for_component, chosen)


def _fallback_support_comp(
    preferred: str,
    support_spec: dict,
    for_component: str,
    comps: list[dict],
    config,
) -> dict | None:
    """Create a fallback support component when the preferred part isn't in RAG."""
    mapped = resolve_fallback_symbol(preferred)
    final_id = mapped if mapped else preferred
    category = final_id.split(":")[0] if ":" in final_id else "Device"
    ref_prefix = _ref_prefix_for(final_id, category)
    ref = _next_ref_des(ref_prefix, comps)
    _emit(config, "agent:log", {
        "message": f"  Used fallback symbol {final_id} for {support_spec.get('description', preferred)} (not in RAG)"
    })
    return _make_support_comp(final_id, ref, support_spec, for_component)


def dependency_expander_node(state, config):
    """Build ownership graph and inject missing support components."""
    exp_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Expanding dependencies..."})
    emit_assistant_message(config, "Building dependency graph and injecting support components...")
    emit_tool_event(config, "Dependency Expander", "running", "Building dependency graph...")
    
    contract = _check_stage_contract("dependency_expander", state, ["selected_components", "board_type", "primary_mcu"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "dependency_expander", {})
    
    comps = list(state.get("selected_components", []))
    builtin = state.get("_builtin_components", [])
    board_type = state.get("board_type", "bare_ic")
    primary_mcu = state.get("primary_mcu", "")

    # Merge builtin components
    all_comps = builtin + comps

    # Detect the ACTUAL MCU from selected components (not just primary_mcu)
    # The architecture planner may have locked "ESP32-C3" but the selector
    # may have picked "ESP32-WROOM-32U" (a module). We need to match requirements
    # to the actual component, not the abstract MCU family.
    _MODULE_KEYWORDS = frozenset({
        "WROOM", "DEVKIT", "MINI", "MODULE", "NODEMCU", "WEMOS", "BREAKOUT", "BOARD"
    })
    _MCU_KEYWORDS = frozenset({
        "ESP32", "STM32", "RP2040", "RP2350", "ATMEGA", "ATTINY", "SAMD", "NRF52"
    })
    _TIMER_KEYWORDS = frozenset({
        "NE555", "555", "TIMER", "LM555", "TLC555", "ICM7555"
    })

    selected_is_module = False
    actual_mcu_family = None
    actual_ic_family = None
    for c in comps:
        cid = (c.get("id_str", "") or "").upper()
        if any(kw in cid for kw in _MODULE_KEYWORDS):
            selected_is_module = True
        if any(kw in cid for kw in _MCU_KEYWORDS):
            actual_mcu_family = get_mcu_family(c.get("id_str", ""))
        if any(kw in cid for kw in _TIMER_KEYWORDS):
            actual_ic_family = "NE555"

    # 1. Build ownership graph
    ownership: dict[str, list[str]] = {}
    for c in all_comps:
        ownership[c.get("ref_des", "")] = []

    # 2. Get MCU/IC requirements — use actual family if detected
    ic_family = actual_mcu_family or actual_ic_family
    mcu_family = ic_family or get_mcu_family(primary_mcu) or primary_mcu
    requirements = get_requirements(mcu_family, board_type)

    # If selected MCU is a module, only skip capabilities the module truly provides.
    # Modules include: crystal, flash, antenna. They do NOT include:
    # decoupling caps, EN pull-up, boot-strapping resistors, programming header.
    if selected_is_module:
        # Module_overrides are already applied by get_requirements() above.
        # Only add a specific list of truly-builtin skips for modules.
        _MODULE_BUILTIN_SKIPS = frozenset({
            "crystal_40mhz", "crystal_12mhz", "crystal_8mhz", "crystal_16mhz",
            "flash_memory", "flash",
        })
        before = len(requirements)
        requirements = {k: v for k, v in requirements.items()
                        if k not in _MODULE_BUILTIN_SKIPS}
        dropped = before - len(requirements)
        _emit(config, "agent:log", {
            "message": f"  Selected MCU is a module — skipped {dropped} module-builtin requirement(s), "
                       f"keeping {len(requirements)} external requirement(s)"
        })
    
    # Check if USB connector is present — if so, inject USB ESD protection requirement
    has_usb_connector = any("USB" in (c.get("id_str","") + c.get("category","")).upper() for c in all_comps)
    if has_usb_connector and "usb_esd" not in requirements:
        requirements["usb_esd"] = {
            "required": True,
            "compatible": ["Power_Protection:USBLC6-2SC6"],
            "note": "ESD protection diode for USB D+/D- and VBUS lines"
        }

    _emit(config, "agent:log", {
        "message": f"  MCU {mcu_family} ({board_type}): {len(requirements)} requirement(s) to check"
    })
    
    # 3. Check each requirement
    injected = []
    for req_id, req_spec in requirements.items():
        if not req_spec.get("required", True):
            continue
        
        # Check if already satisfied — require 3+ word matches or structured match
        already_present = False
        req_words = req_id.replace("_", " ").split()
        req_value = req_spec.get("value", "")
        for c in all_comps:
            c_desc = (c.get("description", "") or "").lower()
            c_id = (c.get("id_str", "") or "").lower()
            c_cat = (c.get("category", "") or "").lower()
            c_val = (c.get("value", "") or "").lower()
            # Count matching words (skip short words)
            match_count = sum(1 for w in req_words if len(w) > 2
                             and (w in c_desc or w in c_id or w in c_cat))
            # Check value match if requirement has a value
            value_match = req_value and c_val and req_value.lower() in c_val
            # Require 3+ word matches OR 2+ words with value match
            if match_count >= 3 or (match_count >= 2 and value_match):
                already_present = True
                break
        
        if already_present:
            _emit(config, "agent:log", {"message": f"  Requirement '{req_id}' already satisfied"})
            continue
        
        # Try to inject
        compatible = req_spec.get("compatible", [])
        search_query = f"{req_id.replace('_', ' ')} {primary_mcu}"
        
        support_spec = {
            "search_query": search_query,
            "preferred_id_str": compatible[0] if compatible else "",
            "library_filter": "",
            "description": req_spec.get("note", req_id),
        }
        
        new_comp = _inject_support_component(support_spec, all_comps, primary_mcu, config)
        if new_comp:
            injected.append(new_comp)
            all_comps.append(new_comp)
            _emit(config, "agent:log", {
                "message": f"  Injected {new_comp['ref_des']} ({new_comp['id_str']}) for '{req_id}'"
            })
        else:
            _emit(config, "agent:log", {
                "message": f"  Could not find component for '{req_id}' — will be flagged in validation"
            })
    
    # 4. Update ownership graph for injected components
    for c in injected:
        ref = c.get("ref_des", "")
        if ref:
            ownership[ref] = [c.get("for_component", "")]
    
    # 5. Build capability sources map
    cap_sources: dict[str, str] = {}
    for c in all_comps:
        if c.get("builtin"):
            # Extract capability from subsystem field
            sub = c.get("subsystem", "")
            if sub.startswith("builtin_"):
                cap = sub[len("builtin_"):]
                cap_sources[cap] = c.get("ref_des", "")
    
    # Merge injected into original comps list
    final_comps = comps + injected
    
    if injected:
        _emit(config, "agent:log", {
            "message": f"  Dependency expansion: {len(injected)} component(s) injected"
        })
    
    emit_tool_event(config, "Dependency Expander", "completed",
                    f"{len(injected)} support components injected")
    
    return _stage_result(state, "dependency_expander", {
        "selected_components": final_comps,
        "ownership_graph": ownership,
        "capability_sources": cap_sources,
    })
