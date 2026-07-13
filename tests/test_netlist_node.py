import copy

from agent.nodes.netlist import netlist_node


def _cfg():
    return {"configurable": {"emit": None}}


def test_netlist_node_pwr_flag_injection_is_idempotent(monkeypatch):
    monkeypatch.setattr("agent.nodes.netlist._call_llm_with_tools", lambda *args, **kwargs: "[]")
    monkeypatch.setattr(
        "agent.nodes.netlist._fetch_sexpr",
        lambda *args, **kwargs: "",
        raising=False,
    )
    monkeypatch.setattr(
        "agent.tools.fetch_sexpr",
        lambda *args, **kwargs: "",
    )

    state = {
        "prompt": "simple regulator rail",
        "selected_components": [
            {
                "ref_des": "U1",
                "id_str": "Regulator_Linear:AMS1117-3.3",
                "category": "Regulator_Linear",
                "description": "Regulator",
            },
            {
                "ref_des": "C1",
                "id_str": "Device:C_Small",
                "category": "CAPACITOR",
                "description": "Cap",
            },
        ],
        "pin_matrix": {
            "U1:1": {"name": "3V3", "etype": "power_in", "pin_num": "1", "ref_des": "U1"},
            "U1:2": {"name": "GND", "etype": "power_in", "pin_num": "2", "ref_des": "U1"},
            "C1:1": {"name": "3V3", "etype": "passive", "pin_num": "1", "ref_des": "C1"},
            "C1:2": {"name": "GND", "etype": "passive", "pin_num": "2", "ref_des": "C1"},
        },
        "component_ops": {},
    }

    first = netlist_node(copy.deepcopy(state), _cfg())
    rerun_state = copy.deepcopy(state)
    rerun_state["selected_components"] = first.get("selected_components", state["selected_components"])
    rerun_state["pin_matrix"] = first.get("pin_matrix", state["pin_matrix"])
    rerun_state["component_ops"] = first.get("component_ops", state["component_ops"])
    second = netlist_node(rerun_state, _cfg())

    first_flags = [c for c in first.get("selected_components", []) if c.get("id_str") == "power:PWR_FLAG"]
    second_selected = second.get("selected_components", rerun_state["selected_components"])
    second_flags = [c for c in second_selected if c.get("id_str") == "power:PWR_FLAG"]
    assert len(first_flags) == 2
    assert len(second_flags) == 2
    assert len({c["ref_des"] for c in second_flags}) == 2


def test_netlist_node_uses_star_edges_for_multi_pin_signal_net(monkeypatch):
    monkeypatch.setattr(
        "agent.nodes.netlist._call_llm_with_tools",
        lambda *args, **kwargs: '[{"net": "BUS", "pins": ["U1:1", "R1:1", "D1:1"]}]',
    )

    state = {
        "prompt": "GPIO drives resistor and LED node",
        "selected_components": [
            {"ref_des": "U1", "id_str": "MCU:Test", "category": "MCU", "description": "MCU"},
            {"ref_des": "R1", "id_str": "Device:R", "category": "RESISTOR", "description": "Resistor"},
            {"ref_des": "D1", "id_str": "Device:LED", "category": "DIODE", "description": "LED"},
        ],
        "pin_matrix": {
            "U1:1": {"name": "GPIO2", "etype": "output", "pin_num": "1", "ref_des": "U1"},
            "R1:1": {"name": "~", "etype": "passive", "pin_num": "1", "ref_des": "R1"},
            "D1:1": {"name": "A", "etype": "passive", "pin_num": "1", "ref_des": "D1"},
        },
    }

    result = netlist_node(state, _cfg())
    assert {tuple(sorted((edge["source"], edge["target"]))) for edge in result["netlist"]} == {
        tuple(sorted(("U1:1", "R1:1"))),
        tuple(sorted(("U1:1", "D1:1"))),
    }
