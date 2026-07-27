"""Feature flags for the Engineering Intelligence Runtime.

Every major subsystem is behind a flag. Default OFF until validated.
Rollback = set flag to False (via env var or code).

Usage::

    from agent.feature_flags import is_enabled
    if is_enabled("SYNTHESIS_GRAPH_EARLY"):
        ...
"""

import os

# Default flags — all OFF for safe rollout
DEFAULTS = {
    "SYNTHESIS_GRAPH_EARLY": False,      # M1a: Build graph in dispatch
    "KNOWLEDGE_EXTRACTION_LIVE": False,   # M1a: Live extract_knowledge()
    "VALIDATION_DETERMINISTIC": False,    # M1b: Wire validate_circuit()
    "BUS_WARNINGS_SURFACED": False,       # M1b: Surface bus_checker warnings
    "MOTIF_DETECTION": False,             # M1c: Wire detect_motifs()
    "PREFERENCES_PERSIST": False,         # M1c: Persist user preferences
    "RUNTIME_ENABLED": True,              # M2: EngineeringIntelligenceRuntime
    "OBJECT_AI_MENU": False,              # M3: Right-click context menu
    "CONSTRAINT_SOLVER": False,           # M4: Declarative constraints
    "DESIGN_TWIN": False,                 # M4: Live component state
    "PLANNER_ENABLED": False,             # M5: Cross-capability planning
    "CRITIC_AGENT": False,                # M6: Background design critic
    "SIMULATION_BROKER": False,           # M7: Simulation dispatch
    "DESIGN_EVOLUTION": False,            # M8: Evolution tree
    "CANVAS_AWARE_COPILOT": False,        # Canvas sync + design-aware copilot
}

# Override from environment variables: FEATURE_<FLAG_NAME>=true
_FLAGS: dict[str, bool] = {}


def _load_flags() -> dict[str, bool]:
    if _FLAGS:
        return _FLAGS
    for key, default in DEFAULTS.items():
        env_val = os.environ.get(f"FEATURE_{key}")
        if env_val is not None:
            _FLAGS[key] = env_val.lower() in ("true", "1", "yes")
        else:
            _FLAGS[key] = default
    return _FLAGS


def is_enabled(flag: str) -> bool:
    """Check if a feature flag is enabled."""
    flags = _load_flags()
    return flags.get(flag, False)


def set_flag(flag: str, value: bool) -> None:
    """Override a feature flag at runtime (for testing/emergency rollback)."""
    _FLAGS[flag] = value


def status() -> dict[str, bool]:
    """Return all flags and their current state."""
    return dict(_load_flags())
