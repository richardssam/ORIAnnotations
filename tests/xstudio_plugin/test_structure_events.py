#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Coverage for the structure-events change: event-driven structure discovery.

Property under test, end to end: an xStudio structural event (add_playlist_atom
/ create_timeline_atom / rename_container_atom / remove_container_atom) never
touches the SyncManager or publishes on the callback thread it arrives on
(design D3) — it marks state and enqueues; the poll thread does the rest,
converging on the same publish calls the interval poll already makes
(design D1). The poll itself is untouched and remains the backstop (design D2).

Same interpreter requirements as test_deleted_playlist_poll.py — run via
run_tests_xstudio.sh (needs real ``xstudio.core`` atom types for isinstance
correctness in ``on_structure_event``).
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
from xstudio.core import (  # noqa: E402
    event_atom, add_playlist_atom, create_timeline_atom,
    rename_container_atom, remove_container_atom, Uuid,
)

StructureSyncController = structure_sync.StructureSyncController


class FakeNetwork:
    def __init__(self) -> None:
        self.sent: list = []

    def send_payload(self, payload: dict) -> None:
        self.sent.append(payload.get("payload", {}).get("command", {}).get("event"))


class FakeConnection:
    def __init__(self) -> None:
        self.default_timeout_ms = 100_000


class LiveActor:
    """Stand-in for a Playlist/Timeline actor: exposes only ``.uuid``."""

    def __init__(self, uuid: str, containers=None, name: str = "actor") -> None:
        self.uuid = uuid
        self.containers = containers or []
        self.name = name


class FakeSession:
    def __init__(self, playlists) -> None:
        self.playlists = playlists


class ManagerTouchGuard:
    """Wraps a real SyncManager but raises if any attribute is touched.

    Used to prove a handler never reaches the SyncManager at all (task 4.6) —
    stricter than merely asserting no broadcast, since it catches a stray
    read too.
    """

    def __getattr__(self, name):
        raise AssertionError(f"handler touched SyncManager.{name} — it must not")


class FakePlugin:
    def __init__(self, playlists=None, manager=None) -> None:
        self.manager = manager if manager is not None else _synced_manager()
        self.connection = FakeConnection()
        self.connection.api = types.SimpleNamespace(session=FakeSession(playlists or []))
        self._sync_playlists: dict = {}
        self.joins: list = []
        self._cmd_queue = _FakeQueue()

    def claim_lease(self, channel: str) -> None:
        self.manager.claim_category(channel)

    def detach_event_group_handler(self, key, label: str) -> None:
        pass

    def join_event_group(self, obj, label: str, cb):
        key = f"{label}:{id(obj)}"
        self.joins.append((obj, label))
        return key

    def _on_structure_event(self, event) -> None:
        pass


class _FakeQueue:
    """Minimal stand-in for queue.Queue — just records puts."""

    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


def _synced_manager() -> SyncManager:
    mgr = SyncManager(session_id="test", self_guid="peer-a", network=FakeNetwork())
    mgr.status = STATE_SYNCED
    return mgr


def _event(payload, *rest):
    return (event_atom(), payload) + rest


# ---------------------------------------------------------------------------
# 2.3/2.4/2.5 — the dirty set: dedupe, no-op on resolved, persist on unreadable
# ---------------------------------------------------------------------------


def test_n_marks_for_one_container_cost_one_pass():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    calls = {"new": 0, "rename": 0, "delete": 0}
    structure.poll_new_playlists = lambda: calls.__setitem__("new", calls["new"] + 1)
    structure.poll_playlist_renames = lambda: calls.__setitem__("rename", calls["rename"] + 1)
    structure.poll_deleted_playlists = lambda: calls.__setitem__("delete", calls["delete"] + 1)
    structure._dirty_mark_resolved = lambda u: True  # resolves immediately

    for _ in range(5):
        structure.mark_container_dirty("same-uuid")
    structure.consume_dirty_marks()

    assert calls == {"new": 1, "rename": 1, "delete": 1}
    assert structure._dirty_containers == set()


def test_already_published_mark_produces_no_second_pass():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    pass_calls = []
    structure.poll_new_playlists = lambda: pass_calls.append("new")
    structure.poll_playlist_renames = lambda: None
    structure.poll_deleted_playlists = lambda: None
    structure._dirty_mark_resolved = lambda u: True

    structure.mark_container_dirty("already-published-uuid")
    structure.consume_dirty_marks()
    # A second consume with nothing newly marked must not run the passes again.
    structure.consume_dirty_marks()

    assert pass_calls == ["new"]


def test_unreadable_mark_persists_and_does_not_block_others():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    structure.poll_new_playlists = lambda: None
    structure.poll_playlist_renames = lambda: None
    structure.poll_deleted_playlists = lambda: None
    # "unreadable" never resolves; "readable" resolves on the first check.
    structure._dirty_mark_resolved = lambda u: u == "readable-uuid"

    structure.mark_container_dirty("unreadable-uuid")
    structure.mark_container_dirty("readable-uuid")
    structure.consume_dirty_marks()

    assert structure._dirty_containers == {"unreadable-uuid"}

    # Retried on a later call (e.g. the next backstop tick) without needing a
    # fresh mark.
    structure._dirty_mark_resolved = lambda u: True
    structure._reconcile_dirty_marks()
    assert structure._dirty_containers == set()


# ---------------------------------------------------------------------------
# 3.7 — joining an already-joined playlist is safe
# ---------------------------------------------------------------------------


def test_join_known_playlist_groups_is_idempotent_per_playlist():
    pl = LiveActor("pl-uuid")
    plugin = FakePlugin(playlists=[pl])
    structure = StructureSyncController(plugin)

    structure.join_known_playlist_groups()
    structure.join_known_playlist_groups()

    # join_event_group's own dedup is exercised (not re-tested here — see
    # ORISyncPlugin.join_event_group), but the call site must not skip a
    # playlist it has already joined, either: it always re-attempts (task
    # 3.5's self-heal), and the fake records both attempts safely.
    assert plugin.joins.count((pl, "playlist-structure")) == 2


# ---------------------------------------------------------------------------
# 4.6/4.7 — handlers never touch the manager and never publish inline
# ---------------------------------------------------------------------------


def test_creation_event_handler_never_touches_manager():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)

    ua = types.SimpleNamespace(uuid=Uuid(), actor=None)
    structure.on_structure_event(_event(create_timeline_atom(), ua))

    assert plugin._cmd_queue.items == [("structure_dirty", {})]
    assert "same-uuid" not in structure._dirty_containers  # sanity: real uuid used
    assert len(structure._dirty_containers) == 1


def test_rename_event_handler_never_touches_manager_and_enqueues_only():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)

    u = Uuid()
    structure.on_structure_event(_event(rename_container_atom(), u, "New Name"))

    assert plugin._cmd_queue.items == [
        ("structure_renamed", {"container_uuid": str(u), "new_name": "New Name"})
    ]


def test_remove_event_handler_never_touches_manager_and_enqueues_only():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)

    u = Uuid()
    structure.on_structure_event(_event(remove_container_atom(), u))

    assert plugin._cmd_queue.items == [
        ("structure_removed", {"container_uuid": str(u)})
    ]


def test_add_playlist_event_joins_new_group_and_enqueues_only():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)

    ua = types.SimpleNamespace(uuid=Uuid(), actor=object())
    structure.on_structure_event(_event(add_playlist_atom(), ua))

    assert plugin._cmd_queue.items == [("structure_dirty", {})]
    assert plugin.joins and plugin.joins[0][1] == "playlist-structure"


def test_unrecognised_message_is_ignored_cheaply():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)

    class SomeOtherAtom:
        pass

    structure.on_structure_event(_event(SomeOtherAtom(), "whatever"))

    assert plugin._cmd_queue.items == []
    assert structure._dirty_containers == set()


# ---------------------------------------------------------------------------
# 6.1/6.4 — removal resolves directly from the event's container uuid
# ---------------------------------------------------------------------------


def test_removal_resolves_directly_without_reading_the_removed_actor():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    live_playlist = LiveActor("pl-actor-uuid")
    structure._container_uuid_to_tl_guid["container-uuid-123"] = "tl-guid-abc"
    plugin._sync_playlists["tl-guid-abc"] = (live_playlist, None)
    plugin.manager.register_timeline(__import__("opentimelineio").schema.Timeline(name="X"))
    # Re-key the registered timeline under our chosen tl_guid for the test.
    tl = list(plugin.manager.timelines.values())[0]
    real_guid = tl.metadata["sync"]["guid"]
    plugin.manager.timelines["tl-guid-abc"] = plugin.manager.timelines.pop(real_guid)
    plugin.manager.network.sent.clear()

    resolved = structure._apply_removal_by_container_uuid("container-uuid-123")

    assert resolved is True
    assert "REMOVE_TIMELINE" in plugin.manager.network.sent
    assert "tl-guid-abc" not in plugin._sync_playlists
    assert "container-uuid-123" not in structure._container_uuid_to_tl_guid


def test_removal_for_an_untracked_container_falls_through():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)

    resolved = structure._apply_removal_by_container_uuid("never-seen-uuid")

    assert resolved is False
    assert plugin.manager.network.sent == []


# ---------------------------------------------------------------------------
# 7.1/7.2/7.3 — echo suppression
# ---------------------------------------------------------------------------


def test_event_during_suppression_window_is_dropped():
    import time

    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)
    structure._structural_mutation_suppress_until = time.monotonic() + 5.0

    ua = types.SimpleNamespace(uuid=Uuid(), actor=None)
    structure.on_structure_event(_event(create_timeline_atom(), ua))

    assert plugin._cmd_queue.items == []
    assert structure._dirty_containers == set()


def test_event_after_suppression_window_is_processed():
    plugin = FakePlugin()
    plugin.manager = ManagerTouchGuard()
    structure = StructureSyncController(plugin)
    structure._structural_mutation_suppress_until = 0.0  # already expired

    ua = types.SimpleNamespace(uuid=Uuid(), actor=None)
    structure.on_structure_event(_event(create_timeline_atom(), ua))

    assert plugin._cmd_queue.items == [("structure_dirty", {})]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
