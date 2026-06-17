"""Tests for the typed protocol message layer and registry dispatch."""

import sys
import os
import json

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import SyncManager, STATE_SYNCED, STATE_DISCOVERING
from otio_sync_core import protocol_messages as pm


class FakeNetwork:
    """Captures sent envelopes and replays injected ones (SyncNetworkProtocol)."""

    def __init__(self):
        self.sent = []
        self._inbox = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        out, self._inbox = self._inbox, []
        return out

    def stop(self):
        pass


def _make_synced_manager():
    net = FakeNetwork()
    mgr = SyncManager(session_id="s", self_guid="self-guid", network=net)
    mgr.status = STATE_SYNCED
    return mgr, net


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_has_all_families():
    reg = pm.registered_messages()
    assert reg[("LiveSession.1", "WHO_IS_MASTER")] is pm.WhoIsMaster
    assert reg[("OTIO_SESSION_1.0", "INSERT_CHILD")] is pm.InsertChild
    assert reg[("PLAYBACK_SETTINGS_1.0", "SET")] is pm.PlaybackSettingsSet
    assert pm.message_for("Annotation.1", "PARTIAL") is pm.PartialAnnotation
    assert pm.message_for("NOPE", "NOPE") is None


def test_message_roundtrip_identity():
    samples = {
        pm.WhoIsMaster: {"requester_guid": "g"},
        pm.SelectionSet: {"clip_guid": "c", "view_mode": "sequence", "sync_timestamp": 1.0},
        pm.InsertChild: {"parent_uuid": "p", "index": 2, "child_data": {"k": 1}, "sync_timestamp": 1.0},
        pm.MoveChild: {"parent_uuid": "p", "child_uuid": "c", "to_index": 3, "sync_timestamp": 1.0},
    }
    for cls, payload in samples.items():
        assert cls.from_payload(payload).to_payload() == payload


# ---------------------------------------------------------------------------
# 8.1 — broadcast envelopes match the message class and structure
# ---------------------------------------------------------------------------


def test_broadcast_selection_envelope():
    mgr, net = _make_synced_manager()
    mgr.broadcast_selection("clip-guid", view_mode="sequence")

    assert len(net.sent) == 1
    env = net.sent[0]
    # Envelope structure unchanged.
    assert env["session"] == "s"
    assert env["source_guid"] == "self-guid"
    assert env["payload"]["command_schema"] == pm.SelectionSet.SCHEMA == "SELECTION_1.0"
    assert env["payload"]["command"]["event"] == pm.SelectionSet.EVENT == "SET"
    body = env["payload"]["command"]["payload"]
    assert body["clip_guid"] == "clip-guid"
    assert body["view_mode"] == "sequence"
    # Envelope must be JSON-serializable (no message objects leak onto the wire).
    json.dumps(env)


def test_broadcast_playback_envelope_and_json_safe():
    mgr, net = _make_synced_manager()
    mgr.broadcast_playback_state(
        {"playing": True, "looping": False,
         "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 5.0, "rate": 24.0}},
        timeline_guid="tl-1",
    )
    env = net.sent[0]
    assert env["payload"]["command_schema"] == "PLAYBACK_SETTINGS_1.0"
    assert env["payload"]["command"]["event"] == "SET"
    body = env["payload"]["command"]["payload"]
    assert body["playing"] is True
    assert body["timeline_guid"] == "tl-1"
    assert "sync_timestamp" in body
    json.dumps(env)


def test_i_am_master_envelope_schema_preserved():
    mgr, net = _make_synced_manager()
    mgr.is_master = True
    mgr.broadcast_master_response()
    env = net.sent[0]
    assert env["payload"]["command"]["event"] == "I_AM_MASTER"
    # Legacy top-level schema key must still be emitted for older peers.
    assert env["schema"] == "SYNC_REVIEW_1.0"


# ---------------------------------------------------------------------------
# 8.2 — registry dispatch on receive
# ---------------------------------------------------------------------------


def _envelope(schema, event, payload, source="other-guid"):
    return {
        "session": "s",
        "source_guid": source,
        "payload": {"command_schema": schema, "command": {"event": event, "payload": payload}},
    }


def test_dispatch_known_selection_routes_to_handler():
    mgr, _ = _make_synced_manager()
    res = mgr.apply_patch(_envelope("SELECTION_1.0", "SET",
                                    {"clip_guid": "abc", "view_mode": "source"}))
    assert res is not None
    action, data = res
    assert action == "selection_changed"
    assert mgr.selected_clip_guid == "abc"


def test_dispatch_unknown_pair_ignored_safely():
    mgr, _ = _make_synced_manager()
    # Unknown schema/event must not raise and must return None.
    assert mgr.apply_patch(_envelope("MADE_UP_1.0", "NOPE", {"x": 1})) is None
    # Known schema but unknown event also ignored.
    assert mgr.apply_patch(_envelope("SELECTION_1.0", "BOGUS", {})) is None


def test_dispatch_self_message_discarded():
    mgr, _ = _make_synced_manager()
    res = mgr.apply_patch(_envelope("SELECTION_1.0", "SET", {"clip_guid": "x"},
                                    source="self-guid"))
    assert res is None


def test_dispatch_i_am_master_master_found():
    mgr, _ = _make_synced_manager()
    mgr.status = STATE_DISCOVERING
    res = mgr.apply_patch(_envelope("LiveSession.1", "I_AM_MASTER",
                                    {"master_guid": "the-master"}))
    assert res == ("master_found", "the-master")
    assert mgr.master_guid == "the-master"


def test_dispatch_insert_child_applies_via_patcher():
    mgr, _ = _make_synced_manager()
    track = otio.schema.Track("T")
    mgr.patcher.ensure_guid_and_map(track)
    track_guid = track.metadata["sync"]["guid"]

    clip = otio.schema.Clip("C")
    mgr.patcher.ensure_guid_and_map(clip)
    child_data = json.loads(otio.adapters.write_to_string(clip, "otio_json", indent=-1))

    res = mgr.apply_patch(_envelope(
        "OTIO_SESSION_1.0", "INSERT_CHILD",
        {"parent_uuid": track_guid, "index": -1, "child_data": child_data},
    ))
    assert res is not None
    action, obj = res
    assert action == "insert_child"
    assert len(track) == 1


# ---------------------------------------------------------------------------
# 8.3 — settings messages tolerate unknown/extra fields
# ---------------------------------------------------------------------------


def test_playback_settings_tolerates_extras_on_wire():
    payload = {"playing": True, "muted": True, "source_index": 3, "weird_key": [1, 2]}
    rt = pm.PlaybackSettingsSet.from_payload(payload).to_payload()
    assert rt == payload  # extras preserved exactly


def test_display_settings_tolerates_extras_through_dispatch():
    mgr, _ = _make_synced_manager()
    res = mgr.apply_patch(_envelope(
        "DISPLAY_SETTINGS_1.0", "SET",
        {"pan": [0.0, 0.0], "zoom": 2.0, "gamma": 2.2, "exposure": 0.5, "channel": "R"},
    ))
    assert res is not None
    action, data = res
    assert action == "display_settings"
    # Unknown 'gamma' must survive and not break handling.
    assert data["gamma"] == 2.2
    assert mgr.display_state["zoom"] == 2.0


# ---------------------------------------------------------------------------
# Typed OTIO protocol fields — messages own OTIO <-> wire conversion
# ---------------------------------------------------------------------------


def _otio_dict(obj):
    """Reference serialization matching the prior pre-serialized call sites."""
    return json.loads(otio.adapters.write_to_string(obj, "otio_json", indent=-1))


def test_otio_fields_serialize_in_to_payload_byte_identical():
    """Each OTIO-bearing message accepts an object and emits the prior wire bytes."""
    tl = otio.schema.Timeline(name="seq")
    tl.tracks.append(otio.schema.Track(name="V1"))
    clip = otio.schema.Clip(name="c")
    cmd = otio.schema.Marker(name="m")

    add = pm.AddTimeline(timeline_guid="g", timeline=tl, sync_timestamp=1.0)
    assert add.to_payload()["timeline"] == _otio_dict(tl)

    snap = pm.StateSnapshot(target_guid="t", timelines={"g": tl})
    assert snap.to_payload()["timelines"]["g"] == _otio_dict(tl)

    ins = pm.InsertChild(parent_uuid="p", child_data=clip, index=-1)
    assert ins.to_payload()["child_data"] == _otio_dict(clip)

    rep = pm.ReplaceAnnotationCommands(annotation_clip_guid="a", commands=[cmd])
    assert rep.to_payload()["commands"] == [_otio_dict(cmd)]

    # Envelopes built from these must stay JSON-serializable (no objects leak).
    for msg in (add, snap, ins, rep):
        json.dumps(msg.to_payload())


def test_from_payload_keeps_raw_and_as_otio_deserializes_lazily():
    """from_payload stores wire form unchanged; as_otio() does the conversion."""
    tl = otio.schema.Timeline(name="seq")
    payload = pm.AddTimeline(timeline_guid="g", timeline=tl).to_payload()

    recv = pm.AddTimeline.from_payload(payload)
    # Lazy: the field still holds the raw dict after reconstruction.
    assert isinstance(recv.timeline, dict)

    got = recv.as_otio()
    assert isinstance(got, otio.schema.Timeline)
    assert got.name == "seq"


def test_as_otio_passes_through_objects_and_collections():
    tl = otio.schema.Timeline(name="seq")
    clip = otio.schema.Clip(name="c")
    # Built locally (objects), as_otio() returns them as objects.
    assert pm.AddTimeline(timeline_guid="g", timeline=tl).as_otio() is tl
    snap = pm.StateSnapshot(target_guid="t", timelines={"g": tl})
    assert snap.as_otio()["g"] is tl
    rep = pm.ReplaceAnnotationCommands(annotation_clip_guid="a", commands=[clip])
    assert rep.as_otio() == [clip]


def test_add_timeline_known_guid_skips_deserialization(monkeypatch):
    """A duplicate AddTimeline for a known GUID must not pay as_otio() cost."""
    mgr, _ = _make_synced_manager()
    tl = otio.schema.Timeline(name="seq")
    mgr._timelines["known-guid"] = tl  # already held

    called = {"n": 0}
    orig = pm.AddTimeline.as_otio

    def spy(self):
        called["n"] += 1
        return orig(self)

    monkeypatch.setattr(pm.AddTimeline, "as_otio", spy)

    res = mgr.apply_patch(_envelope(
        "TIMELINE_1.0", "ADD_TIMELINE",
        {"timeline_guid": "known-guid", "timeline": _otio_dict(tl)},
    ))
    assert res is None
    assert called["n"] == 0  # guard rejected it before any deserialization


def test_insert_child_dispatch_uses_as_otio():
    """InsertChild applied through the patcher deserializes via the message."""
    mgr, _ = _make_synced_manager()
    track = otio.schema.Track("T")
    mgr.patcher.ensure_guid_and_map(track)
    track_guid = track.metadata["sync"]["guid"]

    clip = otio.schema.Clip("C")
    res = mgr.apply_patch(_envelope(
        "OTIO_SESSION_1.0", "INSERT_CHILD",
        {"parent_uuid": track_guid, "index": -1, "child_data": _otio_dict(clip)},
    ))
    assert res is not None
    action, obj = res
    assert action == "insert_child"
    assert len(track) == 1


def test_protocol_module_importable_without_opentimelineio():
    """The protocol_messages module must import with no OTIO installed, so the
    doc generator can enumerate classes and field metadata.  Loads the module
    file directly (bypassing the package __init__) under an import hook that
    blocks ``opentimelineio``, proving the module has no top-level OTIO import."""
    import subprocess

    here = os.path.dirname(__file__)
    mod_path = os.path.abspath(
        os.path.join(here, "../../python/otio_sync_core/protocol_messages.py")
    )
    code = (
        "import sys, builtins, importlib.util;"
        "_imp = builtins.__import__;"
        "builtins.__import__ = lambda n,*a,**k: "
        "(_ for _ in ()).throw(ImportError(n)) if n.split('.')[0]=='opentimelineio' "
        "else _imp(n,*a,**k);"
        f"spec = importlib.util.spec_from_file_location('pm_isolated', {mod_path!r});"
        "pm = importlib.util.module_from_spec(spec);"
        "sys.modules['pm_isolated'] = pm;"  # dataclass decorator needs this during exec
        "spec.loader.exec_module(pm);"
        "assert pm.registered_messages(), 'registry empty';"
        "assert pm.AddTimeline.doc_fields();"
        "print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout
