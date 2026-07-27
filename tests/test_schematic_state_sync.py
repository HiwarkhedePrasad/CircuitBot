"""Tests for schematic state sync with image marker support.

Verifies that image_markers survive the sync round-trip
and that two sessions remain isolated.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.state import DesignSession, DesignSessionManager
from server.session_db import SessionDB


class TestStateSyncWithMarkers:
    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.db = SessionDB(db_path=self.db_path)
        # Patch the session DB to use our temp db
        import server.state
        original_init = DesignSession.__init__

        def patched_init(self, session_id):
            original_init(self, session_id)
            self._db_path = self.db_path
        self._patcher = patched_init

    def test_marker_in_snapshot_roundtrip(self):
        ds = DesignSession("test_marker_roundtrip")
        snapshot = {
            "components": [],
            "wire_paths": [],
            "net_labels": [],
            "image_markers": [
                {
                    "id": "img_1",
                    "marker_number": 1,
                    "label": "Test Marker",
                    "x": 10.0,
                    "y": 20.0,
                    "width": 20,
                    "height": 15,
                    "asset_id": "ast_test123",
                },
            ],
            "power_labels": [],
            "netlist": [],
        }
        ds.replace_design(snapshot)
        loaded = ds.get_design()
        assert "image_markers" in loaded
        assert len(loaded["image_markers"]) == 1
        assert loaded["image_markers"][0]["marker_number"] == 1
        assert loaded["image_markers"][0]["asset_id"] == "ast_test123"

    def test_marker_merge_canvas_state(self):
        ds = DesignSession("test_marker_merge")
        base_snapshot = {
            "components": [],
            "wire_paths": [],
            "net_labels": [],
            "image_markers": [],
            "power_labels": [],
            "netlist": [],
        }
        ds.replace_design(base_snapshot)
        rev = ds.revision

        update = {
            "image_markers": [
                {
                    "id": "img_1",
                    "marker_number": 1,
                    "label": "Merged Marker",
                    "x": 5.0, "y": 5.0,
                    "width": 20, "height": 15,
                    "asset_id": "ast_merged",
                },
            ],
        }
        new_rev = ds.merge_canvas_state(update, rev)
        assert new_rev is not None
        assert len(ds.last_design.get("image_markers", [])) == 1

    def test_session_isolation(self):
        mgr = DesignSessionManager()
        ds1 = mgr.get_or_create("session_a")
        ds2 = mgr.get_or_create("session_b")

        snap_a = {
            "components": [],
            "wire_paths": [],
            "net_labels": [],
            "image_markers": [{"id": "img_1", "marker_number": 1, "label": "A", "x": 0, "y": 0, "width": 20, "height": 15, "asset_id": "ast_a"}],
            "power_labels": [],
            "netlist": [],
        }
        snap_b = {
            "components": [],
            "wire_paths": [],
            "net_labels": [],
            "image_markers": [{"id": "img_1", "marker_number": 1, "label": "B", "x": 0, "y": 0, "width": 20, "height": 15, "asset_id": "ast_b"}],
            "power_labels": [],
            "netlist": [],
        }
        ds1.replace_design(snap_a)
        ds2.replace_design(snap_b)

        assert ds1.last_design["image_markers"][0]["asset_id"] == "ast_a"
        assert ds2.last_design["image_markers"][0]["asset_id"] == "ast_b"
