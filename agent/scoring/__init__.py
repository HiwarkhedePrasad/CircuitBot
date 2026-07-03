"""Scorer — aggregates self-registering placement and routing metrics.

Usage::

    from agent.scoring import Scorer

    scorer = Scorer()

    # Placement score (no routing needed)
    pscore = scorer.score_placement(components, netlist, pin_matrix)

    # Routing score (after wires are placed)
    rscore = scorer.score_routing(components, placements, wires, netlist)

    # Combined
    total = pscore.total + rscore.total
"""
from __future__ import annotations

from typing import Any

from agent.scoring import weights as wmod
from agent.scoring.metrics import (
    get_placement_metrics,
    get_routing_metrics,
)


class ScoreReport:
    """Container for a scored evaluation."""

    def __init__(self, metric_scores: dict[str, float], total: float):
        self.metric_scores = dict(metric_scores)
        self.total = total

    def __repr__(self) -> str:
        parts = [f"total={self.total:.1f}"]
        for name, score in sorted(self.metric_scores.items()):
            parts.append(f"{name}={score:.1f}")
        return f"ScoreReport({', '.join(parts)})"

    def breakdown(self) -> dict[str, float]:
        return dict(self.metric_scores)


class Scorer:
    """Aggregates all registered metrics into weighted scores.

    Args:
        placement_weights: Override defaults from ``weights.PLACEMENT_WEIGHTS``.
        routing_weights: Override defaults from ``weights.ROUTING_WEIGHTS``.
    """

    def __init__(
        self,
        placement_weights: dict[str, Any] | None = None,
        routing_weights: dict[str, Any] | None = None,
    ):
        self._placement_weight_map = wmod.merge_weights(wmod.PLACEMENT_WEIGHTS, placement_weights)
        self._routing_weight_map = wmod.merge_weights(wmod.ROUTING_WEIGHTS, routing_weights)

        # Force-import all metric modules so decorators fire
        import agent.scoring.metrics as _m  # noqa: F401
        import importlib
        import pkgutil
        for _importer, modname, _ispkg in pkgutil.iter_modules(_m.__path__):
            importlib.import_module(f"agent.scoring.metrics.{modname}")

        self._placement_metrics: dict[str, type] = {}
        self._routing_metrics: dict[str, type] = {}

        self._discover_metrics()

        # Fail loudly if a weighted metric is missing its implementation
        for weight_name in self._placement_weight_map:
            if weight_name not in self._placement_metrics:
                raise KeyError(
                    f"Placement metric '{weight_name}' has weight "
                    f"{self._placement_weight_map[weight_name]} but no registered metric class. "
                    f"Available: {sorted(self._placement_metrics)}"
                )
        for weight_name in self._routing_weight_map:
            if weight_name not in self._routing_metrics:
                raise KeyError(
                    f"Routing metric '{weight_name}' has weight "
                    f"{self._routing_weight_map[weight_name]} but no registered metric class. "
                    f"Available: {sorted(self._routing_metrics)}"
                )

    def _discover_metrics(self):
        """Import all metric modules so their decorators register them."""
        self._placement_metrics = dict(get_placement_metrics())
        self._routing_metrics = dict(get_routing_metrics())

    def score_placement(self, components: list, netlist: list, pin_matrix: dict) -> ScoreReport:
        """Evaluate placement quality without routing.

        Only uses metrics decorated with ``@placement_metric``.
        """
        scores: dict[str, float] = {}
        total = 0.0
        layout = {}

        for name, cls in self._placement_metrics.items():
            if name not in self._placement_weight_map:
                continue
            try:
                value = cls().evaluate(layout, components, netlist, pin_matrix)
            except Exception:
                value = 0.0
            weighted = value * self._placement_weight_map.get(name, 1.0)
            scores[name] = weighted
            total += weighted

        return ScoreReport(scores, total)

    def score_routing(
        self, components: list, placements: list, wires: list, netlist: list,
    ) -> ScoreReport:
        """Evaluate routing quality.

        Only uses metrics decorated with ``@routing_metric``.
        """
        scores: dict[str, float] = {}
        total = 0.0

        for name, cls in self._routing_metrics.items():
            if name not in self._routing_weight_map:
                continue
            try:
                value = cls().evaluate(components, placements, wires, netlist)
            except Exception:
                value = 0.0
            weighted = value * self._routing_weight_map.get(name, 1.0)
            scores[name] = weighted
            total += weighted

        return ScoreReport(scores, total)

    def score_total(
        self, components: list, placements: list, wires: list,
        netlist: list, pin_matrix: dict,
    ) -> ScoreReport:
        """Combined placement + routing score."""
        ps = self.score_placement(components, netlist, pin_matrix)
        rs = self.score_routing(components, placements, wires, netlist)

        combined = {}
        combined.update(ps.metric_scores)
        combined.update(rs.metric_scores)
        return ScoreReport(combined, ps.total + rs.total)
