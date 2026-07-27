"""Tests for /api/sync_schematic_state endpoint logic.

Verifies full snapshot sync, validation, conflict detection, and derived state rebuild.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.state import session_manager


def _make_snapshot(components=None, wire_paths=None, net_labels=None, revision=0):
    """Build a minimal schematic snapshot."""
    return {
        'revision': revision,
        'components': components or [],
        'wire_paths': wire_paths or [],
        'net_labels': net_labels or [],
        'power_labels': [],
        'netlist': [],
    }


def _sync(snapshot, expected_revision=0, session_id='test_sync_ep'):
    """Call the sync endpoint logic directly."""
    from server.routes import _validate_schematic_snapshot, _normalize_schematic_snapshot, _rebuild_derived_state
    ds = session_manager.get_or_create(session_id)

    errors = _validate_schematic_snapshot(snapshot)
    if errors:
        return None, 400, errors

    normalized = _normalize_schematic_snapshot(snapshot)
    new_revision = ds.merge_canvas_state(normalized, expected_revision)
    if new_revision is None:
        return None, 409, {'current_revision': ds.revision}

    _rebuild_derived_state(ds)
    return new_revision, 200, None


def _reset(session_id='test_sync_ep'):
    """Clear session state."""
    ds = session_manager.get_or_create(session_id)
    ds.clear_design()


# ── Basic sync tests ───────────────────────────────────────────────────

def test_sync_empty_snapshot():
    _reset()
    rev, status, err = _sync(_make_snapshot())
    assert status == 200
    assert rev == 1


def test_sync_with_components():
    _reset()
    snapshot = _make_snapshot(components=[
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20},
        {'ref_des': 'C1', 'id_str': 'Device:C', 'x': 30, 'y': 40},
    ])
    rev, status, err = _sync(snapshot)
    assert status == 200
    assert rev == 1

    ds = session_manager.get_or_create('test_sync_ep')
    comps = ds.get_design().get('selected_components', [])
    assert len(comps) == 2
    assert comps[0]['ref'] == 'R1'


def test_sync_with_wires():
    _reset()
    snapshot = _make_snapshot(
        components=[
            {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
            {'ref_des': 'R2', 'id_str': 'Device:R', 'x': 30, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
        ],
        wire_paths=[
            {'wire_id': 'W1', 'source': 'R1:2', 'target': 'R2:1', 'path': [{'x': 11, 'y': 20}, {'x': 29, 'y': 20}], 'net': 'N$001'},
        ],
    )
    rev, status, err = _sync(snapshot)
    assert status == 200

    ds = session_manager.get_or_create('test_sync_ep')
    design = ds.get_design()
    assert len(design.get('wire_paths', [])) == 1
    assert 'N$001' in design.get('nets', {})


def test_sync_with_net_labels():
    _reset()
    snapshot = _make_snapshot(
        components=[
            {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}]},
        ],
        net_labels=[
            {'id': 'nl_1', 'net': 'VCC', 'x': 10, 'y': 20, 'orientation': 0, 'pin': 'R1:1'},
        ],
    )
    rev, status, err = _sync(snapshot)
    assert status == 200

    ds = session_manager.get_or_create('test_sync_ep')
    design = ds.get_design()
    assert 'VCC' in design.get('nets', {})


# ── Validation tests ───────────────────────────────────────────────────

def test_rejects_missing_ref_des():
    _reset()
    from server.routes import _validate_schematic_snapshot
    snapshot = _make_snapshot(components=[
        {'id_str': 'Device:R', 'x': 10, 'y': 20},  # Missing ref_des
    ])
    errors = _validate_schematic_snapshot(snapshot)
    assert any('ref_des' in e for e in errors)


def test_rejects_duplicate_ref_des():
    _reset()
    from server.routes import _validate_schematic_snapshot
    snapshot = _make_snapshot(components=[
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20},
        {'ref_des': 'R1', 'id_str': 'Device:C', 'x': 30, 'y': 40},  # Duplicate
    ])
    errors = _validate_schematic_snapshot(snapshot)
    assert any('duplicate' in e for e in errors)


def test_rejects_invalid_position():
    _reset()
    from server.routes import _validate_schematic_snapshot
    snapshot = _make_snapshot(components=[
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 'bad', 'y': 20},
    ])
    errors = _validate_schematic_snapshot(snapshot)
    assert any('position' in e for e in errors)


# ── Conflict detection ─────────────────────────────────────────────────

def test_stale_revision_returns_conflict():
    _reset()
    # First sync at revision 0
    rev1, _, _ = _sync(_make_snapshot(components=[{'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20}]))
    assert rev1 == 1

    # Second sync also claiming revision 0 (stale)
    rev2, status, err = _sync(
        _make_snapshot(components=[{'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20}]),
        expected_revision=0
    )
    assert status == 409
    assert rev2 is None


def test_correct_revision_succeeds():
    _reset()
    rev1, _, _ = _sync(_make_snapshot(components=[{'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20}]))
    assert rev1 == 1

    rev2, status, _ = _sync(
        _make_snapshot(components=[{'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20}, {'ref_des': 'C1', 'id_str': 'Device:C', 'x': 30, 'y': 40}]),
        expected_revision=1
    )
    assert status == 200
    assert rev2 == 2


# ── Derived state rebuild ──────────────────────────────────────────────

def test_rebuilds_component_placements():
    _reset()
    snapshot = _make_snapshot(components=[
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10.0, 'y': 20.0},
    ])
    _sync(snapshot)

    ds = session_manager.get_or_create('test_sync_ep')
    design = ds.get_design()
    placements = design.get('component_placements', [])
    assert len(placements) == 1
    assert placements[0]['ref_des'] == 'R1'
    assert placements[0]['x'] == 10.0


def test_rebuilds_pin_matrix():
    _reset()
    snapshot = _make_snapshot(components=[
        {'ref_des': 'R1', 'id_str': 'Device:R', 'x': 10, 'y': 20, 'pins': [{'number': '1'}, {'number': '2'}]},
    ])
    _sync(snapshot)

    ds = session_manager.get_or_create('test_sync_ep')
    design = ds.get_design()
    pin_matrix = design.get('pin_matrix', {})
    assert 'R1:1' in pin_matrix
    assert 'R1:2' in pin_matrix


# ── Sequential syncs ───────────────────────────────────────────────────

def test_multiple_syncs_increment_revision():
    _reset()
    for i in range(5):
        rev, status, _ = _sync(
            _make_snapshot(components=[{'ref_des': f'R{i+1}', 'id_str': 'Device:R', 'x': i * 10, 'y': 20}]),
            expected_revision=i
        )
        assert status == 200
        assert rev == i + 1
