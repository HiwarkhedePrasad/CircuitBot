import json
import re
import os
import traceback
from langgraph.graph import StateGraph
from agent.state import AgentState
from agent.prompts import (
    ANALYZE_SYSTEM, ANALYZE_USER, SELECT_SYSTEM, SELECT_USER,
    NETLIST_SYSTEM, NETLIST_USER, NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER,
)
from agent.tools import search_components, fetch_sexpr, llm_call
from agent.layout_engine import BackendLayoutEngine


def _emit(config, event, data):
    emit_fn = config["configurable"].get("emit")
    if emit_fn:
        emit_fn(event, data)


def _clean_json(text: str) -> str:
    text = text.strip()
    start = text.find('[')
    if start < 0:
        start = text.find('{')
    if start < 0:
        return ''
    text = text[start:]
    if text.startswith('['):
        end = text.rfind(']')
        if end >= 0:
            text = text[:end+1]
    elif text.startswith('{'):
        end = text.rfind('}')
        if end >= 0:
            text = text[:end+1]
    text = re.sub(r'```json\s*|```\s*', '', text).strip()
    return text


def _call_llm(system: str, user: str) -> str:
    try:
        return llm_call(system, user)
    except Exception as e:
        print(f"LLM call failed: {e}")
        traceback.print_exc()
        return ""


# ── net classification helpers ───────────────────────────────────────────────

GND_NET_NAMES = {"GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EP", "EPAD", "0V"}
POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "V+", "V-", "VSYS", "VOUT", "VEE", "PWR"}


def _is_gnd_net(name: str) -> bool:
    return name.upper().lstrip('+') in GND_NET_NAMES


def _is_power_net(name: str) -> bool:
    n = name.upper().lstrip('+')
    if n in POWER_NET_NAMES:
        return True
    # Voltage-style names: 3V3, 5V, 12V, 1V8, V5, 3.3V, 5.0V, 3_3V ...
    if re.match(r'^\d+V\d*$', n) or re.match(r'^V\d+$', n) or re.match(r'^\d+[._]\d+V$', n):
        return True
    return False


# Part-number-like tokens: 2+ letters followed by a digit, optional suffix
# (matches ESP32-C3, DS18B20, AT89S52, MCP73831; rejects 3V3, 100nF, USB-C)
_PART_TOKEN_RE = re.compile(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\b')
_NON_PART_WORDS = {"USB2", "USB3", "RS232", "RS485", "CAT5", "CAT6", "WIFI6", "IEEE802"}


def _extract_part_numbers(prompt: str) -> list:
    """Deterministically pull explicit part numbers out of the user prompt.

    Safety net for the analyze LLM: even if it genericizes 'ESP32-C3' into
    'Microcontroller', these tokens still get searched verbatim.
    """
    out, seen = [], set()
    for m in _PART_TOKEN_RE.finditer(prompt):
        tok = m.group(0)
        up = tok.upper()
        if len(up) < 5 or up in _NON_PART_WORDS or up in seen:
            continue
        # Reject component values / units (10uF, 100nF, 16MHz, 10kOhm)
        if re.fullmatch(r'[A-Z]{0,2}\d+(V\d*|UF|NF|PF|UH|MH|K|M|MA|A|W|OHM|KOHM|MHZ|KHZ|HZ|BIT|MM)', up):
            continue
        seen.add(up)
        out.append(tok)
    return out


def _is_passive(id_str: str, category: str) -> bool:
    """Passives (R, C, L, crystals, LEDs) may legitimately appear multiple times."""
    cat = (category or '').upper()
    return id_str.startswith('Device:') or cat in ('DEVICE',)


def _ref_prefix_for(id_str: str, category: str) -> str:
    """Determine the correct reference designator prefix for a component.

    Decides from the KiCad library name in id_str (ground truth) first;
    the LLM-supplied category is only a fallback hint. This prevents
    scrambled designators (e.g. an inductor renamed to U#).
    """
    lib = id_str.partition(':')[0].upper()
    name = id_str.partition(':')[2].upper()
    cat = (category or '').upper()
    if id_str.startswith('Device:'):
        if name == 'R' or name.startswith('R_'):
            return 'R'
        if name == 'C' or name.startswith(('C_', 'CP')):
            return 'C'
        if name == 'L' or name.startswith('L_'):
            return 'L'
        if name.startswith(('CRYSTAL', 'RESONATOR')):
            return 'Y'
        if name.startswith(('LED', 'D_')) or name == 'D':
            return 'D'
        if name.startswith('Q_'):
            return 'Q'
        if name.startswith('BATTERY'):
            return 'BT'
        if name.startswith(('FUSE', 'POLYFUSE')):
            return 'F'
    # Library-name checks (ground truth) take precedence over LLM category
    hints = f"{lib} {cat}"
    if 'INDUCTOR' in hints:
        return 'L'
    if 'CONNECTOR' in hints:
        return 'J'
    if 'SWITCH' in hints:
        return 'SW'
    if 'TRANSISTOR' in hints:
        return 'Q'
    if 'DIODE' in hints or 'LED' in hints:
        return 'D'
    if 'CRYSTAL' in hints or 'OSCILLATOR' in hints:
        return 'Y'
    if 'BATTERY' in hints:
        return 'BT'
    if 'RELAY' in hints:
        return 'K'
    return 'U'


def analyze_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
    text = _call_llm(ANALYZE_SYSTEM, ANALYZE_USER.format(prompt=state["prompt"]))
    text = _clean_json(text)
    try:
        analysis = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse analysis JSON: {text[:200]}")
        analysis = []
    if not analysis:
        analysis = [{"subsystem": state["prompt"], "function": "Main function", "example_components": []}]
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " +
                   ", ".join(a.get("subsystem", "?") for a in analysis)
    })
    return {"analysis": analysis}


def research_node(state: AgentState, config) -> dict:
    analysis = state.get("analysis", [])
    if not analysis:
        _emit(config, "agent:log", {"message": "No subsystems to research."})
        return {"research_results": []}

    all_results = []

    # ── Priority group: part numbers the user typed verbatim ──
    # These MUST surface in the search results regardless of what the
    # analyze LLM produced, so select_node can pick the exact part.
    user_parts = _extract_part_numbers(state.get("prompt", ""))
    if user_parts:
        _emit(config, "agent:thinking", {"message": f"Searching user-specified parts: {', '.join(user_parts)}..."})
        results, seen = [], set()
        for part in user_parts:
            try:
                for r in search_components(part, k=3):
                    if r["id_str"] not in seen:
                        seen.add(r["id_str"])
                        results.append(r)
            except Exception as e:
                print(f"Search failed for user part '{part}': {e}")
        if results:
            all_results.append({
                "subsystem": f"User-specified parts ({', '.join(user_parts)})",
                "function": "Parts explicitly requested by the user — MUST be selected when matching",
                "results": results[:8],
            })
            _emit(config, "agent:log", {
                "message": f"  User-specified parts: found {len(results)} candidates"
            })

    for sub in analysis:
        name = sub.get("subsystem", sub if isinstance(sub, str) else "unknown")
        examples = sub.get("example_components", [])
        if isinstance(examples, str):
            examples = [examples]
        # Example part numbers FIRST so exact-part hits rank ahead of
        # generic subsystem-name matches in the deduped top-k.
        queries = (examples[:2] if isinstance(examples, list) else []) + [name]
        _emit(config, "agent:thinking", {"message": f"Searching components for {name}..."})

        results = []
        for q in queries:
            try:
                results.extend(search_components(q, k=4))
            except Exception as e:
                print(f"Search failed for '{q}': {e}")

        seen = set()
        deduped = []
        for r in results:
            if r["id_str"] not in seen:
                seen.add(r["id_str"])
                deduped.append(r)

        all_results.append({
            "subsystem": name,
            "function": sub.get("function", ""),
            "results": deduped[:3], # Only 3 candidates to save tokens
        })
        _emit(config, "agent:log", {
            "message": f"  {name}: found {len(deduped)} candidates"
        })

    return {"research_results": all_results}


def select_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})

    research = state.get("research_results", [])
    if not research:
        _emit(config, "agent:log", {"message": "No research results to select from."})
        return {"selected_components": []}

    results_json = json.dumps(research, indent=2)
    # Truncate descriptions aggressively to avoid TPM limits
    if len(results_json) > 6000:
        truncated = []
        for sub in research:
            tsub = sub.copy()
            tsub["results"] = []
            for r in sub.get("results", []):
                tr = r.copy()
                if len(tr.get("text", "")) > 100:
                    tr["text"] = tr["text"][:97] + "..."
                tsub["results"].append(tr)
            truncated.append(tsub)
        results_json = json.dumps(truncated, indent=2)

    text = _call_llm(SELECT_SYSTEM, SELECT_USER.format(
        prompt=state["prompt"], results_json=results_json
    ))
    text = _clean_json(text)
    try:
        selected = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse selection JSON: {text[:200]}")
        selected = []

    if not selected:
        ref_letters = "URCL"
        selected = []
        for i, sub in enumerate(research):
            results = sub.get("results", [])
            if results:
                best = results[0]
                ref = f"{ref_letters[i % len(ref_letters)]}{i + 1}"
                selected.append({
                    "id_str": best["id_str"],
                    "ref_des": ref,
                    "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                    "description": best.get("text", ""),
                })
    else:
        valid_ids = set()
        for sub in research:
            for r in sub.get("results", []):
                valid_ids.add(r["id_str"])
        filtered = []
        for s in selected:
            if s["id_str"] in valid_ids:
                filtered.append(s)
            else:
                _emit(config, "agent:log", {"message": f"  Rejected hallucinated ID: {s['id_str']}"})
                for sub in research:
                    results = sub.get("results", [])
                    if results:
                        filtered.append({
                            "id_str": results[0]["id_str"],
                            "ref_des": s["ref_des"],
                            "category": results[0]["id_str"].split(":")[0] if ":" in results[0]["id_str"] else "General",
                            "description": results[0].get("text", ""),
                        })
                        break
        selected = filtered

    # Deduplicate: complex ICs must be unique; passives (R, C, L, Y) may repeat.
    # Also normalize reference designator prefixes (no more "C3" microcontrollers).
    seen_ids = set()
    seen_refs = set()
    deduped = []
    ref_counter = {}

    def _next_ref(prefix):
        n = ref_counter.get(prefix, 0) + 1
        while f"{prefix}{n}" in seen_refs:
            n += 1
        ref_counter[prefix] = n
        return f"{prefix}{n}"

    for s in selected:
        id_str = s["id_str"]
        category = s.get("category", "")
        passive = _is_passive(id_str, category)

        if not passive and id_str in seen_ids:
            _emit(config, "agent:log", {"message": f"  Skipped duplicate IC: {id_str}"})
            continue

        # Enforce correct ref prefix for the component type
        correct_prefix = _ref_prefix_for(id_str, category)
        current_prefix = ''.join(c for c in s.get("ref_des", "") if c.isalpha()) or 'U'
        if current_prefix != correct_prefix or s.get("ref_des", "") in seen_refs:
            old_ref = s.get("ref_des", "?")
            s["ref_des"] = _next_ref(correct_prefix)
            if old_ref != s["ref_des"]:
                _emit(config, "agent:log", {"message": f"  Renamed {old_ref} -> {s['ref_des']}"})
        else:
            num_part = ''.join(c for c in s["ref_des"] if c.isdigit())
            if num_part:
                ref_counter[correct_prefix] = max(ref_counter.get(correct_prefix, 0), int(num_part))

        seen_ids.add(id_str)
        seen_refs.add(s["ref_des"])
        deduped.append(s)

    selected = deduped

    # ── Post-selection fix: replace Interface_USB ICs with actual connectors ──
    # When the user asks for "USB-C power input", the LLM often picks FUSB302 (a
    # USB PD controller IC) because it appears first in search results. Swap it
    # for a real connector from the Connector library.
    for s in selected:
        id_str = s["id_str"]
        cat = (s.get("category", "") or "").upper()
        desc = (s.get("description", "") or "").upper()
        # Check if this is an interface IC that should be a connector
        if cat.startswith("INTERFACE_USB") or "FUSB" in id_str.upper():
            # Search for a matching connector
            query = "USB-C connector" if "USB-C" in desc or "TYPE-C" in desc else "USB connector"
            try:
                for r in search_components(query, k=6):
                    r_cat = (r.get("category", "") or "").upper()
                    if r_cat.startswith("CONNECTOR") and "USB" in r_cat:
                        old = s["id_str"]
                        s["id_str"] = r["id_str"]
                        s["category"] = r.get("category", s["category"])
                        s["description"] = r.get("text", s.get("description", ""))
                        _emit(config, "agent:log", {
                            "message": f"  Swapped {old} -> {s['id_str']} (real connector)"
                        })
                        break
            except Exception as e:
                print(f"Connector swap failed: {e}")

    _emit(config, "agent:log", {
        "message": f"Selected {len(selected)} components: " +
                   ", ".join(f'{s["ref_des"]}={s["id_str"].split(":")[-1][:20]}' for s in selected)
    })
    return {"selected_components": selected}


def dispatch_node(state: AgentState, config) -> dict:
    pin_matrix = {}
    component_ops = {}

    for comp in state.get("selected_components", []):
        id_str = comp["id_str"]
        ref_des = comp["ref_des"]

        _emit(config, "agent:thinking", {"message": f"Loading {ref_des} ({id_str})..."})

        ops = []
        try:
            sexpr = fetch_sexpr(id_str)
            # Pass the library name from id_str (e.g. "Power_Management"),
            # not the LLM-assigned category — extends resolution needs it.
            ops = _parse_sexpr_to_ops(sexpr, id_str.split(":")[0])
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  Failed to load {ref_des}: trying search fallback..."})
            try:
                results = search_components(comp.get("description", ref_des), k=5)
                for r in results:
                    try:
                        sexpr = fetch_sexpr(r["id_str"])
                        ops = _parse_sexpr_to_ops(sexpr, r["id_str"].split(":")[0])
                        if ops:
                            id_str = r["id_str"]
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        if not ops:
            _emit(config, "agent:log", {"message": f"  Skipped {ref_des}: no symbol found"})
            continue

        _emit(config, "agent:component", {
            "id_str": id_str,
            "category": comp["category"],
            "ref_des": ref_des,
            "description": comp.get("description", ""),
            "ops": ops,
        })

        pins = _extract_pins_from_ops(ops, ref_des)
        
        # Log pin details for debugging
        pin_names = [f"{p['pin_num']}:{p['name']}" for p in pins.values()]
        _emit(config, "agent:log", {
            "message": f"  Added {ref_des} ({len(pins)} pins): {', '.join(pin_names[:5])}" + 
                      (f"... +{len(pins)-5} more" if len(pins) > 5 else "")
        })
        
        pin_matrix.update(pins)
        component_ops[ref_des] = ops

    _emit(config, "agent:log", {"message": "All components loaded. Planning layout and routing..."})

    return {"pin_matrix": pin_matrix, "component_ops": component_ops}


MAX_BATCH_PINS = 36  # max signal pins per LLM netlist call


def _merge_net(nets: list, name: str, new_pins: list):
    """Add pins to an existing net (matched case-insensitively by name) or create it."""
    for n in nets:
        if n["net"].upper() == name.upper():
            n["pins"].extend(p for p in new_pins if p not in n["pins"])
            return
    nets.append({"net": name, "pins": list(new_pins)})


def _find_net_by_name(nets: list, name: str):
    """Find a net by case-insensitive name."""
    up = name.upper()
    for n in nets:
        if n["net"].upper() == up:
            return n
    return None


def _make_signal_batches(pin_keys: list, max_pins: int = MAX_BATCH_PINS) -> list:
    """Split components into batches of refs, each capped at ~max_pins signal pins.

    The hub component (most pins, usually the MCU) is included in EVERY batch so
    each batch can wire its peripherals directly to real MCU pins instead of
    hallucinating them.
    """
    by_ref = {}
    for k in pin_keys:
        by_ref.setdefault(k.split(":")[0], []).append(k)
    refs = sorted(by_ref, key=lambda r: -len(by_ref[r]))
    if not refs:
        return []

    hub = refs[0] if len(refs) > 1 and len(by_ref[refs[0]]) >= 6 else None
    others = [r for r in refs if r != hub]

    batches, cur, cnt = [], [], 0
    for r in others:
        n = len(by_ref[r])
        if cur and cnt + n > max_pins:
            batches.append(cur)
            cur, cnt = [], 0
        cur.append(r)
        cnt += n
    if cur:
        batches.append(cur)
    if not batches:
        batches = [[]]
    if hub:
        batches = [[hub] + b for b in batches]
    return batches


def netlist_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})

    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": [], "nets": [], "power_pins": []}

    comps_desc = "\n".join(
        f'  {c["ref_des"]}: {c["id_str"]} ({c["category"]})'
        for c in comps
    )

    # ── Phase 0: Pin Classification Dump ────────────────────────────────────
    print("\n" + "="*20 + " PIN CLASSIFICATION " + "="*20)
    for ref in sorted({k.split(':')[0] for k in pins}):
        print(f"\nComponent: {ref}")
        ref_pins = {k: v for k, v in pins.items() if k.startswith(f"{ref}:")}
        for k, p in sorted(ref_pins.items(), key=lambda x: x[0]):
            pname = p.get("name", "").upper()
            etype = p.get("etype", "").lower()
            
            # Classification
            cat = "SIGNAL"
            if _is_gnd_net(pname): cat = "GROUND"
            elif _is_power_net(pname): cat = "POWER"
            elif etype in POWER_ETYPES: cat = "POWER (structural)"
            elif any(x in pname for x in ("XTAL", "OSC", "XIN", "XOUT")): cat = "XTAL"
            
            print(f"  {k.split(':')[-1]:<4} {pname:<15} {etype:<15} -> {cat}")
    print("\n" + "="*56 + "\n")

    # ── Phase 1: deterministic power/GND assignment (no LLM, zero hallucination) ──
    # Primary signal: the pin's KiCad electrical type (power_in/power_out) —
    # a structural property that generalizes across the whole library.
    # Name lists are only secondary, used to pick GND vs a specific rail.
    assigned = set()
    power_groups = {}
    structural_power = set()
    for key, pin in pins.items():
        pname = pin.get("name", "").strip().upper()
        etype = pin.get("etype", "")
        if _is_gnd_net(pname):
            power_groups.setdefault("GND", []).append(key)
            assigned.add(key)
        elif _is_power_net(pname):
            canon = pname.lstrip('+')
            if canon in ("VCC", "VDD"):
                canon = "3V3"
            power_groups.setdefault(canon, []).append(key)
            assigned.add(key)
        elif etype in POWER_ETYPES and pname and pname != "~":
            # Structurally a power pin with a non-standard name (e.g. VREG,
            # VOUTA, V1): create a power net named after the pin itself so
            # the component is deterministically pulled into a real net —
            # this kills floating islands for the whole library.
            canon = pname.lstrip('+')
            power_groups.setdefault(canon, []).append(key)
            structural_power.add(canon)
            assigned.add(key)
    nets = [{"net": n, "pins": p} for n, p in power_groups.items()]
    _emit(config, "agent:log", {
        "message": f"  Power/GND pre-assigned deterministically: {len(assigned)} pins -> "
                   f"{', '.join(power_groups) or 'none'}"
    })

    # ── Phase 2: batched LLM signal-net generation with per-batch validation ──
    signal_keys = [k for k in pins if k not in assigned]
    batches = _make_signal_batches(signal_keys, max_pins=MAX_BATCH_PINS)
    if len(batches) > 1:
        _emit(config, "agent:log", {
            "message": f"  Wiring {len(signal_keys)} signal pins in {len(batches)} batches"
        })

    for bi, batch_refs in enumerate(batches, 1):
        batch_keys = sorted(
            k for k in signal_keys
            if k.split(":")[0] in batch_refs and k not in assigned
        )
        if not batch_keys:
            continue

        _emit(config, "agent:thinking", {
            "message": f"Planning pin connections (batch {bi}/{len(batches)})..."
        })
        pins_desc = "\n".join(f'  Key="{k}"  name="{pins[k]["name"]}"' for k in batch_keys)
        existing = ", ".join(n["net"] for n in nets) or "(none yet)"

        text = _call_llm(NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER.format(
            prompt=state["prompt"],
            components_desc=comps_desc,
            existing_nets=existing,
            pins_desc=pins_desc,
        ))
        text = _clean_json(text)
        try:
            batch_nets = json.loads(text) if text else []
        except json.JSONDecodeError:
            print(f"Batch {bi}: failed to parse nets JSON: {text[:200]}")
            batch_nets = []
        if not isinstance(batch_nets, list):
            continue

        n_dropped = 0
        n_resolved = 0
        batch_key_set = set(batch_keys)
        for net in batch_nets:
            if not isinstance(net, dict):
                continue
            name = str(net.get("net", "")).strip()
            raw = net.get("pins", [])
            if not name or not isinstance(raw, list):
                continue
            clean = []
            for p in raw:
                if p in batch_key_set and p not in assigned:
                    assigned.add(p)
                    clean.append(p)
                else:
                    # Try to rescue hallucinated pin ref via name/number matching
                    resolved = _resolve_hallucinated_pin(p, pins, assigned)
                    if resolved and resolved in batch_key_set and resolved not in assigned:
                        assigned.add(resolved)
                        clean.append(resolved)
                        n_resolved += 1
                    else:
                        n_dropped += 1
            if clean:
                _merge_net(nets, name, clean)
        if n_dropped or n_resolved:
            _emit(config, "agent:log", {
                "message": f"  Batch {bi}: resolved {n_resolved}, dropped {n_dropped} hallucinated pin refs"
            })

    # ── Phase 3: rule-based fallback for pins the LLM left unassigned ──
    leftover = {k: pins[k] for k in pins if k not in assigned}
    if leftover:
        for net in _generate_nets_fallback(leftover):
            _merge_net(nets, net["net"], net["pins"])
        _emit(config, "agent:log", {
            "message": f"  Name-match fallback assigned {len(leftover)} leftover pins"
        })

    # ── Validate nets: pins must exist, each pin in at most one net ──
    used_pins = set()
    valid_nets = []
    for net in nets:
        if not isinstance(net, dict):
            continue
        name = str(net.get("net", "")).strip()
        net_pins = net.get("pins", [])
        if not name or not isinstance(net_pins, list):
            continue
        clean = []
        for p in net_pins:
            if p not in pins:
                _emit(config, "agent:log", {"message": f"  Dropped invalid pin: {p} (net {name})"})
                continue
            if p in used_pins:
                _emit(config, "agent:log", {"message": f"  Dropped duplicate pin: {p} (net {name})"})
                continue
            used_pins.add(p)
            clean.append(p)
        if clean:
            is_pwr = (_is_gnd_net(name) or _is_power_net(name)
                      or name.upper().lstrip('+') in structural_power)
            if len(clean) >= 2 or (is_pwr and len(clean) >= 1) or (len(clean) == 1 and not is_pwr):
                valid_nets.append({"net": name, "pins": clean})

    # ── ERC check: power/GND nets must not contain signal-looking pins shorted to rails ──
    # (basic sanity — a GND net containing a pin named "3V3" etc. is a short)
    for net in valid_nets:
        name = net["net"]
        is_pwr = _is_gnd_net(name) or _is_power_net(name) or name.upper().lstrip('+') in structural_power
        if is_pwr:
            to_remove = []
            for p in net["pins"]:
                pname = pins[p].get("name", "").upper()
                # 1) Power name mismatch (e.g. 3V3 pin in GND net)
                if _is_gnd_net(name) and _is_power_net(pname):
                    to_remove.append(p)
                # 2) Signal pin in power net (e.g. SDA in 3V3 net)
                elif any(s in pname for s in ("SDA", "SCL", "TX", "RX", "MOSI", "MISO", "SCK", "CS", "DQ", "DP", "DM", "USB")):
                    # Passive/Power types might legitimately have these names in some contexts
                    # but in a general schematic, a DQ pin in a power net is almost certainly an LLM error.
                    if pins[p].get("etype") not in POWER_ETYPES:
                        to_remove.append(p)
            
            for p in to_remove:
                net["pins"].remove(p)
                used_pins.discard(p)
                _emit(config, "agent:log", {"message": f"  ERC: removed signal pin {p} ({pins[p].get('name')}) from power net {name}"})

    # ── Netlist Diagnostic Dump & Quality Check ──────────────────────────────
    print("\n" + "="*20 + " GENERATED NETS " + "="*20)
    for net in valid_nets:
        net_name = net["net"]
        pin_keys = net["pins"]
        print(f"\nNET: {net_name}")
        
        # Quality heuristics
        warnings = []
        name_up = net_name.upper()
        rx_pins = []
        tx_pins = []
        has_xtal = "XTAL" in name_up or "OSC" in name_up

        for pk in pin_keys:
            p = pins.get(pk, {})
            pname = p.get("name", "").upper()
            etype = p.get("etype", "").lower()
            print(f"  {pk:<10} {p.get('ref_des')}:{pname:<15} ({etype})")
            
            if "RX" in pname: rx_pins.append(pk)
            if "TX" in pname: tx_pins.append(pk)

            # XTAL ↔ Signal check
            is_xtal_pin = any(x in pname for x in ("XTAL", "OSC", "XIN", "XOUT", "X1", "X2"))
            if has_xtal and not is_xtal_pin and etype not in ("passive", "power_in"):
                warnings.append(f"  [!] Suspicious: Non-XTAL pin {pk} ({pname}) in XTAL net")
            if is_xtal_pin and not has_xtal and "GND" not in name_up:
                warnings.append(f"  [!] Suspicious: XTAL pin {pk} in non-XTAL net {net_name}")
            
            # POWER ↔ Signal check
            is_power_net = _is_power_net(net_name) or name_up in structural_power
            if is_power_net and any(s in pname for s in ("SDA", "SCL", "TX", "RX", "MOSI", "MISO", "SCK", "CS")):
                warnings.append(f"  [!] Suspicious: Signal pin {pk} ({pname}) in power net {net_name}")
            
            # RESET ↔ Signal check
            if ("RESET" in name_up or "RST" in name_up) and any(s in pname for s in ("SDA", "SCL", "TX", "RX")):
                warnings.append(f"  [!] Suspicious: Signal pin {pk} ({pname}) in reset net")

        # Connection logic checks
        if len(rx_pins) > 1 and "RX" in name_up:
            warnings.append(f"  [!] Suspicious: Multiple RX pins {rx_pins} connected together (RX-RX short?)")
        if len(tx_pins) > 1 and "TX" in name_up:
            warnings.append(f"  [!] Suspicious: Multiple TX pins {tx_pins} connected together (TX-TX short?)")
        
        # GPIO super-net check
        if name_up == "GPIO" and len(pin_keys) > 4:
             warnings.append(f"  [!] Suspicious: Large 'GPIO' net ({len(pin_keys)} pins). Likely hallucination.")
        
        if warnings:
            for w in warnings:
                print(w)
            _emit(config, "agent:log", {"message": f"  Net Quality Report: {len(warnings)} issues in net {net_name}"})

    print("\n" + "="*56 + "\n")

    # ── Split: power/GND nets become labels (no wires), signal nets get routed ──
    power_pins = []   # [{pin, net}] -> rendered as power symbols / global labels
    netlist = []      # pairwise signal connections for the A* router
    n_power_nets = 0
    n_signal_nets = 0

    for net in valid_nets:
        name = net["net"]
        if (_is_gnd_net(name) or _is_power_net(name)
                or name.upper().lstrip('+') in structural_power):
            n_power_nets += 1
            canonical = "GND" if _is_gnd_net(name) else name.upper().lstrip('+')
            for p in net["pins"]:
                power_pins.append({"pin": p, "net": canonical})
        else:
            n_signal_nets += 1
            ps = net["pins"]
            for i in range(len(ps) - 1):
                netlist.append({"source": ps[i], "target": ps[i + 1], "net": name})

    # ── Orphan detection: every component should touch at least one net ──
    connected_refs = set()
    for conn in netlist:
        connected_refs.add(conn["source"].split(":")[0])
        connected_refs.add(conn["target"].split(":")[0])
    for pp in power_pins:
        connected_refs.add(pp["pin"].split(":")[0])

    all_refs = {c["ref_des"] for c in state.get("selected_components", [])}
    orphans = sorted(all_refs - connected_refs)
    if orphans:
        _emit(config, "agent:log", {
            "message": f"  WARNING: {len(orphans)} unconnected component(s): {', '.join(orphans)}. "
                       f"Attaching their power/ground pins to nets."
        })
        # Rescue: pull any GND/power-named pin of an orphan into the proper net
        for ref in orphans:
            for key, pin in pins.items():
                if key.split(":")[0] != ref or key in used_pins:
                    continue
                pname = pin.get("name", "").upper()
                if _is_gnd_net(pname):
                    power_pins.append({"pin": key, "net": "GND"})
                    used_pins.add(key)
                elif _is_power_net(pname):
                    power_pins.append({"pin": key, "net": pname.lstrip('+')})
                    used_pins.add(key)
                elif pin.get("etype") in POWER_ETYPES and pname and pname != "~":
                    power_pins.append({"pin": key, "net": pname.lstrip('+')})
                    used_pins.add(key)

    _emit(config, "agent:log", {
        "message": f"Nets: {n_power_nets} power/GND ({len(power_pins)} pins as power symbols), "
                   f"{n_signal_nets} signal ({len(netlist)} wire connections)"
    })
    return {"netlist": netlist, "nets": valid_nets, "power_pins": power_pins}


def layout_route_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Computing layout and routing wires..."})

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])

    if not comps or not comp_ops:
        _emit(config, "agent:done", {"message": "No components to place."})
        return {}

    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(ref_des, ops, comp["category"])

    if not engine.components:
        _emit(config, "agent:done", {"message": "No components could be placed."})
        return {}

    engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)
    engine.build_obstacle_matrix(pin_matrix=pin_matrix)
    traces = engine.route_traces(netlist, pin_matrix)

    # ── Post-route validation: detect & fix parallel wire overlaps ──
    traces, n_fixed, n_conflicts = engine.check_and_fix_overlaps(traces)
    if n_fixed or n_conflicts:
        _emit(config, "agent:log", {
            "message": f"  Overlap check: {n_fixed} wire(s) re-routed"
                       + (f", {n_conflicts} unresolved overlap(s) remain" if n_conflicts else "")
        })

    placements = engine.get_placements()

    # ── Compute power symbol/label positions (absolute coords + direction) ──
    power_labels = []
    for pp in power_pins:
        pin = pin_matrix.get(pp["pin"])
        if not pin:
            continue
        ref = pp["pin"].split(":")[0]
        comp = engine._get_comp(ref)
        if not comp:
            continue
        ax = pin["x"] + comp["x"]
        ay = pin["y"] + comp["y"]
        # Outward direction (away from component center)
        ccx = comp["x"] + comp["bbox"]["x"] + comp["bbox"]["w"] / 2
        ccy = comp["y"] + comp["bbox"]["y"] + comp["bbox"]["h"] / 2
        dx = ax - ccx
        dy = ay - ccy
        if abs(dx) >= abs(dy):
            direction = "right" if dx >= 0 else "left"
        else:
            direction = "up" if dy >= 0 else "down"
        power_labels.append({
            "pin": pp["pin"],
            "net": pp["net"],
            "x": ax,
            "y": ay,
            "dir": direction,
        })

    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })

    _emit(config, "agent:done", {
        "message": f"Design complete: {len(engine.components)} components, "
                   f"{len(traces)} signal wires, {len(power_labels)} power symbols"
    })

    return {
        "component_placements": placements,
        "wire_paths": traces,
        "power_labels": power_labels,
    }


# Pin name alias groups — maps messy KiCad names to canonical electrical functions
PIN_ALIASES = {
    # I2C
    "SDA": {"SDA", "SDI", "SDIO", "I2C0_SDA", "I2C1_SDA", "I2C_DATA", "I2CDAT",
            "GPIO21", "IO21", "PIN21", "I2C_SDA", "SDA0", "SDA1"},
    "SCL": {"SCL", "SCK", "I2C0_SCL", "I2C1_SCL", "I2C_CLK", "I2CCLK",
            "GPIO22", "IO22", "PIN22", "I2C_SCL", "SCL0", "SCL1"},
    # UART
    "TX": {"TXD", "TX", "TXD0", "TXD1", "UART_TX", "UART0_TX", "UART1_TX", "TXD_0", "TXD_1", "TX0", "TX1",
           "GPIO1", "GPIO6", "GPIO7", "TXD2"},
    "RX": {"RXD", "RX", "RXD0", "RXD1", "UART_RX", "UART0_RX", "UART1_RX", "RXD_0", "RXD_1", "RX0", "RX1",
           "GPIO2", "GPIO3", "GPIO8", "RXD2"},
    # SPI
    "MOSI": {"MOSI", "SPI_MOSI", "SPI0_MOSI", "SPI1_MOSI", "SI", "SDO"},
    "MISO": {"MISO", "SPI_MISO", "SPI0_MISO", "SPI1_MISO", "SO", "SDI"},
    "SCK": {"SCK", "SPI_SCK", "SPI0_SCK", "SPI1_SCK", "SPI_CLK", "SPICLK"},
    "CS": {"CS", "SS", "NSS", "SPI_CS", "SPI0_CS", "SPI1_CS", "CHIP_SELECT", "CE"},
    # Crystal / Oscillator
    "XTAL1": {"XTAL1", "XTAL_IN", "OSC_IN", "OSCI", "OSC0_IN", "OSC1_IN", "XIN"},
    "XTAL2": {"XTAL2", "XTAL_OUT", "OSC_OUT", "OSCO", "OSC0_OUT", "OSC1_OUT", "XOUT"},
    # Reset / Enable
    "RESET": {"RST", "RESET", "NRST", "N_RST", "nRST", "NRESET", "N_RESET", "RST_N", "RSTB"},
    "EN": {"EN", "ENABLE", "CHIP_EN", "CEN", "CE_N", "SHDN", "SHDN_N", "ON_OFF"},
    # Interrupts
    "INT": {"INT", "IRQ", "NINT", "N_IRQ", "nINT", "INT_N", "IRQ_N"},
    # Status / Indicator
    "STAT": {"STAT", "STATE", "STATUS", "CHG_STAT", "CHG_STATE", "FAULT", "PG", "POWER_GOOD"},
}

# Complementary signal pairs — TX on one device connects to RX on another
COMPLEMENTARY_PAIRS = [
    ("TX", "RX"),
    ("RX", "TX"),
    ("MOSI", "MISO"),  # actually these are separate nets, but handled by alias groups
    ("MISO", "MOSI"),
]


def _canonical_signal_name(name: str):
    """Map a raw pin name to its canonical signal name if it matches an alias group."""
    upper = name.upper().strip()
    for canon, aliases in PIN_ALIASES.items():
        if upper in aliases:
            return canon
    return None


def _resolve_hallucinated_pin(bad_key: str, pin_matrix: dict, assigned: set) -> str | None:
    """Rescue a hallucinated pin key by matching its pin name/number against real pins.

    The LLM often outputs keys like 'U1:21' (guessing pin number 21 = GPIO21)
    when the real key is 'U1:3' (pin 3 has name='GPIO21'). This function looks
    up the ref's actual pins and finds the best match by pin number, pin name,
    or alias group.
    """
    ref = bad_key.split(':')[0]
    hint = bad_key.split(':')[1] if ':' in bad_key else ''

    # Get all unassigned pins for this ref
    candidates = []
    for key, pin in pin_matrix.items():
        if key.split(':')[0] == ref and key not in assigned:
            candidates.append((key, pin))

    if not hint:
        return None

    hint_upper = hint.upper()

    # Strategy 1: hint matches pin_num literally
    for key, pin in candidates:
        if pin.get('pin_num', '') == hint:
            return key

    # Strategy 2: hint matches pin name (e.g. hint="GPIO21" → name="GPIO21")
    for key, pin in candidates:
        pname = pin.get('name', '').upper()
        if pname == hint_upper:
            return key

    # Strategy 3: hint is a number, look for pin name ending with that number
    # (e.g. hint="21" → name="GPIO21" or "IO21" or "PIN21")
    if hint.isdigit():
        for key, pin in candidates:
            pname = pin.get('name', '').upper()
            if pname.endswith(hint) and not pname.startswith(('1', '2', '3', '4', '5', '6', '7', '8', '9')):
                return key
            if pname == f"IO{hint}" or pname == f"PIN{hint}" or pname == f"GPIO{hint}":
                return key

    # Strategy 4: hint name aliases to a canonical signal, and a pin matches
    hint_canon = _canonical_signal_name(hint)
    if hint_canon:
        for key, pin in candidates:
            pname = pin.get('name', '').upper()
            if _canonical_signal_name(pname) == hint_canon:
                return key

    # Strategy 5: hint is a common signal name, try to find an IO pin (last resort)
    if hint_upper in PIN_ALIASES:
        for key, pin in candidates:
            etype = pin.get('etype', '')
            pname = pin.get('name', '').upper()
            if etype in ('bidirectional', 'input', 'output') and pname.startswith('IO'):
                return key

    return None


def _generate_nets_fallback(pin_matrix: dict) -> list:
    """Rule-based fallback: group pins into named nets by pin name with aliasing."""
    by_name = {}
    for key, pin in pin_matrix.items():
        name = pin.get("name", "").strip().upper()
        if not name or name in ("~", "NC", ""):
            continue
        by_name.setdefault(name, []).append(key)

    nets = []

    # GND net: collect every ground-named pin
    gnd_pins = []
    for name in list(by_name.keys()):
        if _is_gnd_net(name):
            gnd_pins.extend(by_name.pop(name))
    if gnd_pins:
        nets.append({"net": "GND", "pins": gnd_pins})

    # Power nets grouped by canonical voltage name
    power_groups = {}
    for name in list(by_name.keys()):
        if _is_power_net(name):
            canon = name.lstrip('+')
            if canon in ("VCC", "VDD"):
                canon = "3V3"
            power_groups.setdefault(canon, []).extend(by_name.pop(name))
    for canon, pins_list in power_groups.items():
        nets.append({"net": canon, "pins": pins_list})

    # ── Signal nets: group by canonical alias first ──
    signal_groups = {}  # canonical_name -> list of pin keys
    unmatched = []       # pin keys that didn't match any alias group

    for name, keys in by_name.items():
        canon = _canonical_signal_name(name)
        if canon:
            signal_groups.setdefault(canon, []).extend(keys)
        else:
            unmatched.append((name, keys))

    for canon, pins in signal_groups.items():
        if len(pins) >= 1:
            nets.append({"net": canon.upper(), "pins": pins})

    # Second pass: merge unmatched pins into existing canonical alias groups
    still_unmatched = []
    for name, keys in unmatched:
        canon = _canonical_signal_name(name)
        if canon and canon.upper() in {n["net"] for n in nets}:
            # Merge into existing canonical net
            for n in nets:
                if n["net"] == canon.upper():
                    n["pins"].extend(keys)
                    break
        else:
            still_unmatched.append((name, keys))
    unmatched = still_unmatched

    # Match remaining unmatched by exact name (at least 2 pins sharing the same name)
    for name, keys in unmatched:
        if len(keys) >= 2:
            nets.append({"net": name, "pins": keys})

    # Remaining single unmatched pins: attach as standalone signal nets
    # so they appear as labels (better than dropping them entirely)
    leftover = {name: keys for name, keys in unmatched if len(keys) == 1}
    for name, keys in leftover.items():
        nets.append({"net": name, "pins": keys})

    return nets


def _parse_sexpr_to_ops(sexpr_str: str, lib_name: str, _depth: int = 0) -> list:
    acc = []
    extends = None

    def parse(s):
        tokens, i = [], 0
        while i < len(s):
            c = s[i]
            if c == '(':
                tokens.append(c); i += 1
            elif c == ')':
                tokens.append(c); i += 1
            elif c in ' \t\n\r':
                i += 1
            elif c == '"':
                j = i + 1
                while j < len(s) and not (s[j] == '"' and s[j-1] != '\\'):
                    j += 1
                tokens.append(s[i:j+1]); i = j + 1
            else:
                j = i
                while j < len(s) and s[j] not in '() \t\n\r':
                    j += 1
                tokens.append(s[i:j]); i = j

        stack, root = [], []
        stack.append(root)
        for t in tokens:
            if t == '(':
                n = []; stack[-1].append(n); stack.append(n)
            elif t == ')':
                if len(stack) > 1: stack.pop()
            else:
                v = t[1:-1] if t.startswith('"') and t.endswith('"') else t
                stack[-1].append(v)
        return root[0] if root else []

    ast = parse(sexpr_str)
    if not ast:
        return acc

    def walk(node):
        nonlocal extends
        if not isinstance(node, list) or not node:
            return
        typ = node[0]
        if typ in ("rectangle", "polyline", "circle", "arc", "pin", "property", "text"):
            acc.append(node)
        # Derived symbols declare a parent via (extends "ParentName") —
        # it is a direct child of the symbol node, NOT one of the drawing ops,
        # so it must be captured here during the walk.
        if typ == "extends" and len(node) > 1 and extends is None:
            extends = node[1]
        if typ in ("symbol", "kicad_symbol_lib"):
            for child in node[1:]:
                walk(child)

    walk(ast)

    # Resolve inheritance: derived symbols (e.g. LTC4417HUF -> LTC4417CUF)
    # carry only properties; all pins/graphics live in the parent symbol.
    # The parent always lives in the same library, so resolve against
    # lib_name (the library prefix of id_str), never an LLM category label.
    if extends and _depth < 5:
        try:
            parent_sexpr = fetch_sexpr(f"{lib_name}:{extends}")
            parent_ops = _parse_sexpr_to_ops(parent_sexpr, lib_name, _depth + 1)
            # Parent ops first, child ops after — child properties
            # (Reference/Value/Footprint) override the parent's downstream.
            parent_ops.extend(acc)
            return parent_ops
        except Exception as e:
            print(f"Failed to resolve extends '{extends}' in lib '{lib_name}': {e}")

    return acc


# Valid KiCad pin electrical types — op[1] of a pin node is one of these.
# This is a STRUCTURAL property of the symbol, independent of pin naming.
KICAD_PIN_ETYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
}
POWER_ETYPES = ("power_in", "power_out")


def _extract_pins_from_ops(ops: list, ref_des: str) -> dict:
    GRID_SIZE = 1.27
    pin_matrix = {}

    for op in ops:
        if op[0] != "pin":
            continue

        # Electrical type: (pin power_in line (at ...) ...) -> op[1]
        etype = op[1] if len(op) > 1 and isinstance(op[1], str) and op[1] in KICAD_PIN_ETYPES else ""

        at = _get_attr(op, "at")
        len_node = _get_attr(op, "length")
        num_node = _get_attr(op, "number")
        if not at or not len_node or not num_node:
            continue

        try:
            px = float(at[1])
            py = float(at[2])
            ang_deg = float(at[3]) if len(at) > 3 else 0
            length = float(len_node[1])
        except (ValueError, IndexError):
            continue

        # Handle all rotation angles properly
        ang_rad = ang_deg * 3.14159 / 180.0
        cos_a = round(1.0 if ang_deg == 0 else (-1.0 if ang_deg == 180 else 0.0), 2)
        sin_a = round(1.0 if ang_deg == 90 else (-1.0 if ang_deg == 270 else 0.0), 2)
        
        # If not cardinal direction, use actual trig
        if abs(cos_a) < 0.1 and abs(sin_a) < 0.1:
            import math
            cos_a = math.cos(ang_rad)
            sin_a = math.sin(ang_rad)
        
        ex = px + cos_a * length
        ey = py + sin_a * length

        name_node = _get_attr(op, "name")
        pin_name = name_node[1] if name_node else ""
        pin_num = num_node[1].replace('"', '').strip()
        
        # Skip invalid pin numbers
        if not pin_num:
            continue

        key = f"{ref_des}:{pin_num}"
        
        # Avoid duplicate pin numbers (can happen with inherited symbols)
        if key in pin_matrix:
            continue
        
        pin_matrix[key] = {
            "x": round(ex / GRID_SIZE) * GRID_SIZE,
            "y": round(ey / GRID_SIZE) * GRID_SIZE,
            "name": pin_name.strip(),
            "ref_des": ref_des,
            "pin_num": pin_num,
            "etype": etype,
        }

    return pin_matrix


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("analyze", analyze_node)
    builder.add_node("research", research_node)
    builder.add_node("select", select_node)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("netlist", netlist_node)
    builder.add_node("layout_route", layout_route_node)

    builder.set_entry_point("analyze")
    builder.add_edge("analyze", "research")
    builder.add_edge("research", "select")
    builder.add_edge("select", "dispatch")
    builder.add_edge("dispatch", "netlist")
    builder.add_edge("netlist", "layout_route")

    return builder.compile()


agent_graph = build_graph()
