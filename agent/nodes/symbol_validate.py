from agent.validation import ValidationIssue
from agent.component_knowledge import POWER_PIN_NAMES
from agent.utils import _emit


def symbol_validate_node(state, config):
    issues = []
    pin_matrix = state.get("pin_matrix", {})

    # SYV001: Pin with no name
    for key, pin in pin_matrix.items():
        pname = pin.get("name", "").strip()
        if not pname:
            issues.append(ValidationIssue(
                code="SYV001",
                severity="warning",
                stage="symbol_validate",
                message=f"Pin {key} has no name",
                component=key.split(":")[0],
                pin=key,
            ))

    # SYV002: IC missing VCC/GND pins
    # Detect pins with VCC/VDD/GND-like names to alert when missing
    vcc_found: dict[str, bool] = {}
    gnd_found: dict[str, bool] = {}
    for key, pin in pin_matrix.items():
        ref = key.split(":")[0]
        pname = pin.get("name", "").strip().upper()
        if pname in ("VCC", "VDD", "VUSB", "VBUS", "VIN"):
            vcc_found[ref] = True
        if pname in ("GND", "VSS", "GNDD", "GNDA", "EP", "EPAD"):
            gnd_found[ref] = True

    for ref in set(list(vcc_found.keys()) + list(gnd_found.keys())):
        if ref in vcc_found and ref not in gnd_found:
            issues.append(ValidationIssue(
                code="SYV002",
                severity="warning",
                stage="symbol_validate",
                message=f"Component {ref} has VCC-like pins but no GND-like pin",
                component=ref,
            ))

    # SYV003: Pin with duplicate name within same component
    comp_pins: dict[str, dict[str, list[str]]] = {}
    for key, pin in pin_matrix.items():
        ref = key.split(":")[0]
        pname = pin.get("name", "").strip()
        if pname:
            comp_pins.setdefault(ref, {}).setdefault(pname, []).append(key)
    for ref, names in comp_pins.items():
        for pname, keys in names.items():
            if len(keys) > 1:
                issues.append(ValidationIssue(
                    code="SYV003",
                    severity="info",
                    stage="symbol_validate",
                    message=f"Component {ref} has multiple pins named '{pname}': {', '.join(keys)}",
                    component=ref,
                ))

    # SYV004: Pin at origin (0,0) — possible unplaced symbol
    for key, pin in pin_matrix.items():
        x = pin.get("x", 0)
        y = pin.get("y", 0)
        if abs(x) < 0.001 and abs(y) < 0.001:
            issues.append(ValidationIssue(
                code="SYV004",
                severity="warning",
                stage="symbol_validate",
                message=f"Pin {key} is at origin (0,0) — symbol may be unplaced",
                component=key.split(":")[0],
                pin=key,
            ))

    # SYV005: Pin alias not matching any known pattern
    for key, pin in pin_matrix.items():
        pname = pin.get("name", "").strip().upper()
        if pname and pname != "~" and len(pname) > 20:
            issues.append(ValidationIssue(
                code="SYV005",
                severity="info",
                stage="symbol_validate",
                message=f"Pin {key} has unusually long name '{pin['name']}'",
                component=key.split(":")[0],
                pin=key,
            ))

    for iss in issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    return {"_validation_issues": state.get("_validation_issues", []) + [i.to_dict() for i in issues]}
