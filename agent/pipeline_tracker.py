"""Authoritative pipeline execution tracking for CircuitBot LangGraph runs."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from agent.emit_utils import emit_pipeline_event


PIPELINE_SCHEMA_VERSION = 1
TERMINAL_STAGE_STATUSES = {"completed", "warning", "failed", "skipped", "cancelled"}


@dataclass(frozen=True)
class StageDefinition:
    key: str
    label: str
    phase: str
    order: int
    optional: bool = False
    graph_versions: tuple[str, ...] = ("new",)


@dataclass
class StageAttempt:
    attempt: int
    status: str
    started_at_ms: int
    completed_at_ms: int | None = None
    duration_ms: int | None = None
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


PHASE_CATALOG = (
    {"key": "understand", "label": "Understand", "order": 1},
    {"key": "components", "label": "Components", "order": 2},
    {"key": "schematic", "label": "Schematic", "order": 3},
    {"key": "pcb", "label": "PCB", "order": 4},
)


def _stage(
    key: str,
    label: str,
    phase: str,
    order: int,
    *,
    optional: bool = False,
    graph_versions: tuple[str, ...] = ("new", "legacy"),
) -> StageDefinition:
    return StageDefinition(key, label, phase, order, optional, graph_versions)


STAGE_CATALOG: dict[str, StageDefinition] = {
    item.key: item
    for item in (
        _stage("clarify", "Clarify requirements", "understand", 1),
        _stage("analyze", "Analyze design", "understand", 2),
        _stage("research", "Research components", "understand", 3),
        _stage("architecture_planner", "Plan architecture", "understand", 4, graph_versions=("new",)),
        _stage("capability_resolver", "Resolve capabilities", "understand", 5, graph_versions=("new",)),
        _stage("select", "Select components", "components", 6),
        _stage("dependency_expander", "Expand dependencies", "components", 7, graph_versions=("new",)),
        _stage("deduplicator", "Deduplicate components", "components", 8, graph_versions=("new",)),
        _stage("constraint_checker", "Check constraints", "components", 9, graph_versions=("new",)),
        _stage("repair", "Repair component set", "components", 10, optional=True, graph_versions=("new",)),
        _stage("validate", "Validate components", "components", 11),
        _stage("validate_repair", "Repair validation issues", "components", 12, optional=True),
        _stage("ask_validation_help", "Resolve validation", "components", 13, optional=True),
        _stage(
            "post_validate_constraint_checker",
            "Recheck constraints",
            "components",
            14,
            graph_versions=("new",),
        ),
        _stage("freeze_components", "Freeze component list", "components", 15, graph_versions=("new",)),
        _stage("datasheet_search", "Search datasheets", "components", 16),
        _stage("symbol_compatibility", "Check symbol compatibility", "schematic", 17),
        _stage("dispatch", "Load component symbols", "schematic", 18),
        _stage("pin_marker", "Mark unused pins", "schematic", 19, graph_versions=("new",)),
        _stage("symbol_validate", "Validate symbols", "schematic", 20),
        _stage("connection_search", "Plan connections", "schematic", 21),
        _stage("netlist", "Generate netlist", "schematic", 22),
        _stage("power_net_repair", "Repair power nets", "schematic", 23),
        _stage("structural_net_validate", "Validate net structure", "schematic", 24),
        _stage("structural_net_repair", "Repair net structure", "schematic", 25),
        _stage("placement", "Place schematic symbols", "schematic", 26),
        _stage("routing", "Route schematic", "schematic", 27),
        _stage("connectivity_validate", "Validate connectivity", "schematic", 28),
        _stage("connectivity_repair", "Repair connectivity", "schematic", 29),
        _stage("schematic_audit", "Audit schematic", "schematic", 30),
        _stage("schematic_repair", "Repair schematic", "schematic", 31, optional=True),
        _stage("ask_pcb_approval", "Confirm PCB layout", "pcb", 32),
        _stage("ask_board_config", "Configure board stackup", "pcb", 33, optional=True),
        _stage("pcb_layout", "Generate PCB layout", "pcb", 34, optional=True),
        _stage("design_review", "Review completed design", "pcb", 35, optional=True),
        _stage("llm_judge", "Evaluate design quality", "pcb", 36, optional=True),
    )
}


def stage_catalog_for_graph(graph_version: str) -> list[dict[str, Any]]:
    return [
        asdict(stage)
        for stage in sorted(STAGE_CATALOG.values(), key=lambda item: item.order)
        if graph_version in stage.graph_versions
    ]


def _length(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set, dict)) else 0


def _research_count(result: dict[str, Any]) -> int:
    research = result.get("research_results", {})
    if isinstance(research, dict):
        return sum(_length(value) for value in research.values())
    return _length(research)


def _summary_for_stage(stage_key: str, result: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(result, dict):
        return "", {}

    summary = ""
    metrics: dict[str, Any] = {}
    if stage_key == "analyze":
        count = _length(result.get("analysis"))
        summary, metrics = f"{count} subsystems identified", {"subsystems": count}
    elif stage_key == "research":
        count = _research_count(result)
        summary, metrics = f"{count} candidates found", {"candidates": count}
    elif stage_key in {"select", "dependency_expander", "deduplicator", "freeze_components"}:
        count = _length(result.get("selected_components"))
        summary, metrics = f"{count} components retained", {"components": count}
    elif stage_key in {"constraint_checker", "post_validate_constraint_checker"}:
        fatal = _length(result.get("fatal_errors"))
        repairable = _length(result.get("repairable_errors"))
        warnings = _length(result.get("constraint_warnings"))
        summary = f"{fatal + repairable + warnings} constraint findings"
        metrics = {"fatal": fatal, "repairable": repairable, "warnings": warnings}
    elif stage_key == "validate":
        errors = _length(result.get("validation_errors"))
        warnings = _length(result.get("validation_warnings"))
        summary = "Passed" if not errors and not warnings else f"{errors} errors, {warnings} warnings"
        metrics = {"errors": errors, "warnings": warnings}
    elif stage_key == "netlist":
        count = _length(result.get("nets") or result.get("netlist"))
        summary, metrics = f"{count} nets generated", {"nets": count}
    elif stage_key == "placement":
        count = _length(result.get("component_placements"))
        summary, metrics = f"{count} symbols placed", {"placements": count}
    elif stage_key == "routing":
        count = _length(result.get("wire_paths"))
        summary, metrics = f"{count} connections routed", {"connections": count}
    elif stage_key == "pcb_layout":
        generated = bool(result.get("board_model") or result.get("_board_model"))
        summary, metrics = ("Board model generated" if generated else "PCB layout completed"), {"board_model": generated}
    elif stage_key == "design_review":
        count = _length(result.get("review_suggestions"))
        summary, metrics = f"{count} review suggestions", {"suggestions": count}
    elif stage_key == "llm_judge":
        eval_result = result.get("judge_evaluation", {})
        overall = eval_result.get("overall", 0) if isinstance(eval_result, dict) else 0
        summary = f"Overall score: {overall}/10"
        metrics = {"overall": overall} if isinstance(overall, (int, float)) else {}
    elif result.get("error"):
        summary = str(result.get("error"))[:240]

    return summary, metrics


def _status_for_result(stage_key: str, result: Any) -> str:
    if not isinstance(result, dict):
        return "completed"
    if result.get("error") or result.get("fatal_errors"):
        return "failed"
    if stage_key == "ask_pcb_approval" and result.get("pcb_approved") is False:
        return "skipped"
    if result.get("repairable_errors") or result.get("validation_errors"):
        return "warning"
    return "completed"


class PipelineRun:
    """Thread-safe execution record shared through LangGraph configurable state."""

    def __init__(
        self,
        graph_version: str,
        emitter: Callable[[str, dict[str, Any]], None] | None,
        snapshot_sink: Callable[[dict[str, Any]], None] | None = None,
        run_id: str | None = None,
    ):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.graph_version = graph_version
        self.status = "pending"
        self.stages: dict[str, list[StageAttempt]] = {}
        self.current_stage: str | None = None
        self.current_attempt: int | None = None
        self.sequence = 0
        self.started_at_ms = int(time.time() * 1000)
        self.completed_at_ms: int | None = None
        self.duration_ms: int | None = None
        self._monotonic_started = time.monotonic()
        self._emitter = emitter
        self._snapshot_sink = snapshot_sink
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            self.status = "running"
            event = self._event_locked(
                "run_started",
                status=self.status,
                graph_version=self.graph_version,
                stage_catalog=stage_catalog_for_graph(self.graph_version),
                phase_catalog=copy.deepcopy(PHASE_CATALOG),
                started_at_ms=self.started_at_ms,
            )
        self._publish(event)

    def start_stage(self, stage_key: str) -> StageAttempt:
        stage = STAGE_CATALOG[stage_key]
        with self._lock:
            attempts = self.stages.setdefault(stage_key, [])
            attempt = StageAttempt(
                attempt=len(attempts) + 1,
                status="running",
                started_at_ms=int(time.time() * 1000),
            )
            attempts.append(attempt)
            self.current_stage = stage_key
            self.current_attempt = attempt.attempt
            event = self._event_locked(
                "stage_started",
                stage=stage,
                attempt=attempt,
            )
        self._publish(event)
        return attempt

    def update_stage(self, stage_key: str, attempt_number: int, status: str, summary: str = "") -> None:
        stage = STAGE_CATALOG.get(stage_key)
        if stage is None:
            return
        with self._lock:
            attempt = self._find_attempt_locked(stage_key, attempt_number)
            if attempt is None or attempt.status in TERMINAL_STAGE_STATUSES:
                return
            attempt.status = status
            if summary:
                attempt.summary = summary[:240]
            event = self._event_locked("stage_updated", stage=stage, attempt=attempt)
        self._publish(event)

    def finish_stage(
        self,
        stage_key: str,
        attempt_number: int,
        status: str,
        summary: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> None:
        stage = STAGE_CATALOG.get(stage_key)
        if stage is None:
            return
        with self._lock:
            attempt = self._find_attempt_locked(stage_key, attempt_number)
            if attempt is None or attempt.status in TERMINAL_STAGE_STATUSES:
                return
            attempt.status = status
            attempt.completed_at_ms = int(time.time() * 1000)
            attempt.duration_ms = max(0, attempt.completed_at_ms - attempt.started_at_ms)
            if summary:
                attempt.summary = summary[:240]
            if metrics:
                attempt.metrics = copy.deepcopy(metrics)
            if self.current_stage == stage_key and self.current_attempt == attempt_number:
                self.current_stage = None
                self.current_attempt = None
            event = self._event_locked("stage_finished", stage=stage, attempt=attempt)
        self._publish(event)

    def close(self, status: str, summary: str = "") -> None:
        with self._lock:
            if self.status in {"completed", "failed", "cancelled"}:
                return
            now_ms = int(time.time() * 1000)
            if self.current_stage and self.current_attempt:
                attempt = self._find_attempt_locked(self.current_stage, self.current_attempt)
                if attempt and attempt.status not in TERMINAL_STAGE_STATUSES:
                    attempt.status = "failed" if status == "failed" else "cancelled"
                    attempt.completed_at_ms = now_ms
                    attempt.duration_ms = max(0, now_ms - attempt.started_at_ms)
            self.status = status
            self.completed_at_ms = now_ms
            self.duration_ms = max(0, int((time.monotonic() - self._monotonic_started) * 1000))
            self.current_stage = None
            self.current_attempt = None
            event = self._event_locked(
                "run_finished",
                status=status,
                summary=summary[:240],
                completed_at_ms=self.completed_at_ms,
                duration_ms=self.duration_ms,
            )
        self._publish(event)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": PIPELINE_SCHEMA_VERSION,
                "run_id": self.run_id,
                "graph_version": self.graph_version,
                "status": self.status,
                "sequence": self.sequence,
                "started_at_ms": self.started_at_ms,
                "completed_at_ms": self.completed_at_ms,
                "duration_ms": self.duration_ms,
                "current_stage": self.current_stage,
                "current_attempt": self.current_attempt,
                "phase_catalog": copy.deepcopy(PHASE_CATALOG),
                "stage_catalog": stage_catalog_for_graph(self.graph_version),
                "stages": {
                    key: [asdict(attempt) for attempt in attempts]
                    for key, attempts in self.stages.items()
                },
            }

    def _find_attempt_locked(self, stage_key: str, attempt_number: int) -> StageAttempt | None:
        attempts = self.stages.get(stage_key, [])
        for attempt in attempts:
            if attempt.attempt == attempt_number:
                return attempt
        return None

    def _event_locked(self, action: str, **payload: Any) -> dict[str, Any]:
        self.sequence += 1
        event: dict[str, Any] = {
            "schema_version": PIPELINE_SCHEMA_VERSION,
            "action": action,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "graph_version": self.graph_version,
            "ts": int(time.time() * 1000),
        }
        stage = payload.pop("stage", None)
        attempt = payload.pop("attempt", None)
        if stage is not None:
            event.update({
                "stage_key": stage.key,
                "stage_label": stage.label,
                "phase": stage.phase,
                "order": stage.order,
            })
        if attempt is not None:
            event.update(asdict(attempt))
        event.update(payload)
        return event

    def _publish(self, event: dict[str, Any]) -> None:
        snapshot = self.to_dict()
        if self._snapshot_sink:
            self._snapshot_sink(snapshot)
        if self._emitter:
            emit_pipeline_event(self._emitter, event)


def init_pipeline_run(
    config: dict[str, Any],
    graph_version: str,
    snapshot_sink: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineRun:
    configurable = config.setdefault("configurable", {})
    run = PipelineRun(
        graph_version=graph_version,
        emitter=configurable.get("emit"),
        snapshot_sink=snapshot_sink,
        run_id=configurable.get("run_id"),
    )
    configurable["pipeline_run"] = run
    run.start()
    return run


def get_pipeline_run(config: dict[str, Any] | None) -> PipelineRun | None:
    return (config or {}).get("configurable", {}).get("pipeline_run")


def update_pipeline_stage(config: dict[str, Any], status: str, summary: str = "") -> None:
    configurable = (config or {}).get("configurable", {})
    context = configurable.get("pipeline_context") or {}
    run = configurable.get("pipeline_run")
    if not run or not context:
        return
    run.update_stage(context.get("stage_key"), context.get("attempt"), status, summary)


def track_node(node_key: str, node_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a graph node with ordered execution tracking."""
    if getattr(node_fn, "_pipeline_tracked_key", None) == node_key:
        return node_fn

    def wrapped(state, config=None, *args, **kwargs):
        run = get_pipeline_run(config)
        if run is None or node_key not in STAGE_CATALOG:
            return node_fn(state, config, *args, **kwargs)

        attempt = run.start_stage(node_key)
        stage_config = dict(config or {})
        configurable = dict(stage_config.get("configurable", {}))
        configurable["pipeline_context"] = {
            "run_id": run.run_id,
            "stage_key": node_key,
            "attempt": attempt.attempt,
        }
        stage_config["configurable"] = configurable

        try:
            result = node_fn(state, stage_config, *args, **kwargs)
        except Exception as exc:
            run.finish_stage(node_key, attempt.attempt, "failed", str(exc))
            raise

        summary, metrics = _summary_for_stage(node_key, result)
        run.finish_stage(
            node_key,
            attempt.attempt,
            _status_for_result(node_key, result),
            summary,
            metrics,
        )
        return result

    wrapped.__name__ = getattr(node_fn, "__name__", f"tracked_{node_key}")
    wrapped.__doc__ = getattr(node_fn, "__doc__", None)
    wrapped._pipeline_tracked_key = node_key
    wrapped._pipeline_original = node_fn
    return wrapped


def add_tracked_node(builder, node_key: str, node_fn: Callable[..., Any]) -> None:
    builder.add_node(node_key, track_node(node_key, node_fn))
