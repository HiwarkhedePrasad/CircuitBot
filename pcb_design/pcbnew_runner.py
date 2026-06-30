"""Orchestrates PCB generation via KiCad's pcbnew in an isolated subprocess.

Runs inside the project's virtualenv. Pre-resolves footprint paths and
serialises all data to JSON before handing off to pcbnew_worker.py
(which runs under KiCad's bundled Python and must not import project code).
"""

import json
import os
import subprocess
import sys

KICAD_PYTHON = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Programs", "KiCad", "10.0", "bin", "python.exe",
)
_WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "pcbnew_worker.py")


def _resolve_footprint_path(fp_str: str) -> str:
    """Resolve a footprint string like 'Package_SOIC:SOIC-8' to an absolute
    .kicad_mod file path using the project's KiCad footprint library."""
    if not fp_str:
        return ""
    from kicad_rag.constants import FOOTPRINTS_ROOT
    cat, _, name = fp_str.partition(":")
    path = FOOTPRINTS_ROOT / f"{cat}.pretty" / f"{name}.kicad_mod"
    return str(path.resolve()) if path.is_file() else ""


def build_board_via_subprocess(board_model_dict: dict,
                                netlist: list) -> dict:
    """Send board model + netlist to the KiCad Python subprocess for PCB
    generation. Returns dict with keys: status, kicad_pcb (str), traces, vias.
    """
    if not os.path.isfile(KICAD_PYTHON):
        raise FileNotFoundError(
            f"KiCad Python not found at {KICAD_PYTHON}. "
            "Install KiCad 10.0 to enable PCB generation."
        )
    if not os.path.isfile(_WORKER_SCRIPT):
        raise FileNotFoundError(f"Worker script not found at {_WORKER_SCRIPT}")

    # Pre-resolve footprint paths while we have access to project imports
    resolved_comps = []
    for comp in board_model_dict.get("components", []):
        fp_str = comp.get("footprint", "")
        resolved_comps.append({
            **comp,
            "footprint_path": _resolve_footprint_path(fp_str),
        })

    payload = json.dumps({
        "model": {**board_model_dict, "components": resolved_comps},
        "netlist": netlist,
    })

    result = subprocess.run(
        [KICAD_PYTHON, _WORKER_SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"pcbnew worker failed (exit {result.returncode}):\n"
            f"{result.stderr}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"pcbnew worker returned invalid JSON: {e}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )
