"""Simulated-annealing placement optimizer.

Wraps the ``blocks_v2`` base placer and applies SA to refine the
initial placement.  Uses *only* placement metrics (no routing costs)
during iteration, with two phases:

1. **Block-level** — shift / swap whole functional blocks.
2. **Component-level** — nudge / swap / mirror / reparent individual components.

Usage::

    from agent.placement import PlacementEngine
    opt = PlacementEngine.create("sa_optimizer")
    placements = opt.place(components, netlist, pin_matrix)
"""

from __future__ import annotations

import math
import random
import time

from agent.placement.blocks_v2 import (
    BlocksV2Placer, _prepare_components, _snap, _remove_overlaps,
    _get_comp_ref, GRID_SIZE,
)
from agent.placement.annealing import CoolingSchedule
from agent.placement.perturbations import (
    nudge, swap_components, mirror, reparent,
    shift_block, swap_blocks,
)
from agent.scoring import Scorer


# ── Default config ───────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "t_start": 500.0,
    "t_min": 0.1,
    "cooling_rate": 0.92,
    "max_iterations": 800,
    "block_iterations": 200,
    "hill_climb_iters": 50,
    "target_acceptance": 0.44,
    "adapt_strength": 0.1,
    "log_every": 0,
    "weights": None,
}

PERTURBATIONS = [
    ("nudge", nudge, 0.0),
    ("swap", swap_components, 0.0),
    ("mirror", mirror, 0.0),
    ("reparent", reparent, 0.0),
    ("shift_block", shift_block, 0.0),
    ("swap_blocks", swap_blocks, 0.0),
]

BLOCK_PERTURBATIONS = [
    ("shift_block", shift_block, 0.0),
    ("swap_blocks", swap_blocks, 0.0),
]

COMP_PERTURBATIONS = [
    ("nudge", nudge, 0.0),
    ("swap", swap_components, 0.0),
    ("mirror", mirror, 0.0),
    ("reparent", reparent, 0.0),
]


def _normalise_probs(pert_list: list[tuple]) -> list[tuple]:
    total = sum(p[2] for p in pert_list)
    if total <= 0:
        n = len(pert_list)
        return [(name, fn, 1.0 / n) for name, fn, _ in pert_list]
    return [(name, fn, p / total) for name, fn, p in pert_list]


def _pick_perturbation(pert_list: list[tuple]):
    normed = _normalise_probs(pert_list)
    r = random.random()
    cum = 0.0
    for name, fn, prob in normed:
        cum += prob
        if r <= cum:
            return name, fn
    return normed[-1][0], normed[-1][1]


def _rebalance_probs(pert_list: list[tuple],
                     overlap_ratio: float,
                     crossing_ratio: float) -> list[tuple]:
    result = []
    for name, fn, base_prob in pert_list:
        p = base_prob
        if name == "nudge" and overlap_ratio > 0.3:
            p = base_prob * (1 + overlap_ratio)
        elif name == "swap" and crossing_ratio > 0.3:
            p = base_prob * (1 + crossing_ratio)
        elif name == "mirror" and crossing_ratio > 0.3:
            p = base_prob * (1 + crossing_ratio)
        result.append((name, fn, p))
    return result


class SAOptimizer:
    """Simulated-annealing placement optimizer.

    Wraps ``BlocksV2Placer`` for the initial layout.
    """

    def __init__(self, config: dict | None = None):
        self._cfg = {**DEFAULT_CONFIG, **(config or {})}
        self._scorer = Scorer(placement_weights=self._cfg["weights"])
        self._base_placer = BlocksV2Placer()
        self.history: list[dict] = []
        self._metrics = {"overlaps": 0, "crossings": 0}

    def place(self, components: list[dict], netlist: list,
              pin_matrix: dict) -> list:
        components = _prepare_components(components)
        netlist = netlist or []
        pin_matrix = pin_matrix or {}

        # Phase 0: initial placement via blocks_v2
        base_positions = self._base_placer.place(components, netlist, pin_matrix)
        pos_map = {p["ref_des"]: p for p in base_positions}
        for c in components:
            p = pos_map.get(c["ref_des"])
            if p:
                c["x"] = p["x"]
                c["y"] = p["y"]
                c["rotation"] = p.get("rotation", 0.0)

        best_state: list[dict] = []
        best_score = float("inf")

        # Phase 1: block-level SA
        if self._cfg["block_iterations"] > 0:
            schedule = CoolingSchedule(
                t_start=self._cfg["t_start"],
                t_min=self._cfg["t_min"],
                cooling_rate=self._cfg["cooling_rate"],
                target_acceptance=self._cfg["target_acceptance"],
                adaptation_strength=self._cfg["adapt_strength"],
                max_iterations=self._cfg["block_iterations"],
            )
            result = self._run_phase(
                components, netlist, pin_matrix, schedule,
                BLOCK_PERTURBATIONS, self._cfg["block_iterations"],
                "block",
            )
            best_state, best_score = result

        # Phase 2: component-level SA
        schedule = CoolingSchedule(
            t_start=self._cfg["t_start"] * 0.5,
            t_min=self._cfg["t_min"],
            cooling_rate=self._cfg["cooling_rate"],
            target_acceptance=self._cfg["target_acceptance"],
            adaptation_strength=self._cfg["adapt_strength"],
            max_iterations=self._cfg["max_iterations"],
        )
        result = self._run_phase(
            components, netlist, pin_matrix, schedule,
            COMP_PERTURBATIONS, self._cfg["max_iterations"],
            "component",
            start_score=best_score if best_state else None,
        )
        comp_state, comp_score = result
        if comp_score < best_score:
            best_state, best_score = comp_state, comp_score

        # Phase 3: greedy hill-climb
        hill_state, hill_score = self._greedy_hill_climb(
            components, netlist, pin_matrix,
            self._cfg["hill_climb_iters"],
        )
        if hill_score < best_score:
            best_state, best_score = hill_state, hill_score

        # Restore best state
        for c in components:
            bp = best_state.get(c["ref_des"])
            if bp:
                c["x"] = bp[0]
                c["y"] = bp[1]

        _remove_overlaps(components)

        for c in components:
            bbox = c.get("bbox", {})
            c["x"] = _snap(c["x"] - bbox.get("x", 0))
            c["y"] = _snap(c["y"] - bbox.get("y", 0))

        return [{"ref_des": c["ref_des"], "x": c["x"], "y": c["y"],
                 "rotation": c.get("rotation", 0.0)} for c in components]

    def _run_phase(
        self,
        components: list[dict],
        netlist: list,
        pin_matrix: dict,
        schedule: CoolingSchedule,
        pert_list: list[tuple],
        max_iters: int,
        phase_name: str,
        start_score: float | None = None,
    ) -> tuple[dict[str, tuple[float, float]], float]:
        initial_probs = [0.4, 0.25, 0.2, 0.15] if any(
            n in [p[0] for p in pert_list] for n in ["nudge", "swap", "mirror", "reparent"]
        ) else [0.6, 0.4]

        if start_score is not None:
            best_state = {c["ref_des"]: (c["x"], c["y"]) for c in components}
            best_score = start_score
            current_score = start_score
        else:
            report = self._scorer.score_placement(components, netlist, pin_matrix)
            best_state = {c["ref_des"]: (c["x"], c["y"]) for c in components}
            best_score = report.total
            current_score = report.total

        # Set initial probabilities
        n_pert = len(pert_list)
        for i, (name, fn, _) in enumerate(pert_list):
            weight = initial_probs[i] if i < len(initial_probs) else 1.0 / n_pert
            pert_list[i] = (name, fn, weight)

        last_log = 0
        for iteration in range(max_iters):
            temp = schedule.step()

            # Capture pre-perturbation state
            old_state = {c["ref_des"]: (c["x"], c["y"]) for c in components}
            old_score = current_score

            # Rebalance probabilities based on dominant metrics
            overlap_ratio = min(self._metrics.get("overlaps", 0) / max(len(components), 1), 1.0)
            crossing_ratio = min(self._metrics.get("crossings", 0) / max(len(components), 1), 1.0)
            adjusted = _rebalance_probs(pert_list, overlap_ratio, crossing_ratio)

            name, fn = _pick_perturbation(adjusted)
            info = fn(components, netlist)

            if info is None:
                continue

            # Evaluate
            report = self._scorer.score_placement(components, netlist, pin_matrix)
            new_score = report.total
            delta = new_score - old_score

            accepted = schedule.accept(delta)
            if accepted:
                current_score = new_score
                if delta < 0:
                    best_score = new_score
                    best_state = {c["ref_des"]: (c["x"], c["y"]) for c in components}
            else:
                self._restore_state(components, old_state)

            # Update metrics for rebalancing
            ms = report.metric_scores
            self._metrics["overlaps"] = ms.get("overlap", 0)
            self._metrics["crossings"] = ms.get("estimated_crossings", 0)

            # Log
            if self._cfg["log_every"] > 0 and iteration - last_log >= self._cfg["log_every"]:
                last_log = iteration
                self.history.append({
                    "phase": phase_name,
                    "iteration": iteration,
                    "temperature": round(temp, 2),
                    "score": round(best_score, 1),
                    "perturbation": name,
                    "accepted": accepted,
                    "overlaps": self._metrics["overlaps"],
                    "crossings": self._metrics["crossings"],
                })

        return best_state, best_score

    def _greedy_hill_climb(
        self,
        components: list[dict],
        netlist: list,
        pin_matrix: dict,
        max_iters: int,
    ) -> tuple[dict[str, tuple[float, float]], float]:
        best_state = {c["ref_des"]: (c["x"], c["y"]) for c in components}
        report = self._scorer.score_placement(components, netlist, pin_matrix)
        best_score = report.total

        for _ in range(max_iters):
            improved = False
            for c in components:
                if c.get("tier", -1) < 0:
                    continue
                for dx in (-GRID_SIZE, 0, GRID_SIZE):
                    for dy in (-GRID_SIZE, 0, GRID_SIZE):
                        if dx == 0 and dy == 0:
                            continue
                        ox, oy = c["x"], c["y"]
                        c["x"] = _snap(ox + dx)
                        c["y"] = _snap(oy + dy)
                        report = self._scorer.score_placement(
                            components, netlist, pin_matrix)
                        if report.total < best_score:
                            best_score = report.total
                            best_state[c["ref_des"]] = (c["x"], c["y"])
                            improved = True
                        else:
                            c["x"], c["y"] = ox, oy
                if improved:
                    break
            if not improved:
                break

        return best_state, best_score

    @staticmethod
    def _restore_state(components: list[dict],
                       state: dict[str, tuple[float, float]]) -> None:
        for c in components:
            s = state.get(c["ref_des"])
            if s:
                c["x"], c["y"] = s
