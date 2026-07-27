"""AI Planner — decomposes cross-capability goals into ordered DAGs.

Takes a user goal, uses the ReasoningEngine to map to principles and capabilities,
then returns an ordered plan with preconditions and expected outputs.

The Planner is deterministic — no LLM calls. All decisions come from
the ReasoningEngine knowledge graph and the current design state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class PlanStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """A single step in a plan."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    capability: str = ""
    description: str = ""
    status: PlanStatus = PlanStatus.PENDING
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # step IDs this depends on
    estimated_cost: float = 1.0
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "capability": self.capability,
            "description": self.description, "status": self.status.value,
            "depends_on": self.depends_on, "estimated_cost": self.estimated_cost,
        }


@dataclass
class Plan:
    """An ordered plan of capabilities to execute."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    principles: list[str] = field(default_factory=list)  # principle IDs used
    status: PlanStatus = PlanStatus.PENDING
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "principles": self.principles, "status": self.status.value,
        }

    @property
    def execution_order(self) -> list[PlanStep]:
        """Return steps in topological execution order."""
        if not self.steps:
            return []

        # Build dependency graph
        step_map = {s.id: s for s in self.steps}
        in_degree = {s.id: 0 for s in self.steps}
        for s in self.steps:
            for dep_id in s.depends_on:
                if dep_id in in_degree:
                    in_degree[s.id] += 1

        # Kahn's algorithm for topological sort
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            sid = queue.pop(0)
            order.append(step_map[sid])
            for s in self.steps:
                if sid in s.depends_on:
                    in_degree[s.id] -= 1
                    if in_degree[s.id] == 0:
                        queue.append(s.id)

        return order

    @property
    def estimated_total_cost(self) -> float:
        """Sum of all step costs."""
        return sum(s.estimated_cost for s in self.steps)

    def next_step(self) -> PlanStep | None:
        """Get the next pending step with all dependencies satisfied."""
        completed = {s.id for s in self.steps if s.status == PlanStatus.COMPLETED}
        for step in self.execution_order:
            if step.status != PlanStatus.PENDING:
                continue
            if all(dep in completed for dep in step.depends_on):
                return step
        return None


# ── Capability dependency rules ─────────────────────────────────────────

# Which capabilities must run before others
DEPENDENCY_RULES: dict[str, list[str]] = {
    "placement_optimization": ["thermal_analysis"],
    "routing_optimization": ["placement_optimization", "trace_sizing"],
    "trace_widening": ["thermal_analysis"],
    "copper_pour": ["placement_optimization"],
    "via_placement": ["placement_optimization"],
    "stackup_optimization": ["routing_analysis"],
}


class AIPlanner:
    """Decomposes goals into ordered capability DAGs.

    Uses ReasoningEngine for principle mapping and dependency rules
    for ordering. No LLM calls — fully deterministic.
    """

    def __init__(self, reasoning_engine: Any = None):
        if reasoning_engine is None:
            from agent.runtime.reasoning_engine import ReasoningEngine
            self._reasoning = ReasoningEngine()
        else:
            self._reasoning = reasoning_engine

    def plan(self, goal: str, current_state: dict | None = None) -> Plan:
        """Create a plan for achieving a goal.

        Args:
            goal: user goal like "reduce board cost" or "improve thermal"
            current_state: current design state for context

        Returns:
            Ordered Plan with steps, dependencies, and cost estimates.
        """
        plan = Plan(goal=goal)

        # 1. Decompose goal into principles
        principles = self._reasoning.decompose(goal)
        plan.principles = [p.id for p in principles]

        # 2. Map principles to capabilities
        capabilities = self._reasoning.map_to_capabilities(principles)

        # 3. Suggest checks
        checks = self._reasoning.suggest_checks(goal)
        plan.metadata["suggested_checks"] = [c.to_dict() for c in checks]

        # 4. Build steps with dependencies
        steps = []
        cap_to_step: dict[str, PlanStep] = {}

        for cap in capabilities:
            step = PlanStep(
                capability=cap.name,
                description=cap.description,
                input_types=list(cap.input_types),
                output_types=list(cap.output_types),
                estimated_cost=cap.estimated_cost,
            )
            steps.append(step)
            cap_to_step[cap.name] = step

        # 5. Apply dependency rules
        for cap in capabilities:
            deps = DEPENDENCY_RULES.get(cap.name, [])
            step = cap_to_step.get(cap.name)
            if step:
                for dep_name in deps:
                    dep_step = cap_to_step.get(dep_name)
                    if dep_step and dep_step.id != step.id:
                        step.depends_on.append(dep_step.id)

        # 6. Add analysis/validation steps
        # Always start with analysis
        analysis_step = PlanStep(
            capability="design_analysis",
            description="Analyze current design state",
            input_types=["board_model", "selected_components"],
            output_types=["analysis_report"],
            estimated_cost=0.5,
        )
        steps.insert(0, analysis_step)

        # End with validation
        validation_step = PlanStep(
            capability="design_validation",
            description="Validate changes against constraints",
            input_types=["board_model", "constraints"],
            output_types=["validation_report"],
            estimated_cost=0.5,
        )
        # All other steps depend on validation completing last
        steps.append(validation_step)

        # Make all capability steps depend on analysis
        for step in steps:
            if step.capability not in ("design_analysis", "design_validation"):
                if analysis_step.id not in step.depends_on:
                    step.depends_on.insert(0, analysis_step.id)
                validation_step.depends_on.append(step.id)

        plan.steps = steps
        return plan

    def explain_plan(self, plan: Plan) -> str:
        """Generate human-readable explanation of the plan."""
        lines = [f"Plan: {plan.goal}"]
        lines.append(f"Principles: {', '.join(plan.principles)}")
        lines.append(f"Steps: {len(plan.steps)}")
        lines.append(f"Estimated cost: {plan.estimated_total_cost:.1f}")
        lines.append("")
        lines.append("Execution order:")
        for i, step in enumerate(plan.execution_order, 1):
            deps = f" (after: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  {i}. {step.capability}: {step.description}{deps}")
        return "\n".join(lines)
