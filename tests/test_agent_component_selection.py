from agent.nodes.analyze import _apply_user_part_intent
from agent.nodes.select import (
    _filter_candidates_by_expected_type,
    _normalize_part_family,
    select_node,
)
from agent.nodes.validate import _check_prompt_integrity
from agent.nodes.validate import validate_node
from agent.nodes.validate_repair import _filter_repair_candidates


def _cfg():
    return {"configurable": {"emit": None}}


def test_analyze_preserves_user_named_sensor_part():
    analysis = [{
        "subsystem": "Sensor",
        "function": "Temperature sensing",
        "bus": "I2C",
        "example_components": ["TMP117", "BME280"],
    }]
    updated = _apply_user_part_intent(analysis, "Use a DS18B20 temperature sensor with MCU")
    assert updated[0]["example_components"][0] == "DS18B20"


def test_type_filter_blocks_led_driver_for_resistor_request():
    sub = {
        "subsystem": "Passive Components",
        "function": "330 ohm resistor for LED current limit",
        "example_components": ["330 ohm resistor"],
    }
    candidates = [
        {"id_str": "Driver_LED:MP3362GJ", "category": "Driver_LED", "text": "LED driver"},
        {"id_str": "Device:R_Small", "category": "RESISTOR", "text": "Small resistor"},
    ]
    filtered = _filter_candidates_by_expected_type(sub, candidates, "add a 330 ohm resistor")
    assert [c["id_str"] for c in filtered] == ["Device:R_Small"]


def test_prompt_integrity_accepts_stm32_family_match():
    errors = _check_prompt_integrity(
        "Use an STM32 microcontroller",
        [{
            "ref_des": "U1",
            "id_str": "MCU_ST_STM32:STM32F103C8Tx",
            "category": "MCU",
            "description": "STM32 MCU",
        }],
    )
    assert errors == []


def test_repair_filter_excludes_rejected_family_variants():
    candidates = [
        {"id_str": "Interface_USB:CP2102N", "category": "Interface_USB", "text": "USB UART bridge"},
        {"id_str": "Interface_USB:CP2102C", "category": "Interface_USB", "text": "USB UART bridge alt"},
        {"id_str": "Interface_USB:CH340C", "category": "Interface_USB", "text": "USB UART bridge"},
    ]
    filtered = _filter_repair_candidates(
        "USB Interface",
        "Need a USB UART bridge",
        candidates,
        rejected_ids=set(),
        rejected_families={"INTERFACE_USB:CP2102"},
    )
    assert [c["id_str"] for c in filtered] == ["Interface_USB:CH340C"]


def test_part_family_normalization_groups_cp2102_variants():
    assert _normalize_part_family("Interface_USB:CP2102N-A02-GQFN20") == "INTERFACE_USB:CP2102"
    assert _normalize_part_family("Interface_USB:CP2102C") == "INTERFACE_USB:CP2102"


def test_validate_reports_missing_library_after_max_retries(monkeypatch):
    monkeypatch.setattr(
        "agent.nodes.validate._call_llm",
        lambda *args, **kwargs: '{"valid": false, "issues": [{"id_str": "Regulator_Switching:BadBuck", "severity": "error", "message": "Invalid buck regulator", "suggestion": "Select a compatible buck regulator"}]}',
    )
    result = validate_node(
        {
            "prompt": "12V to 3.3V buck converter with STM32",
            "analysis": [{"subsystem": "Power Regulation", "function": "12V to 3.3V buck converter"}],
            "research_results": [],
            "selected_components": [{
                "id_str": "Regulator_Switching:BadBuck",
                "ref_des": "U1",
                "category": "Regulator_Switching",
                "description": "bad buck",
                "datasheet_text": "",
            }],
            "retry_count": 3,
            "repair_failures": ["Power Regulation:Regulator_Switching:BadBuck"],
        },
        {"configurable": {"emit": None}},
    )
    assert "No compatible component found in the available library" in result["_validation_error_detail"]


def test_select_does_not_carry_forward_stale_primary_component(monkeypatch):
    monkeypatch.setattr(
        "agent.nodes.select.rank_candidates",
        lambda *args, **kwargs: [{
            "id_str": "MCU_ST_STM32:STM32F103C8Tx",
            "category": "MCU",
            "text": "STM32 MCU",
            "footprint": "",
            "pads": [],
            "score": 5.0,
            "justification": "better match",
        }],
    )
    monkeypatch.setattr("agent.nodes.select.fetch_footprint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("agent.nodes.select.get_supporting_components", lambda *_args, **_kwargs: [])

    state = {
        "prompt": "Use an STM32 microcontroller",
        "retry_count": 0,
        "research_results": [{
            "subsystem": "MCU",
            "function": "Main controller",
            "results": [{
                "id_str": "MCU_ST_STM32:STM32F103C8Tx",
                "category": "MCU",
                "text": "STM32 MCU",
                "footprint": "",
                "pads": [],
            }],
        }],
        "selected_components": [{
            "id_str": "MCU_Module:ESP32-WROOM-32",
            "ref_des": "U1",
            "category": "MCU",
            "description": "stale previous pick",
            "subsystem": "MCU",
            "justification": "",
            "datasheet_text": "",
        }],
    }

    result = select_node(state, _cfg())
    ids = [c["id_str"] for c in result["selected_components"]]
    assert ids == ["MCU_ST_STM32:STM32F103C8Tx"]


def test_select_keeps_validator_added_support_component(monkeypatch):
    monkeypatch.setattr(
        "agent.nodes.select.rank_candidates",
        lambda *args, **kwargs: [{
            "id_str": "MCU_ST_STM32:STM32F103C8Tx",
            "category": "MCU",
            "text": "STM32 MCU",
            "footprint": "",
            "pads": [],
            "score": 5.0,
            "justification": "better match",
        }],
    )
    monkeypatch.setattr("agent.nodes.select.fetch_footprint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("agent.nodes.select.get_supporting_components", lambda *_args, **_kwargs: [])

    state = {
        "prompt": "Use an STM32 microcontroller with USB",
        "retry_count": 0,
        "research_results": [{
            "subsystem": "MCU",
            "function": "Main controller",
            "results": [{
                "id_str": "MCU_ST_STM32:STM32F103C8Tx",
                "category": "MCU",
                "text": "STM32 MCU",
                "footprint": "",
                "pads": [],
            }],
        }],
        "selected_components": [{
            "id_str": "Interface_USB:CP2102N",
            "ref_des": "U2",
            "category": "Interface_USB",
            "description": "USB UART bridge",
            "subsystem": "MCU",
            "justification": "Auto-added by validator: USB bridge required",
            "datasheet_text": "",
        }],
    }

    result = select_node(state, _cfg())
    ids = {c["id_str"] for c in result["selected_components"]}
    assert ids == {"MCU_ST_STM32:STM32F103C8Tx", "Interface_USB:CP2102N"}


def test_select_dedupes_duplicate_support_injection(monkeypatch):
    monkeypatch.setattr(
        "agent.nodes.select.rank_candidates",
        lambda *args, **kwargs: [{
            "id_str": "MCU_ST_STM32:STM32F103C8Tx",
            "category": "MCU",
            "text": "STM32 MCU",
            "footprint": "",
            "pads": [],
            "score": 5.0,
            "justification": "better match",
        }],
    )
    monkeypatch.setattr("agent.nodes.select.fetch_footprint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "agent.nodes.select.get_supporting_components",
        lambda *_args, **_kwargs: [
            {
                "search_query": "0.1uF capacitor",
                "preferred_id_str": "Device:C_Small",
                "library_filter": "Device",
                "ref_des_prefix": "C",
                "description": "Decoupling capacitor",
            },
            {
                "search_query": "0.1uF capacitor",
                "preferred_id_str": "Device:C_Small",
                "library_filter": "Device",
                "ref_des_prefix": "C",
                "description": "Decoupling capacitor",
            },
        ],
    )
    monkeypatch.setattr(
        "agent.nodes.select.search_components",
        lambda *args, **kwargs: [{
            "id_str": "Device:C_Small",
            "category": "Device",
            "text": "Capacitor",
            "footprint": "",
            "pads": [],
        }],
    )

    state = {
        "prompt": "Use a microcontroller with decoupling",
        "retry_count": 0,
        "research_results": [{
            "subsystem": "MCU",
            "function": "Main controller",
            "results": [{
                "id_str": "MCU_ST_STM32:STM32F103C8Tx",
                "category": "MCU",
                "text": "STM32 MCU",
                "footprint": "",
                "pads": [],
            }],
        }],
        "selected_components": [],
    }

    result = select_node(state, _cfg())
    ids = [c["id_str"] for c in result["selected_components"]]
    assert ids.count("Device:C_Small") == 1
