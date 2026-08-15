#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for poll_deleted_playlists' blocking identity reads.

Property under test: the deletion poll must not block the poll thread on an
actor that is mid-teardown, and a read that times out must never be mistaken
for evidence of deletion.

Observed 2026-08-14 21:11:34: this pass took 58.2 s.  The poll thread runs its
passes in series, so ``poll_new_playlists`` could not run either, and a sequence
created on that peer during the stall went undetected for about a minute.

Same interpreter requirements as test_sequence_reconciliation_convergence.py —
see its docstring.
"""
import os
import sys
import types

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402

_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import structure_sync  # noqa: E402

StructureSyncController = structure_sync.StructureSyncController


class FakeConnection:
    """Records the timeout in force for each read, so a test can assert bounding."""

    def __init__(self) -> None:
        self.default_timeout_ms = 100_000  # xStudio's real default
        self.timeouts_seen: list = []


class FakeNetwork:
    def __init__(self) -> None:
        self.sent: list = []

    def send_payload(self, payload: dict) -> None:
        self.sent.append(payload.get("payload", {}).get("command", {}).get("event"))


class DeadActor:
    """An actor whose identity read blocks — the case the poll is looking for.

    Raises rather than sleeping: the real read blocks inside a C++ dequeue for
    the connection's timeout and then raises, and it is the raise-vs-return that
    the calling code has to get right.
    """

    def __init__(self, connection) -> None:
        self._connection = connection

    @property
    def uuid(self):
        self._connection.timeouts_seen.append(self._connection.default_timeout_ms)
        raise TimeoutError("Dequeue timeout")


class LiveActor:
    def __init__(self, uuid: str) -> None:
        self.uuid = uuid
        self.containers: list = []


class FakeSession:
    def __init__(self, playlists) -> None:
        self.playlists = playlists


class FakePlugin:
    def __init__(self, playlists) -> None:
        self.manager = SyncManager(
            session_id="test", self_guid="peer-a", network=FakeNetwork()
        )
        self.manager.status = STATE_SYNCED
        self.connection = FakeConnection()
        self.connection.api = types.SimpleNamespace(session=FakeSession(playlists))
        self._sync_playlists: dict = {}

    def claim_lease(self, channel: str) -> None:
        self.manager.claim_category(channel)

    def detach_event_group_handler(self, key, label: str) -> None:
        pass


def test_a_timed_out_identity_read_is_not_treated_as_a_deletion():
    """A read that did not answer is not a peer saying "deleted".

    Broadcasting REMOVE_TIMELINE off a failed read would delete a live timeline
    on every peer — far worse than noticing a real deletion one pass late.
    """
    live = LiveActor("live-playlist-uuid")
    plugin = FakePlugin([live])
    structure = StructureSyncController(plugin)
    plugin._sync_playlists["tl-guid"] = (live, DeadActor(plugin.connection))

    structure.poll_deleted_playlists()

    assert "REMOVE_TIMELINE" not in plugin.manager.network.sent
    assert "tl-guid" in plugin._sync_playlists, "the entry must survive to be re-checked"


def test_the_identity_read_is_bounded():
    """Unbounded, this costs the connection's 100 s default per dead actor."""
    live = LiveActor("live-playlist-uuid")
    plugin = FakePlugin([live])
    structure = StructureSyncController(plugin)
    plugin._sync_playlists["tl-guid"] = (live, DeadActor(plugin.connection))

    structure.poll_deleted_playlists()

    assert plugin.connection.timeouts_seen, "the identity read never ran"
    assert all(t <= structure_sync._IDENTITY_TIMEOUT_MS for t in plugin.connection.timeouts_seen), (
        f"identity reads ran at {plugin.connection.timeouts_seen} ms, not bounded to"
        f" {structure_sync._IDENTITY_TIMEOUT_MS} ms"
    )
    assert plugin.connection.default_timeout_ms == 100_000, "the bound must be restored"


def test_a_genuinely_absent_timeline_is_still_removed():
    """The sibling check: bounding must not stop real deletions being noticed."""
    import opentimelineio as otio

    live = LiveActor("live-playlist-uuid")
    plugin = FakePlugin([live])
    structure = StructureSyncController(plugin)
    # Registered so the removal has something real to broadcast about.
    tl = otio.schema.Timeline(name="Gone")
    plugin.manager.register_timeline(tl)
    tl_guid = tl.metadata["sync"]["guid"]
    plugin.manager.network.sent.clear()
    plugin._sync_playlists[tl_guid] = (LiveActor("gone-playlist-uuid"), None)

    structure.poll_deleted_playlists()

    assert "REMOVE_TIMELINE" in plugin.manager.network.sent
    assert tl_guid not in plugin._sync_playlists


if __name__ == "__main__":
    test_a_timed_out_identity_read_is_not_treated_as_a_deletion()
    test_the_identity_read_is_bounded()
    test_a_genuinely_absent_timeline_is_still_removed()
    print("PASS")
