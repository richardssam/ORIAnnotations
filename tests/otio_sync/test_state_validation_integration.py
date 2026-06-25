"""Integration test for the structural state-checkpoint flow.

Deterministically exercises the record-side derivation and the replay-side
validation together (no live apps): a recording -> state checkpoints ->
validate a matching client (pass) and a desynced client (fail).  This is the
runnable stand-in for the live ``--periodic-state`` re-record described in the
change's task 6.1; the live run additionally needs RV/xStudio + a broker.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(project_root, "python"))
sys.path.insert(0, os.path.join(project_root, "sync_test", "python"))
sys.path.insert(0, project_root)

from sync_test.runner import TestRunner as _TestRunner  # aliased: avoid pytest collection
from sync_test.runner import derive_state_checkpoints

RECORDING = os.path.join(
    project_root, "sync_test", "recordings", "reorder.jsonl"
)


def _recorded_snapshot_payload(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            p = row.get("payload", {}).get("payload", {})
            if (p.get("command_schema") == "LiveSession.1"
                    and p.get("command", {}).get("event") == "STATE_SNAPSHOT"):
                return p["command"]["payload"]
    raise AssertionError("no snapshot in recording")


class TestStateValidationIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bypass config loading; we only exercise the validation methods.
        cls.runner = _TestRunner.__new__(_TestRunner)
        cls.checkpoints = derive_state_checkpoints(RECORDING)
        cls.client = _recorded_snapshot_payload(RECORDING)

    def test_checkpoints_derived(self):
        self.assertEqual(len(self.checkpoints), 1)
        # 3 timelines in the snapshot, but one is a single-clip view timeline the
        # projection excludes → 2 comparable sequence timelines.
        self.assertEqual(len(self.checkpoints[0]["expected"]["timelines"]), 2)

    def test_matching_client_passes_oracle(self):
        ok, msg = self.runner.validate_state_checkpoint(
            [self.client], ["openrv"], self.checkpoints[0]
        )
        self.assertTrue(ok, msg)

    def test_desynced_client_fails_oracle(self):
        """A client missing a clip (e.g. a dropped INSERT_CHILD) must fail."""
        bad = copy.deepcopy(self.client)
        first_tl = next(iter(bad["timelines"].values()))
        for track in first_tl.get("tracks", {}).get("children", []):
            kids = track.get("children", [])
            for i, c in enumerate(kids):
                if c.get("metadata", {}).get("sync", {}).get("guid"):
                    del kids[i]
                    break
            else:
                continue
            break
        ok, msg = self.runner.validate_state_checkpoint(
            [bad], ["openrv"], self.checkpoints[0]
        )
        self.assertFalse(ok)
        self.assertIn("missing clip", msg)

    def test_error_client_skipped_not_failed(self):
        ok, msg = self.runner.validate_state_checkpoint(
            [{"error": "full_state not supported"}], ["legacy"], self.checkpoints[0]
        )
        self.assertTrue(ok)
        self.assertIn("no full-state-capable apps", msg)

    def test_consensus_detects_divergence(self):
        bad = copy.deepcopy(self.client)
        first_tl = next(iter(bad["timelines"].values()))
        for track in first_tl.get("tracks", {}).get("children", []):
            kids = track.get("children", [])
            if len(kids) >= 2:
                track["children"] = list(reversed(kids))
                break
        ok, msg = self.runner.compare_full_states(
            [self.client, bad], ["openrv", "xstudio"]
        )
        self.assertFalse(ok)
        self.assertIn("consensus", msg.lower())


if __name__ == "__main__":
    unittest.main()
