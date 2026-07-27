from agent.utils import _emit


def connectivity_repair_node(state, config):
    issues = state.get("_validation_issues", [])
    missing_issues = [i for i in issues if i.get("code") == "PGV001"]
    geometry_issues = [i for i in issues if i.get("code") in {"PGV005", "PGV006"}]
    if not missing_issues and not geometry_issues:
        _emit(config, "agent:log", {"message": "  Connectivity repair: no fixable issues found"})
        return {}

    netlist = list(state.get("netlist", []))
    nets = state.get("nets", [])
    pin_matrix = state.get("pin_matrix", {})

    already_pending = set()
    for req in state.get("_erc_pending_connections", []):
        already_pending.add((req.get("pin", ""), req.get("net", "")))

    pending = list(state.get("_erc_pending_connections", []))

    for iss in missing_issues:
        pin_key = iss.get("pin", "")
        net_name = iss.get("net", "")
        if not pin_key or not net_name:
            continue

        if (pin_key, net_name) in already_pending:
            continue

        pending.append({"pin": pin_key, "net": net_name})
        already_pending.add((pin_key, net_name))

    _emit(config, "agent:log", {
        "message": (
            f"  Connectivity repair: {len(missing_issues)} missing attachment(s), "
            f"{len(geometry_issues)} malformed route(s)"
        )
    })

    affected_nets = {
        issue.get("net", "") for issue in geometry_issues if issue.get("net")
    }

    return {
        "netlist": netlist,
        "_erc_pending_connections": pending,
        "_erc_affected_nets": sorted(affected_nets),
    }
