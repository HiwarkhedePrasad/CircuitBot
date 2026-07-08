"""
Backward-compatibility re-export layer for agent/utils.py.

All public symbols that were historically defined in this module are
re-exported here. New code should import directly from the split modules:

  agent/emit_utils.py     — SSE event emission, sanitization
  agent/llm_utils.py      — LLM retry logic, rate limiting
  agent/route_utils.py    — Graph routing helpers
  agent/sexpr_utils.py    — S-expression parsing, pin extraction

The symbols below (AgentLLMError, net-name constants, pin matching, etc.)
are defined in this file because they are not part of any split module.
"""
import re

from agent.emit_utils import (
    _safe_print, _emit, emit_assistant_message, emit_tool_event,
    _emit_activity, _clean_json, _sanitize_data,
)
from agent.llm_utils import (
    _rate_limit, _record_call, _is_connection_error,
    _retry_llm_call, _call_llm, _call_llm_with_tools,
)
from agent.route_utils import (
    _check_stage_contract, _stage_result,
    _route_after_validate, _route_after_validation_help,
    _route_after_pcb_approval, _route_after_erc,
)
from agent.sexpr_utils import (
    _parse_sexpr_to_ops, _extract_pins_from_ops, _get_attr,
)

# ── Re-export constants from llm_utils ────────────────────────────────
from agent.llm_utils import MAX_LLM_RETRIES, MAX_VALIDATION_RETRIES, MAX_BATCH_PINS

# ── Unique constants (not in any split module) ────────────────────────

GND_NET_NAMES = {"GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EP", "EPAD", "0V", "SHIELD"}
POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "V+", "V-", "VSYS", "VOUT", "VEE", "PWR"}
POWER_ETYPES = {"power_in", "power_out"}

_PART_TOKEN_RE = re.compile(r'\b[A-Za-z]{2,}[0-9][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\b')
_NON_PART_WORDS = {"USB2", "USB3", "RS232", "RS485", "CAT5", "CAT6", "WIFI6", "IEEE802"}


class AgentLLMError(Exception):
    """Raised when an LLM call fails after exhausting all retries."""


# ── Unique helper functions (not in any split module) ─────────────────


def _is_gnd_net(name: str) -> bool:
    return name.upper().lstrip('+') in GND_NET_NAMES


def _is_power_net(name: str) -> bool:
    n = name.upper().lstrip('+')
    if n in POWER_NET_NAMES:
        return True
    if re.match(r'^\d+V\d*$', n) or re.match(r'^V\d+$', n):
        return True
    return False


def _extract_part_numbers(prompt: str) -> list:
    out, seen = [], set()
    for m in _PART_TOKEN_RE.finditer(prompt):
        tok = m.group(0)
        up = tok.upper()
        if len(up) < 5 or up in _NON_PART_WORDS or up in seen:
            continue
        if re.fullmatch(r'[A-Z]{0,2}\d+(V\d*|UF|NF|PF|UH|MH|K|M|MA|A|W|OHM|KOHM|MHZ|KHZ|HZ|BIT|MM)', up):
            continue
        seen.add(up)
        out.append(tok)
    return out


def _is_passive(id_str: str, category: str) -> bool:
    cat = (category or '').upper()
    return id_str.startswith('Device:') or cat in ('DEVICE',)


def _ref_prefix_for(id_str: str, category: str) -> str:
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
    if 'PWR_FLAG' in hints or id_str == 'power:PWR_FLAG':
        return '#FLG'
    return 'U'


PIN_ALIASES = {
    "SDA": {"SDA", "SDI", "SDIO", "I2C0_SDA", "I2C1_SDA", "I2C_DATA", "I2CDAT"},
    "SCL": {"SCL", "SCK", "I2C0_SCL", "I2C1_SCL", "I2C_CLK", "I2CCLK"},
    "TX": {"TXD", "TX", "TXD0", "TXD1", "UART_TX", "UART0_TX", "UART1_TX", "TXD_0", "TXD_1", "TX0", "TX1"},
    "RX": {"RXD", "RX", "RXD0", "RXD1", "UART_RX", "UART0_RX", "UART1_RX", "RXD_0", "RXD_1", "RX0", "RX1"},
    "MOSI": {"MOSI", "SPI_MOSI", "SPI0_MOSI", "SPI1_MOSI", "SI", "SDO"},
    "MISO": {"MISO", "SPI_MISO", "SPI0_MISO", "SPI1_MISO", "SO", "SDI"},
    "SCK": {"SCK", "SPI_SCK", "SPI0_SCK", "SPI1_SCK", "SPI_CLK", "SPICLK"},
    "CS": {"CS", "SS", "NSS", "SPI_CS", "SPI0_CS", "SPI1_CS", "CHIP_SELECT", "CE"},
    "XTAL1": {"XTAL1", "XTAL_IN", "OSC_IN", "OSCI", "OSC0_IN", "OSC1_IN", "XIN"},
    "XTAL2": {"XTAL2", "XTAL_OUT", "OSC_OUT", "OSCO", "OSC0_OUT", "OSC1_OUT", "XOUT"},
    "RESET": {"RST", "RESET", "NRST", "N_RST", "nRST", "NRESET", "N_RESET", "RST_N", "RSTB"},
    "EN": {"EN", "ENABLE", "CHIP_EN", "CEN", "CE_N", "SHDN", "SHDN_N", "ON_OFF"},
    "INT": {"INT", "IRQ", "NINT", "N_IRQ", "nINT", "INT_N", "IRQ_N"},
    "STAT": {"STAT", "STATE", "STATUS", "CHG_STAT", "CHG_STATE", "FAULT", "PG", "POWER_GOOD"},
}

COMPLEMENTARY_PAIRS = [
    ("TX", "RX"),
    ("RX", "TX"),
    ("MOSI", "MISO"),
    ("MISO", "MOSI"),
]


def _canonical_signal_name(name: str):
    upper = name.upper().strip()
    for canon, aliases in PIN_ALIASES.items():
        if upper in aliases:
            return canon
    return None


def _resolve_hallucinated_pin(bad_key: str, pin_matrix: dict, assigned: set) -> str | None:
    ref = bad_key.split(':')[0]
    hint = bad_key.split(':')[1] if ':' in bad_key else ''
    candidates = []
    for key, pin in pin_matrix.items():
        if key.split(':')[0] == ref and key not in assigned:
            candidates.append((key, pin))
    if not hint:
        return None
    hint_upper = hint.upper()
    for key, pin in candidates:
        if pin.get('pin_num', '') == hint:
            return key
    for key, pin in candidates:
        pname = pin.get('name', '').upper()
        if pname == hint_upper:
            return key
    if hint.isdigit():
        for key, pin in candidates:
            pname = pin.get('name', '').upper()
            if pname.endswith(hint) and not pname.startswith(('1', '2', '3', '4', '5', '6', '7', '8', '9')):
                return key
            if pname == f"IO{hint}" or pname == f"PIN{hint}" or pname == f"GPIO{hint}":
                return key
    hint_canon = _canonical_signal_name(hint)
    if hint_canon:
        for key, pin in candidates:
            pname = pin.get('name', '').upper()
            if _canonical_signal_name(pname) == hint_canon:
                return key
    if hint_upper in PIN_ALIASES:
        for key, pin in candidates:
            etype = pin.get('etype', '')
            pname = pin.get('name', '').upper()
            if etype in ('bidirectional', 'input', 'output') and pname.startswith('IO'):
                return key
    return None


def _merge_net(nets: list, name: str, new_pins: list):
    for n in nets:
        if n["net"].upper() == name.upper():
            n["pins"].extend(p for p in new_pins if p not in n["pins"])
            return
    nets.append({"net": name, "pins": list(new_pins)})


def _make_signal_batches(pin_keys: list, max_pins: int = MAX_BATCH_PINS) -> list:
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


def _create_pwr_flag_component(net_name: str, index: int, flag_ops: list,
                                flag_pin_raw: dict, justification: str) -> dict:
    """Create a PWR_FLAG component and return state updates.

    Returns a dict with keys: component, comp_op, pin_entry, power_pin_entry.
    """
    ref = f"#FLG{index:02d}"
    component = {
        "id_str": "power:PWR_FLAG",
        "ref_des": ref,
        "category": "Power_Management",
        "description": f"Power flag for {net_name}",
        "footprint": "", "pads": [],
        "justification": justification,
        "datasheet_text": "",
    }
    if flag_pin_raw:
        pk = list(flag_pin_raw.keys())[0]
        pv = flag_pin_raw[pk]
        adj_key = f"{ref}:{pv['pin_num']}"
        adj_pv = dict(pv)
        adj_pv["ref_des"] = ref
        pin_entry = (adj_key, adj_pv)
        power_pin_entry = {"pin": adj_key, "net": net_name}
    else:
        pin_entry = (f"{ref}:1", {
            "x": 0, "y": 0, "name": "",
            "num": "1", "pin_num": "1",
            "ref_des": ref, "angle": 90, "etype": "power_out",
        })
        power_pin_entry = {"pin": f"{ref}:1", "net": net_name}
    return {
        "ref": ref,
        "component": component,
        "comp_op": flag_ops,
        "pin_entry": pin_entry,
        "power_pin_entry": power_pin_entry,
    }


def _generate_nets_fallback(pin_matrix: dict,
                            comps: list | None = None,
                            existing_nets: list | None = None) -> list:
    by_name = {}
    tilde_by_ref: dict[str, list[str]] = {}
    for key, pin in pin_matrix.items():
        name = pin.get("name", "").strip().upper()
        if not name or name in ("NC", ""):
            continue
        if name == "~":
            ref = key.split(":")[0]
            tilde_by_ref.setdefault(ref, []).append(key)
            continue
        by_name.setdefault(name, []).append(key)
    nets: list[dict] = []
    gnd_pins = []
    for name in list(by_name.keys()):
        if _is_gnd_net(name):
            gnd_pins.extend(by_name.pop(name))
    if gnd_pins:
        nets.append({"net": "GND", "pins": gnd_pins})
    power_groups = {}
    for name in list(by_name.keys()):
        if _is_power_net(name):
            canon = name.lstrip('+')
            power_groups.setdefault(canon, []).extend(by_name.pop(name))
    for canon, pins_list in power_groups.items():
        nets.append({"net": canon, "pins": pins_list})
    if comps and existing_nets and tilde_by_ref:
        comp_for = {c["ref_des"]: c.get("for_component", "") for c in comps}
        parent_power: dict[str, str] = {}
        for net in existing_nets:
            if not isinstance(net, dict):
                continue
            net_name = net.get("net", "")
            if _is_gnd_net(net_name):
                continue
            for key in net.get("pins", []):
                ref = key.split(":")[0]
                if any(pc == ref for pc in comp_for.values()):
                    parent_power[ref] = net_name
        for ref, keys in tilde_by_ref.items():
            parent_ref = comp_for.get(ref, "")
            if not parent_ref or len(keys) < 1:
                continue
            power_net = parent_power.get(parent_ref, "3V3")
            if len(keys) >= 2:
                nets.append({"net": power_net, "pins": [keys[0]]})
                nets.append({"net": "GND", "pins": keys[1:]})
            else:
                nets.append({"net": "GND", "pins": keys})
    signal_groups: dict[str, list[str]] = {}
    unmatched: list[tuple[str, list[str]]] = []
    for name, keys in by_name.items():
        canon = _canonical_signal_name(name)
        if canon:
            signal_groups.setdefault(canon, []).extend(keys)
        else:
            unmatched.append((name, keys))
    for canon, pins in signal_groups.items():
        if len(pins) >= 1:
            nets.append({"net": canon.upper(), "pins": pins})
    still_unmatched: list[tuple[str, list[str]]] = []
    for name, keys in unmatched:
        canon = _canonical_signal_name(name)
        existing_names = {n["net"] for n in nets}
        if canon and canon.upper() in existing_names:
            for n in nets:
                if n["net"] == canon.upper():
                    n["pins"].extend(keys)
                    break
        else:
            still_unmatched.append((name, keys))
    unmatched = still_unmatched
    for name, keys in unmatched:
        if len(keys) >= 2:
            nets.append({"net": name, "pins": keys})
    leftover_final = {name: keys for name, keys in unmatched if len(keys) == 1}
    for name, keys in leftover_final.items():
        nets.append({"net": name, "pins": keys})
    return nets
