"""Tests for category-based broadcast authority.

Visibility is host-owned; position and annotation are not. The assertions here
are deliberately made **on the wire** — what a follower's envelope actually
carries — rather than on whether a plugin intended to send it. Intent was what
the retired time-window guards were checking, and it is what kept being wrong.
"""

import os
import sys

import opentimelineio as otio
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402
from otio_sync_core import protocol_messages as pm  # noqa: E402


class FakeNetwork:
    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    """Enforcement is on by default; the kill-switch test overrides it."""
    monkeypatch.delenv(authority.ENFORCEMENT_ENV, raising=False)


def _synced(guid, app, is_host):
    mgr = SyncManager(session_id="s", self_guid=guid, network=FakeNetwork(), app_name=app)
    mgr.status = STATE_SYNCED
    mgr.host_guid = guid if is_host else "someone-else"
    mgr.is_host = is_host
    return mgr


def _playback_payloads(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command_schema"] == pm.PlaybackSettingsSet.SCHEMA
    ]


def _view_state():
    return {
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 61.0, "rate": 24.0},
        "playback_mode": "loop",
        "view_mode": "source",
        "clip_guid": "clip-abc",
    }


# ---------------------------------------------------------------------------
# The category table
# ---------------------------------------------------------------------------


def test_playback_and_display_are_position():
    assert authority.category_for("broadcast_playback_state") == authority.POSITION
    assert authority.category_for("broadcast_display_state") == authority.POSITION


def test_annotation_broadcasts_are_annotation():
    for name in ("broadcast_add_annotation", "broadcast_partial_annotation",
                 "broadcast_replace_annotation_commands"):
        assert authority.category_for(name) == authority.ANNOTATION


def test_timeline_broadcasts_are_structure():
    for name in ("broadcast_add_timeline", "broadcast_remove_timeline",
                 "broadcast_replace_timeline", "broadcast_move_child"):
        assert authority.category_for(name) == authority.STRUCTURE


def test_session_plumbing_has_no_category():
    assert authority.category_for("broadcast_master_response") is None


def test_every_categorised_method_exists_on_the_manager():
    """The table is only useful if it names real methods."""
    for name in authority.BROADCAST_CATEGORIES:
        assert callable(getattr(SyncManager, name, None)), name


def test_visibility_and_position_field_groups_are_disjoint():
    assert not set(authority.VISIBILITY_FIELDS) & set(authority.POSITION_FIELDS)


# ---------------------------------------------------------------------------
# 3.2 — a follower's broadcasts never carry visibility, asserted on the wire
# ---------------------------------------------------------------------------


def test_follower_playback_carries_no_visibility_fields():
    rv = _synced("guid-rv", "openrv", is_host=False)

    status = rv.broadcast_playback_state(_view_state())

    assert status == authority.SUPPRESSED
    sent = _playback_payloads(rv)[-1]
    assert "view_mode" not in sent
    assert "clip_guid" not in sent


def test_follower_omits_the_whole_visibility_group_not_just_view_mode():
    """A clip_guid without a view_mode still asserts what to look at."""
    rv = _synced("guid-rv", "openrv", is_host=False)

    rv.broadcast_playback_state({
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 3.0, "rate": 24.0},
        "clip_guid": "clip-abc",
    })

    sent = _playback_payloads(rv)[-1]
    assert not authority.asserts_visibility(sent)


def test_host_playback_keeps_visibility_fields():
    xs = _synced("guid-xs", "xstudio", is_host=True)

    status = xs.broadcast_playback_state(_view_state())

    assert status == authority.SENT
    sent = _playback_payloads(xs)[-1]
    assert sent["view_mode"] == "source"
    assert sent["clip_guid"] == "clip-abc"


# ---------------------------------------------------------------------------
# 3.3 — a follower may still scrub, play/stop and annotate
# ---------------------------------------------------------------------------


def test_follower_position_survives_the_strip():
    rv = _synced("guid-rv", "openrv", is_host=False)

    rv.broadcast_playback_state(_view_state())

    sent = _playback_payloads(rv)[-1]
    assert sent["playing"] is True
    assert sent["current_time"]["value"] == 61.0
    assert sent["playback_mode"] == "loop"


def test_follower_position_only_message_is_sent_intact():
    rv = _synced("guid-rv", "openrv", is_host=False)

    status = rv.broadcast_playback_state({
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 12.0, "rate": 24.0},
        "playback_mode": "loop",
    })

    assert status == authority.SENT
    assert _playback_payloads(rv)[-1]["current_time"]["value"] == 12.0


def test_follower_may_broadcast_display_state():
    rv = _synced("guid-rv", "openrv", is_host=False)

    status = rv.broadcast_display_state({"channel": "R", "exposure": 1.0, "zoom": 1.0})

    assert status == authority.SENT
    assert any(
        e["payload"]["command_schema"] == pm.DisplaySettingsSet.SCHEMA
        for e in rv.network.sent
    )


def test_follower_may_broadcast_annotations():
    rv = _synced("guid-rv", "openrv", is_host=False)

    status = rv.broadcast_partial_annotation("clip-abc", 4.0, 24.0, [])

    assert status == authority.SENT
    assert any(
        e["payload"]["command_schema"] == pm.PartialAnnotation.SCHEMA
        for e in rv.network.sent
    )


def test_follower_may_broadcast_structure():
    rv = _synced("guid-rv", "openrv", is_host=False)
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": "tl-1"}
    rv.register_timeline(tl)

    assert rv.broadcast_add_timeline("tl-1") == authority.SENT


# ---------------------------------------------------------------------------
# 3.1 — the kill switch
# ---------------------------------------------------------------------------


def test_kill_switch_restores_symmetric_authority(monkeypatch):
    monkeypatch.setenv(authority.ENFORCEMENT_ENV, "0")
    rv = _synced("guid-rv", "openrv", is_host=False)

    status = rv.broadcast_playback_state(_view_state())

    assert status == authority.SENT
    assert _playback_payloads(rv)[-1]["view_mode"] == "source"


def test_kill_switch_is_read_per_call(monkeypatch):
    """Flipping it must take effect in a running session, not only at import."""
    rv = _synced("guid-rv", "openrv", is_host=False)

    monkeypatch.setenv(authority.ENFORCEMENT_ENV, "0")
    assert rv.broadcast_playback_state(_view_state()) == authority.SENT

    monkeypatch.setenv(authority.ENFORCEMENT_ENV, "1")
    assert rv.broadcast_playback_state(_view_state()) == authority.SUPPRESSED


def test_kill_switch_accepts_the_usual_falsey_spellings(monkeypatch):
    for value in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv(authority.ENFORCEMENT_ENV, value)
        assert authority.enforcement_enabled() is False, value


# ---------------------------------------------------------------------------
# D3 — the follower rule expressed as a predicate, shared by both plugins
# ---------------------------------------------------------------------------


def test_follower_may_not_read_a_local_transition_as_user_intent():
    rv = _synced("guid-rv", "openrv", is_host=False)

    assert rv.owns_visibility() is False


def test_host_may_read_a_local_transition_as_user_intent():
    xs = _synced("guid-xs", "xstudio", is_host=True)

    assert xs.owns_visibility() is True


def test_kill_switch_restores_local_intent_inference(monkeypatch):
    monkeypatch.setenv(authority.ENFORCEMENT_ENV, "0")
    rv = _synced("guid-rv", "openrv", is_host=False)

    assert rv.owns_visibility() is True


# ---------------------------------------------------------------------------
# Enforcement lives in one place
# ---------------------------------------------------------------------------


def test_strip_is_a_pure_function_of_the_field_group():
    state = {"playing": True, "view_mode": "source", "clip_guid": "c", "extra": 1}

    stripped = authority.strip_visibility_fields(state)

    assert stripped == {"playing": True, "extra": 1}
    assert "view_mode" in state, "must not mutate the caller's dict"


#: Plugin modules that build and send view-state broadcasts.
_BROADCASTING_MODULES = [
    "rvplugin/ori_sync/playback_sync.py",
    "xstudio_plugin/ori_sync/playback_sync.py",
    "rvplugin/ori_sync/display_sync.py",
    "xstudio_plugin/ori_sync/display_sync.py",
]


def test_no_plugin_gates_a_broadcast_on_being_host():
    """Authority is checked in core, so the two plugins cannot drift on it.

    The hosts have already drifted on hand-replicated protocol behaviour
    (discovery cadence, snapshot assembly), which is why the rule is that a
    plugin calls ``broadcast_*`` and reads the returned status — it never tests
    the role itself. The *local intent* branches are a separate question and go
    through ``manager.owns_visibility()``, one shared implementation, so they
    cannot drift either.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    for rel in _BROADCASTING_MODULES:
        path = os.path.join(repo_root, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert "is_host" not in source, (
            f"{rel} reads is_host directly. Broadcast authority belongs in "
            "SyncManager.broadcast_*; local intent belongs in "
            "SyncManager.owns_visibility()."
        )


def test_suppressed_broadcast_still_reaches_the_network():
    """Suppression removes fields; it does not drop the peer's position update."""
    rv = _synced("guid-rv", "openrv", is_host=False)

    rv.broadcast_playback_state(_view_state())

    assert len(_playback_payloads(rv)) == 1
