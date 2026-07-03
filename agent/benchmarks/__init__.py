"""Benchmark circuit definitions and runner.

Each module in this package exports a ``load()`` function that returns::

    {
        "name": str,
        "description": str,
        "components": [  # same format as ComponentSelection
            {"ref_des": str, "id_str": str, "category": str, "description": str,
             "for_component": str, "ops": list, "footprint": str},
        ],
        "netlist": [{"source": str, "target": str, "net": str}],
        "pin_matrix": {pin_key: {"x": float, "y": float, "angle": float}},
    }

The runner creates a BackendLayoutEngine, loads components, runs placement
and routing, then records metrics against this circuit.
"""

import importlib
import pkgutil
import time
from pathlib import Path


def _rect_ops(w: float = 10.0, h: float = 10.0) -> list:
    """Rectangle ops centered at (0,0)."""
    hw, hh = w / 2, h / 2
    return [["rectangle", ["start", -hw, -hh], ["end", hw, hh]]]


def _discover_circuits():
    """Yield all (name, load_fn) pairs from benchmarks/ modules."""
    package = Path(__file__).resolve().parent
    for importer, modname, ispkg in pkgutil.iter_modules([str(package)]):
        if modname.startswith("_"):
            continue
        mod = importlib.import_module(f".{modname}", __package__)
        if hasattr(mod, "load"):
            yield modname, mod.load
