from uuid import uuid4

from agent.tools import search_components
from agent.utils import _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result, _extract_part_numbers, _sanitize_data


def research_node(state, config):
    contract = _check_stage_contract("research", state, ["analysis"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "research", {"research_results": []})
    analysis = state.get("analysis", [])
    if not analysis:
        _emit(config, "agent:log", {"message": "No subsystems to research."})
        return {"research_results": []}
    emit_assistant_message(config, "Searching for components across all subsystems...")
    rag_tool_id = uuid4().hex[:8]
    emit_tool_event(config, "Component Research", "running", "Searching for components...",
                    tool_id=rag_tool_id)
    all_results = []
    user_parts = _extract_part_numbers(state.get("prompt", ""))
    if user_parts:
        _emit(config, "agent:thinking", {"message": f"Searching user-specified parts: {', '.join(user_parts)}..."})
        for part in user_parts:
            results, seen = [], set()
            try:
                for r in search_components(part, k=3):
                    if r["id_str"] not in seen:
                        seen.add(r["id_str"])
                        if r.get("text"):
                            r["text"] = _sanitize_data(r["text"], label=f"search:{r['id_str']}")
                        results.append(r)
            except Exception as e:
                print(f"Search failed for user part '{part}': {e}")
            if results:
                all_results.append({
                    "subsystem": f"User-specified ({part})",
                    "function": "Parts explicitly requested by the user — MUST be selected when matching",
                    "bus": "any",
                    "results": results[:8],
                })
                _emit(config, "agent:log", {
                    "message": f"  User-specified part '{part}': found {len(results)} candidate(s)"
                })

    # Phase 1: KiCad RAG search with library filtering
    from agent.knowledge.query_expander import expand_subsystem_query
    import re
    for sub in analysis:
        name = sub.get("subsystem", sub if isinstance(sub, str) else "unknown")
        # If subsystem is User-specified (PartName), extract PartName for clean RAG queries
        clean_name = name
        user_part_match = re.search(r'user-specified\s*\(([^)]+)\)', name, re.IGNORECASE)
        if user_part_match:
            clean_name = user_part_match.group(1).strip()

        examples = sub.get("example_components", [])
        if isinstance(examples, str):
            examples = [examples]
        library_filter = sub.get("library_filter", "")
        if not library_filter and isinstance(sub, dict):
            from agent.templates.matcher import get_library_filter
            library_filter = get_library_filter(sub)
        
        expanded_queries = expand_subsystem_query({"subsystem": clean_name})
        queries = ([clean_name] if user_part_match else []) + expanded_queries + (examples[:2] if isinstance(examples, list) else [])
        _emit(config, "agent:thinking", {"message": f"Searching components for {name}..."})
        results = []
        for q in queries:
            try:
                results.extend(search_components(q, k=4, library_filter=library_filter or None))
            except Exception as e:
                print(f"Search failed for '{q}': {e}")
        # If library-filtered search returned nothing, try without filter
        if not results and library_filter:
            _emit(config, "agent:log", {
                "message": f"  Library filter '{library_filter}' returned no results for '{name}' — falling back to unfiltered search"
            })
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
                if r.get("text"):
                    r["text"] = _sanitize_data(r["text"], label=f"search:{r['id_str']}")
                deduped.append(r)
        # Apply library filter post-hoc if provided
        if library_filter:
            filter_patterns = [f.strip() for f in library_filter.split("|") if f.strip()]
            deduped = [r for r in deduped if any(r["id_str"].startswith(p + ":") or r["id_str"].startswith(p + "_") for p in filter_patterns)]

        # Enrich candidates with LCSC / jlcparts sourcing info
        from kicad_rag.jlcparts_db import search_jlcparts
        for cand in deduped:
            if not cand.get("lcsc"):
                cid = cand.get("id_str", "")
                part_query = cid.split(":")[-1] if ":" in cid else cid
                jlc_hits = search_jlcparts(part_query, limit=1)
                if jlc_hits:
                    hit = jlc_hits[0]
                    cand["lcsc"] = hit.get("LCSC", "")
                    cand["mfr_part"] = hit.get("MFR_Part", "")
                    cand["price"] = hit.get("Price", 0.0)
                    cand["stock"] = hit.get("Stock", 0)

        all_results.append({
            "subsystem": name,
            "function": sub.get("function", ""),
            "bus": sub.get("bus", "any"),
            "results": deduped[:8],
        })
        _emit(config, "agent:log", {
            "message": f"  {name}: found {len(deduped)} candidates" +
                       (f" (filter: {library_filter})" if library_filter else "")
        })


    # Phase 1: Web research via DeepSearch (parallel, 2 concurrent)
    _emit(config, "agent:thinking", {"message": "Searching web for component intelligence (2 concurrent)..."})
    web_tool_id = uuid4().hex[:8]
    emit_tool_event(config, "Web Component Research", "running",
                    "Searching web for each subsystem...", tool_id=web_tool_id)

    web_queries = []
    web_subsystems = []
    for sub in analysis:
        name = sub.get("subsystem", "?")
        function = sub.get("function", "")
        examples = sub.get("example_components", [])
        web_queries.append(
            f"Research electronic components for subsystem: {name}. "
            f"Function: {function}. "
            f"Example components: {examples}. "
            f"Return: recommended component types, popular part numbers, "
            f"typical specifications, and any design considerations."
        )
        web_subsystems.append(name)

    from agent.deep_search import deep_search_parallel
    web_search_results = deep_search_parallel(web_queries, config=config)

    web_results = []
    for i, result in enumerate(web_search_results):
        name = web_subsystems[i]
        web_results.append({
            "subsystem": name,
            "summary": result["summary"],
        })
        if result["success"]:
            _emit(config, "agent:log", {"message": f"  Web research for '{name}': complete"})
        else:
            _emit(config, "agent:log", {"message": f"  Web research for '{name}': {result['summary']}"})

    # Deduplicate research_results by subsystem name before returning
    merged_results = []
    seen_subs = {}
    for entry in all_results:
        sname = entry.get("subsystem", "")
        if sname in seen_subs:
            existing = seen_subs[sname]
            existing_ids = {r["id_str"] for r in existing.get("results", [])}
            for r in entry.get("results", []):
                if r["id_str"] not in existing_ids:
                    existing["results"].append(r)
                    existing_ids.add(r["id_str"])
        else:
            seen_subs[sname] = entry
            merged_results.append(entry)
    all_results = merged_results

    total = sum(len(r.get("results", [])) for r in all_results)
    emit_tool_event(config, "Web Component Research", "completed",
                    f"Researched {len(web_results)} subsystems on the web", tool_id=web_tool_id)
    emit_tool_event(config, "Component Research", "completed",
                    f"Found {total} RAG candidates + {len(web_results)} web summaries across {len(analysis)} subsystems",
                    tool_id=rag_tool_id)
    emit_assistant_message(config, f"Found {total} component candidates across {len(analysis)} subsystems, plus web research data.")
    return {"research_results": all_results, "web_research_results": web_results}
