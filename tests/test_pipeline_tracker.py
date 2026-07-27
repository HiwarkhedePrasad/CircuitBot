import pytest

from agent.emit_utils import _emit
from agent.pipeline_tracker import (
    PipelineRun,
    init_pipeline_run,
    stage_catalog_for_graph,
    track_node,
    update_pipeline_stage,
)


def _config(events, snapshots):
    return {
        "configurable": {
            "emit": lambda event, data: events.append((event, data)),
            "run_id": "run-test",
        }
    }, lambda snapshot: snapshots.append(snapshot)


def test_tracked_node_preserves_result_and_emits_ordered_events():
    events = []
    snapshots = []
    config, sink = _config(events, snapshots)
    run = init_pipeline_run(config, "new", sink)
    expected = {"selected_components": [{"ref_des": "U1"}]}

    wrapped = track_node("select", lambda state, node_config: expected)
    result = wrapped({}, config)
    run.close("completed")

    assert result is expected
    pipeline_events = [data for event, data in events if event == "agent:pipeline"]
    assert [event["action"] for event in pipeline_events] == [
        "run_started",
        "stage_started",
        "stage_finished",
        "run_finished",
    ]
    assert [event["sequence"] for event in pipeline_events] == [1, 2, 3, 4]
    assert pipeline_events[2]["metrics"] == {"components": 1}
    assert snapshots[-1]["status"] == "completed"


def test_repeated_stage_allocates_attempts():
    events = []
    config = {"configurable": {"emit": lambda event, data: events.append(data), "run_id": "retry-run"}}
    run = init_pipeline_run(config, "new")
    wrapped = track_node("repair", lambda state, node_config: {"selected_components": []})

    wrapped({}, config)
    wrapped({}, config)

    snapshot = run.to_dict()
    assert [attempt["attempt"] for attempt in snapshot["stages"]["repair"]] == [1, 2]


def test_exception_marks_stage_failed_and_reraises():
    config = {"configurable": {"emit": lambda *_: None, "run_id": "failure-run"}}
    run = init_pipeline_run(config, "new")

    def fail(state, node_config):
        raise RuntimeError("selection failed")

    with pytest.raises(RuntimeError, match="selection failed"):
        track_node("select", fail)({}, config)

    attempt = run.to_dict()["stages"]["select"][0]
    assert attempt["status"] == "failed"
    assert attempt["summary"] == "selection failed"


def test_waiting_transition_and_log_context_are_correlated():
    emitted = []
    config = {"configurable": {"emit": lambda event, data: emitted.append((event, data)), "run_id": "wait-run"}}
    run = init_pipeline_run(config, "new")

    def wait_node(state, node_config):
        update_pipeline_stage(node_config, "waiting", "Awaiting PCB approval")
        _emit(node_config, "agent:log", {"message": "Approval requested"})
        return {"pcb_approved": True}

    track_node("ask_pcb_approval", wait_node)({}, config)

    actions = [data for event, data in emitted if event == "agent:pipeline"]
    assert [event["action"] for event in actions[1:]] == [
        "stage_started",
        "stage_updated",
        "stage_finished",
    ]
    log = next(data for event, data in emitted if event == "agent:log")
    assert log["run_id"] == run.run_id
    assert log["stage_key"] == "ask_pcb_approval"
    assert log["attempt"] == 1


def test_graph_catalogs_do_not_duplicate_backend_frontend_definitions():
    new_keys = {stage["key"] for stage in stage_catalog_for_graph("new")}
    legacy_keys = {stage["key"] for stage in stage_catalog_for_graph("legacy")}

    assert "architecture_planner" in new_keys
    assert "architecture_planner" not in legacy_keys
    assert "validate_repair" in legacy_keys
    assert "validate_repair" in new_keys


def test_pipeline_snapshot_is_json_compatible():
    run = PipelineRun("new", emitter=None, run_id="json-run")
    run.start()
    snapshot = run.to_dict()

    assert snapshot["schema_version"] == 1
    assert snapshot["phase_catalog"][0]["key"] == "understand"
    assert isinstance(snapshot["stage_catalog"], list)
