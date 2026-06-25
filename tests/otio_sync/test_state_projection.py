"""Unit tests for the canonical state projection and diff.

Uses the real ``sync_test/recordings/reorder.jsonl`` snapshot as a fixture so
the projection is exercised against the actual wire shape produced by the
master, not a hand-built mock.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.append(os.path.join(project_root, "python"))

from otio_sync_core.state_projection import (
    diff_states,
    normalize_clip_name,
    project_state,
)

RECORDING = os.path.join(
    project_root, "sync_test", "recordings", "reorder.jsonl"
)


def _load_first_snapshot_payload(path):
    """Return the ``command.payload`` of the first STATE_SNAPSHOT in *path*."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            p = row.get("payload", {}).get("payload", {})
            if (p.get("command_schema") == "LiveSession.1"
                    and p.get("command", {}).get("event") == "STATE_SNAPSHOT"):
                return p["command"]["payload"]
    raise AssertionError(f"No STATE_SNAPSHOT found in {path}")


class TestStateProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = _load_first_snapshot_payload(RECORDING)

    def test_projection_captures_structure(self):
        proj = project_state(self.payload)
        # The reorder recording has three timelines, but one is a single-clip
        # (clip_timeline_for) view timeline that the projection excludes → 2.
        self.assertEqual(len(proj["timelines"]), 2)
        self.assertIsNotNone(proj["frame"])
        # Each timeline projects ordered clip entries with names.
        for tl in proj["timelines"].values():
            self.assertIn("tracks", tl)

    def test_identical_states_match(self):
        a = project_state(self.payload)
        b = project_state(copy.deepcopy(self.payload))
        self.assertEqual(diff_states(a, b), [])

    def test_dropped_representation_fields_do_not_mismatch(self):
        """Changing media URLs / color metadata must not produce a diff."""
        mutated = copy.deepcopy(self.payload)
        for tl in mutated["timelines"].values():
            tl.setdefault("metadata", {})["color"] = {"working_space": "ocio:ACEScg"}
            for track in tl.get("tracks", {}).get("children", []):
                for clip in track.get("children", []):
                    refs = clip.get("media_references", {})
                    for ref in refs.values():
                        if isinstance(ref, dict):
                            ref["target_url"] = "file:///somewhere/else/x.mov"
                    clip["available_range"] = {"bogus": True}
        a = project_state(self.payload)
        b = project_state(mutated)
        self.assertEqual(diff_states(a, b), [])

    def test_reordered_clips_mismatch(self):
        mutated = copy.deepcopy(self.payload)
        # Reverse the first non-empty track of the first timeline.
        first_tl = next(iter(mutated["timelines"].values()))
        for track in first_tl.get("tracks", {}).get("children", []):
            kids = track.get("children", [])
            if len(kids) >= 2:
                track["children"] = list(reversed(kids))
                break
        a = project_state(self.payload)
        b = project_state(mutated)
        diff = diff_states(a, b)
        self.assertTrue(any("reorder" in m for m in diff), diff)

    def test_missing_clip_reported(self):
        mutated = copy.deepcopy(self.payload)
        first_tl = next(iter(mutated["timelines"].values()))
        for track in first_tl.get("tracks", {}).get("children", []):
            kids = track.get("children", [])
            # Drop a clip that has a sync guid so it is GUID-trackable.
            for i, c in enumerate(kids):
                if c.get("metadata", {}).get("sync", {}).get("guid"):
                    del kids[i]
                    break
            else:
                continue
            break
        a = project_state(self.payload)
        b = project_state(mutated)
        diff = diff_states(a, b)
        self.assertTrue(any("missing clip" in m for m in diff), diff)

    def test_unasserted_frame_is_skipped(self):
        a = project_state(self.payload)
        b = copy.deepcopy(a)
        b["frame"] = None
        # Even with a wildly different (but unasserted) frame, no mismatch.
        self.assertFalse(any("frame" in m for m in diff_states(a, b)))

    def test_frame_within_tolerance(self):
        a = project_state(self.payload)
        b = copy.deepcopy(a)
        b["frame"] = float(a["frame"]) + 3
        self.assertEqual(diff_states(a, b, frame_tolerance=5), [])
        far = copy.deepcopy(a)
        far["frame"] = float(a["frame"]) + 50
        self.assertTrue(any("frame" in m for m in diff_states(a, far, frame_tolerance=5)))

    def test_normalize_clip_name(self):
        self.assertEqual(normalize_clip_name("Default Sequence"), "default")
        self.assertEqual(normalize_clip_name(None), "")


if __name__ == "__main__":
    unittest.main()
