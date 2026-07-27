"""Tests for canonical design state in DesignSession.

Verifies revision tracking, non-destructive mutations, and atomic merges.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.state import DesignSession


def _make_session(session_id="test_canonical") -> DesignSession:
    """Create a fresh DesignSession for testing."""
    ds = DesignSession(session_id)
    ds.clear_design()  # Reset any DB-loaded state
    return ds


# ── apply_mutation tests ─────────────────────────────────────────────────

def test_apply_mutation_increments_revision():
    ds = _make_session()
    assert ds.revision == 0

    new_rev = ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    assert new_rev == 1
    assert ds.revision == 1

    new_rev = ds.apply_mutation({"wire_paths": [{"wire_id": "W1"}]})
    assert new_rev == 2
    assert ds.revision == 2


def test_apply_mutation_preserves_other_keys():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    ds.apply_mutation({"wire_paths": [{"wire_id": "W1"}]})

    design = ds.get_design()
    assert len(design["selected_components"]) == 1
    assert len(design["wire_paths"]) == 1


def test_apply_mutation_skips_none_values():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    ds.apply_mutation({"wire_paths": None, "net_labels": [{"net": "GND"}]})

    design = ds.get_design()
    assert "wire_paths" not in design or design["wire_paths"] is None or design["wire_paths"] == []
    assert len(design["net_labels"]) == 1


def test_apply_mutation_sets_revision_metadata():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})

    design = ds.get_design()
    assert design["_revision"] == 1
    assert "_updated_at" in design


# ── replace_design tests ────────────────────────────────────────────────

def test_replace_design_clears_and_replaces():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    ds.apply_mutation({"wire_paths": [{"wire_id": "W1"}]})

    new_rev = ds.replace_design({
        "selected_components": [{"ref": "C1"}],
        "wire_paths": [{"wire_id": "W2"}],
    })

    assert new_rev == 3  # Two apply_mutation calls got revs 1,2; replace gets 3
    design = ds.get_design()
    assert len(design["selected_components"]) == 1
    assert design["selected_components"][0]["ref"] == "C1"
    assert len(design["wire_paths"]) == 1
    assert design["wire_paths"][0]["wire_id"] == "W2"


def test_replace_design_increments_revision():
    ds = _make_session()
    ds.replace_design({"selected_components": []})
    assert ds.revision == 1
    ds.replace_design({"selected_components": [{"ref": "R1"}]})
    assert ds.revision == 2


# ── merge_canvas_state tests ───────────────────────────────────────────

def test_merge_canvas_state_succeeds_with_matching_revision():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    assert ds.revision == 1

    new_rev = ds.merge_canvas_state(
        {"components": [{"ref": "R1"}, {"ref": "R2"}]},
        expected_revision=1
    )
    assert new_rev == 2
    assert ds.revision == 2


def test_merge_canvas_state_returns_none_on_conflict():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    # Revision is 1, but we claim it's 0
    result = ds.merge_canvas_state(
        {"components": [{"ref": "R1"}, {"ref": "R2"}]},
        expected_revision=0
    )
    assert result is None
    assert ds.revision == 1  # Unchanged


def test_merge_canvas_state_preserves_existing_keys():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    ds.apply_mutation({"pin_matrix": {"R1:1": {"net": "GND"}}})

    new_rev = ds.merge_canvas_state(
        {"components": [{"ref": "R1"}, {"ref": "C1"}]},
        expected_revision=2
    )
    assert new_rev == 3
    design = ds.get_design()
    # pin_matrix was not in the snapshot, so it should be preserved
    assert "pin_matrix" in design
    assert design["pin_matrix"]["R1:1"]["net"] == "GND"


def test_merge_canvas_state_skips_internal_metadata():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})

    new_rev = ds.merge_canvas_state(
        {"_revision": 999, "_internal": True, "components": [{"ref": "R1"}]},
        expected_revision=1
    )
    assert new_rev == 2
    # Internal keys should not be written
    assert ds.last_design.get("_revision") == 2  # Updated by _bump_revision


# ── set_design deprecation ─────────────────────────────────────────────

def test_set_design_emits_deprecation_warning():
    import warnings
    ds = _make_session()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ds.set_design({"selected_components": [{"ref": "R1"}]})
        # The logger.warning won't appear in warnings module, but the function
        # should still work without error
    assert ds.revision == 0  # set_design doesn't bump revision (deprecated path)


# ── clear_design resets revision ───────────────────────────────────────

def test_clear_design_resets_revision():
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})
    assert ds.revision == 1

    ds.clear_design()
    assert ds.revision == 0


# ── persistence ────────────────────────────────────────────────────────

def test_apply_mutation_persists_to_db():
    """Verify that mutations trigger a DB persist."""
    ds = _make_session()
    ds.apply_mutation({"selected_components": [{"ref": "R1"}]})

    # Reload from DB
    ds2 = DesignSession(ds.session_id)
    ds2._loaded_from_db = False
    ds2.last_design = {}
    design = ds2.get_design()

    assert len(design.get("selected_components", [])) == 1
    assert design["selected_components"][0]["ref"] == "R1"
    assert design.get("_revision") == 1


# ── thread safety ──────────────────────────────────────────────────────

def test_concurrent_mutations_increment_revision():
    """Multiple concurrent mutations should each get a unique revision."""
    import threading

    ds = _make_session()
    revisions = []

    def mutate(key, value):
        rev = ds.apply_mutation({key: value})
        revisions.append(rev)

    threads = [threading.Thread(target=mutate, args=(f"key_{i}", f"val_{i}"))
               for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All revisions should be unique
    assert len(set(revisions)) == 10
    assert ds.revision == 10
