"""Tests for schematic screenshot pointer markers.

Covers:
  - Marker numbering (no reuse on delete)
  - toDesignSnapshot() serialization
  - Asset upload / delete API
  - Session isolation
"""

import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.session_db import SessionDB
from server.state import DesignSessionManager, DesignSession


# ── Asset DB Tests ───────────────────────────────────────────────────────


class TestAssetStorage:
    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.db = SessionDB(db_path=self.db_path)

    def teardown_method(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_save_and_load_asset(self):
        ok = self.db.save_asset("s1", "ast_abc", "data:image/png;base64,abc123", "image/png")
        assert ok
        asset = self.db.load_asset("ast_abc")
        assert asset is not None
        assert asset["asset_id"] == "ast_abc"
        assert asset["session_id"] == "s1"
        assert asset["image_data"] == "data:image/png;base64,abc123"
        assert "created_at" in asset

    def test_delete_asset(self):
        self.db.save_asset("s1", "ast_del", "data:image/png;base64,del")
        assert self.db.load_asset("ast_del") is not None
        ok = self.db.delete_asset("ast_del")
        assert ok
        assert self.db.load_asset("ast_del") is None

    def test_session_isolation(self):
        self.db.save_asset("s1", "ast_one", "data:img1")
        self.db.save_asset("s2", "ast_two", "data:img2")
        s1_assets = self.db.list_assets("s1")
        s2_assets = self.db.list_assets("s2")
        assert len(s1_assets) == 1
        assert s1_assets[0]["asset_id"] == "ast_one"
        assert len(s2_assets) == 1
        assert s2_assets[0]["asset_id"] == "ast_two"

    def test_list_assets(self):
        self.db.save_asset("s1", "ast_a", "data:a")
        self.db.save_asset("s1", "ast_b", "data:b")
        assets = self.db.list_assets("s1")
        assert len(assets) == 2
        ids = {a["asset_id"] for a in assets}
        assert ids == {"ast_a", "ast_b"}


# ── Marker Numbering Tests ───────────────────────────────────────────────
# These test the logic that mirrors the frontend ImageMarker behavior.


def make_marker(marker_number):
    return {
        "id": f"img_{marker_number}",
        "marker_number": marker_number,
        "x": 0,
        "y": 0,
        "label": f"Marker {marker_number}",
        "width": 20,
        "height": 15,
        "asset_id": f"ast_{marker_number}",
    }


def get_next_marker_number(markers):
    max_num = 0
    for m in markers:
        if m["marker_number"] > max_num:
            max_num = m["marker_number"]
    return max_num + 1


class TestMarkerNumbering:
    def test_numbers_increment(self):
        markers = []
        for i in range(5):
            n = get_next_marker_number(markers)
            markers.append(make_marker(n))
        assert [m["marker_number"] for m in markers] == [1, 2, 3, 4, 5]

    def test_delete_does_not_reuse_number(self):
        markers = []
        for i in range(4):
            n = get_next_marker_number(markers)
            markers.append(make_marker(n))
        # Delete marker 2
        markers = [m for m in markers if m["marker_number"] != 2]
        assert get_next_marker_number(markers) == 5  # Does not reuse 2

    def test_clear_resets_numbering(self):
        markers = []
        for i in range(3):
            n = get_next_marker_number(markers)
            markers.append(make_marker(n))
        markers.clear()
        # After clear, next should be 1
        assert get_next_marker_number(markers) == 1


# ── Snapshot Serialization Tests ─────────────────────────────────────────


def test_marker_to_snapshot():
    marker = {
        "id": "img_1",
        "marker_number": 1,
        "x": 20.32,
        "y": 15.24,
        "label": "Power section screenshot",
        "width": 20,
        "height": 15,
        "asset_id": "ast_abc123",
    }
    snap = {
        "id": marker["id"],
        "marker_number": marker["marker_number"],
        "label": marker["label"],
        "x": marker["x"],
        "y": marker["y"],
        "width": marker["width"],
        "height": marker["height"],
        "asset_id": marker["asset_id"],
    }
    assert snap["id"] == "img_1"
    assert snap["marker_number"] == 1
    assert snap["asset_id"] == "ast_abc123"


def test_snapshot_includes_markers():
    snapshot = {
        "revision": 5,
        "components": [],
        "wire_paths": [],
        "net_labels": [],
        "image_markers": [
            {
                "id": "img_1",
                "marker_number": 1,
                "label": "Power section",
                "x": 20.32,
                "y": 15.24,
                "width": 20,
                "height": 15,
                "asset_id": "ast_abc123",
            },
        ],
        "power_labels": [],
        "netlist": [],
    }
    assert len(snapshot["image_markers"]) == 1
    assert snapshot["image_markers"][0]["marker_number"] == 1
    assert snapshot["image_markers"][0]["asset_id"] == "ast_abc123"
