from agent.nodes.analyze import _apply_user_part_intent
from agent.nodes.select import (
    _filter_candidates_by_expected_type,
    _normalize_part_family,
)
from agent.nodes.validate import _check_prompt_integrity
from agent.nodes.validate import validate_node
from agent.nodes.validate_repair import _filter_repair_candidates


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
