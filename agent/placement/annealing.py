from __future__ import annotations

import math
import random


class CoolingSchedule:
    """Adaptive cooling schedule for simulated annealing.

    Tracks acceptance ratio and adjusts cooling rate so the process
    neither freezes prematurely nor bounces forever near T0.
    """

    def __init__(
        self,
        t_start: float = 1000.0,
        t_min: float = 0.1,
        cooling_rate: float = 0.95,
        target_acceptance: float = 0.44,
        adaptation_strength: float = 0.1,
        max_iterations: int = 1000,
    ):
        self.t = t_start
        self.t_start = t_start
        self.t_min = t_min
        self.base_rate = cooling_rate
        self.target_accept = target_acceptance
        self.adapt_strength = adaptation_strength
        self.max_iters = max_iterations

        self._rate = cooling_rate
        self._step = 0
        self._window_size = 50
        self._accepted: list[bool] = []

    def step(self) -> float:
        self._step += 1
        self.t = max(self.t * self._rate, self.t_min)
        return self.t

    def accept(self, delta: float) -> bool:
        if delta <= 0:
            return True
        if self.t <= 0:
            return False
        prob = math.exp(-delta / self.t)
        accepted = random.random() < prob
        self._accepted.append(accepted)
        if len(self._accepted) > self._window_size:
            self._accepted.pop(0)
        self._adapt_rate()
        return accepted

    def _adapt_rate(self) -> None:
        if len(self._accepted) < self._window_size:
            return
        ratio = sum(self._accepted) / len(self._accepted)
        if ratio < self.target_accept - 0.05:
            self._rate = min(self._rate * (1 + self.adapt_strength), 0.999)
        elif ratio > self.target_accept + 0.05:
            self._rate = max(self._rate * (1 - self.adapt_strength), 0.5)

    @property
    def frozen(self) -> bool:
        return self.t <= self.t_min or self._step >= self.max_iters

    @property
    def progress(self) -> float:
        return self._step / self.max_iters

    def reset(self) -> None:
        self.t = self.t_start
        self._rate = self.base_rate
        self._step = 0
        self._accepted.clear()
