"""Self-registering metric system.

Usage::

    from agent.scoring.metrics import placement_metric

    @placement_metric
    class MyMetric:
        name = "my_metric"

        def evaluate(self, layout, components, netlist, pin_matrix) -> float:
            ...
            return score  # lower is better
"""

from typing import Protocol, runtime_checkable


# ── Registries ─────────────────────────────────────────────────────────────

_PLACEMENT_METRICS: dict[str, type] = {}
_ROUTING_METRICS: dict[str, type] = {}


def placement_metric(cls):
    """Decorate a class to register it as a placement metric."""
    name = getattr(cls, "name", cls.__name__)
    _PLACEMENT_METRICS[name] = cls
    cls._is_placement_metric = True
    return cls


def routing_metric(cls):
    """Decorate a class to register it as a routing metric."""
    name = getattr(cls, "name", cls.__name__)
    _ROUTING_METRICS[name] = cls
    cls._is_routing_metric = True
    return cls


def get_placement_metrics() -> dict[str, type]:
    """Return dict of registered placement metric classes."""
    return dict(_PLACEMENT_METRICS)


def get_routing_metrics() -> dict[str, type]:
    """Return dict of registered routing metric classes."""
    return dict(_ROUTING_METRICS)


@runtime_checkable
class PlacementMetric(Protocol):
    name: str

    def evaluate(self, layout, components, netlist, pin_matrix) -> float:
        ...


@runtime_checkable
class RoutingMetric(Protocol):
    name: str

    def evaluate(self, components, placements, wires, netlist) -> float:
        ...
