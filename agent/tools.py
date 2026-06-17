import json
import math
import os
from pathlib import Path
from dotenv import load_dotenv
from kicad_rag.client import KicadRAG

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

rag = KicadRAG(mode="bm25")

# ── Tool Registry ────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, dict] = {}

def register_tool(fn):
    """Decorator: registers a tool with its docstring parsed as the schema."""
    TOOL_REGISTRY[fn.__name__] = {
        "description": fn.__doc__ or "",
        "fn": fn,
    }
    return fn


@register_tool
def calculate_trace_width(current_a: float, temp_rise_c: float = 10,
                          copper_oz: float = 1, external: bool = True) -> dict:
    """Calculate PCB trace width required for a given current (IPC-2221).
    Args: current_a (A), temp_rise_c (deg C), copper_oz (oz/ft^2), external (bool).
    Returns: {width_mm, required_area_mils2, temp_rise_c, current_a, copper_oz}"""
    copper_thick_mils = copper_oz * 1.37
    k = 0.048 if external else 0.024
    area_mils2 = (current_a / (k * (temp_rise_c ** 0.44))) ** (1 / 0.725)
    width_mils = area_mils2 / copper_thick_mils
    width_mm = width_mils * 0.0254
    return {
        "width_mm": round(width_mm, 3),
        "width_mils": round(width_mils, 2),
        "required_area_mils2": round(area_mils2, 2),
        "temp_rise_c": temp_rise_c,
        "current_a": current_a,
        "copper_oz": copper_oz,
        "copper_thickness_mils": round(copper_thick_mils, 3),
    }


@register_tool
def calculate_max_current(trace_width_mm: float, temp_rise_c: float = 10,
                          copper_oz: float = 1, external: bool = True) -> dict:
    """Calculate maximum current a trace can carry (IPC-2221).
    Args: trace_width_mm (mm), temp_rise_c (deg C), copper_oz (oz/ft^2), external (bool).
    Returns: {max_current_a, trace_width_mm, temp_rise_c, copper_oz}"""
    copper_thick_mils = copper_oz * 1.37
    width_mils = trace_width_mm / 0.0254
    area_mils2 = width_mils * copper_thick_mils
    k = 0.048 if external else 0.024
    current_a = k * (temp_rise_c ** 0.44) * (area_mils2 ** 0.725)
    return {
        "max_current_a": round(current_a, 3),
        "trace_width_mm": trace_width_mm,
        "trace_width_mils": round(width_mils, 2),
        "temp_rise_c": temp_rise_c,
        "copper_oz": copper_oz,
        "copper_thickness_mils": round(copper_thick_mils, 3),
    }


@register_tool
def calculate_microstrip_impedance(trace_width_mm: float,
                                   dielectric_thickness_mm: float,
                                   er: float = 4.5,
                                   trace_thickness_mm: float = 0.035) -> dict:
    """Calculate characteristic impedance of a microstrip trace.
    Args: trace_width_mm (mm), dielectric_thickness_mm (mm), er (FR4 ~4.5), trace_thickness_mm (mm).
    Returns: {z0_ohm, trace_width_mm, dielectric_thickness_mm, er}"""
    w = trace_width_mm
    h = dielectric_thickness_mm
    t = trace_thickness_mm
    if w <= 0 or h <= 0:
        return {"error": "trace_width and dielectric_thickness must be positive"}
    eff_er = (er + 1) / 2 + (er - 1) / 2 * (1 / math.sqrt(1 + 12 * h / w))
    z0 = 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
    return {
        "z0_ohm": round(z0, 2),
        "epsilon_effective": round(eff_er, 3),
        "trace_width_mm": trace_width_mm,
        "dielectric_thickness_mm": dielectric_thickness_mm,
        "er": er,
    }


@register_tool
def calculate_voltage_drop(current_a: float, trace_length_mm: float,
                           trace_width_mm: float, copper_oz: float = 1) -> dict:
    """Calculate DC voltage drop across a PCB trace.
    Args: current_a (A), trace_length_mm (mm), trace_width_mm (mm), copper_oz (oz/ft^2).
    Returns: {voltage_drop_v, power_loss_w, resistance_ohm, trace_length_mm, current_a}"""
    rho = 1.68e-8
    copper_thick_mm = copper_oz * 0.0348
    cross_section_m2 = (trace_width_mm / 1000) * (copper_thick_mm / 1000)
    if cross_section_m2 <= 0:
        return {"error": "cross-section area must be positive"}
    length_m = trace_length_mm / 1000
    resistance_ohm = rho * length_m / cross_section_m2
    voltage_drop_v = current_a * resistance_ohm
    power_loss_w = voltage_drop_v * current_a
    return {
        "voltage_drop_v": round(voltage_drop_v, 6),
        "power_loss_w": round(power_loss_w, 6),
        "resistance_ohm": round(resistance_ohm, 6),
        "trace_length_mm": trace_length_mm,
        "current_a": current_a,
    }


@register_tool
def calculate_via_current(outer_diameter_mm: float, hole_diameter_mm: float,
                          temp_rise_c: float = 10, copper_oz: float = 1) -> dict:
    """Calculate current capacity of a PCB via.
    Args: outer_diameter_mm (mm), hole_diameter_mm (mm), temp_rise_c (deg C), copper_oz (oz/ft^2).
    Returns: {max_current_a, outer_diameter_mm, hole_diameter_mm, temp_rise_c}"""
    copper_thick_mils = copper_oz * 1.37
    outer_diameter_mils = outer_diameter_mm / 0.0254
    circumference_mils = math.pi * outer_diameter_mils
    cross_section_mils2 = circumference_mils * copper_thick_mils
    k = 0.048
    current_a = k * (temp_rise_c ** 0.44) * (cross_section_mils2 ** 0.725)
    return {
        "max_current_a": round(current_a, 3),
        "outer_diameter_mm": outer_diameter_mm,
        "hole_diameter_mm": hole_diameter_mm,
        "temp_rise_c": temp_rise_c,
        "cross_section_mils2": round(cross_section_mils2, 2),
    }


@register_tool
def search_components(query: str, k: int = 8,
                     library_filter: str | None = None) -> list[dict]:
    results = rag.search(query, k=k)
    if library_filter:
        results = [r for r in results
                   if r.id_str.startswith(library_filter + ":")]
    return [
        {
            "id_str": r.id_str,
            "text": r.text,
            "score": r.score,
            "pins": r.pins,
            "datasheet": r.datasheet,
            "footprint": r.footprint,
            "fp_filters": r.fp_filters,
            "pads": r.pads,
        }
        for r in results
    ]


def fetch_sexpr(id_str: str) -> str:
    return rag.sexpr(id_str)


def fetch_pins(id_str: str) -> list[dict]:
    return rag.pins(id_str)


def fetch_footprint(id_str: str) -> dict | None:
    return rag.footprint(id_str)


def llm_call(system: str, user: str, model: str = "meta/llama-3.3-70b-instruct") -> str:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config import get_llm_client

    client = get_llm_client(temperature=0.1, max_tokens=4096)
    full_response = ""
    for chunk in client.stream([{"role": "system", "content": system}, {"role": "user", "content": user}]):
        full_response += chunk.content
    return full_response.strip()


TOOL_DESCRIPTIONS = """AVAILABLE PCB CALCULATION TOOLS (call by outputting JSON with tool_name and args):

1. calculate_trace_width(current_a, temp_rise_c=10, copper_oz=1, external=true)
   -> Returns required trace width in mm for a given current load.

2. calculate_max_current(trace_width_mm, temp_rise_c=10, copper_oz=1, external=true)
   -> Returns max current a trace can safely carry.

3. calculate_microstrip_impedance(trace_width_mm, dielectric_thickness_mm, er=4.5, trace_thickness_mm=0.035)
   -> Returns characteristic impedance Z0 for a microstrip trace.

4. calculate_voltage_drop(current_a, trace_length_mm, trace_width_mm, copper_oz=1)
   -> Returns DC voltage drop and power loss across a trace.

5. calculate_via_current(outer_diameter_mm, hole_diameter_mm, temp_rise_c=10, copper_oz=1)
   -> Returns max current capacity of a via.

To use a tool, include this in your JSON output:
  {"_tool": "calculate_trace_width", "args": {"current_a": 1.5, "temp_rise_c": 20}}
"""


def execute_tool(tool_name: str, **kwargs) -> dict:
    """Execute a registered tool by name with given kwargs."""
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = entry["fn"](**kwargs)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {e}"}