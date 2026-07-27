"""Engineering Intelligence Runtime.

Unified facade for all intelligence subsystems. Every capability receives
one Runtime instance at construction time — never constructs its own services.

Usage::

    from agent.runtime import EngineeringIntelligenceRuntime, get_runtime

    # Direct construction
    runtime = EngineeringIntelligenceRuntime(design_id, revision, event_store, projections)

    # From agent node config (preferred)
    runtime = get_runtime(config)
"""

from agent.runtime.runtime import EngineeringIntelligenceRuntime


def get_runtime(config: dict) -> EngineeringIntelligenceRuntime | None:
    """Get the Runtime instance from agent config.

    Returns None if RUNTIME_ENABLED is False or Runtime was not initialized.
    Nodes should use this to access shared intelligence services.
    """
    return config.get("configurable", {}).get("runtime")


__all__ = ["EngineeringIntelligenceRuntime", "get_runtime"]
