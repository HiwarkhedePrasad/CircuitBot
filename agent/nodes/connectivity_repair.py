from agent.utils import _emit


def connectivity_repair_node(state, config):
    issues = state.get("_validation_issues", [])
    pgv001_issues = [i for i in issues if i.get("code") == "PGV001"]
    if not pgv001_issues:
        _emit(config, "agent:log", {"message": "  Connectivity repair: no fixable issues found"})
        return {}

    netlist = list(state.get("netlist", []))
    nets = state.get("nets", [])
    pin_matrix = state.get("pin_matrix", {})

    already_pending = set()
    for req in state.get("_erc_pending_connections", []):
        already_pending.add((req.get("pin", ""), req.get("net", "")))

    pending = list(state.get("_erc_pending_connections", []))

    for iss in pgv001_issues:
        pin_key = iss.get("pin", "")
        net_name = iss.get("net", "")
        if not pin_key or not net_name:
            continue

        if (pin_key, net_name) in already_pending:
            continue

        pending.append({"pin": pin_key, "net": net_name})
        already_pending.add((pin_key, net_name))

    _emit(config, "agent:log", {
        "message": f"  Connectivity repair: {len(pgv001_issues)} PGV001 issue(s), {len(pending)} total pending"
    })

    return {
        "netlist": netlist,
        "_erc_pending_connections": pending,
    }
