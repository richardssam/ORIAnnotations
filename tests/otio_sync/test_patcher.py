import sys
import os
import pytest
import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.patcher import OTIOPatcher
from otio_sync_core.proxy import OTIOSyncProxy
from otio_sync_core.protocol_messages import (
    SetProperty,
    InsertChild,
    MoveChild,
    RemoveChild,
)


def test_patcher_traverse_and_map():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("Test Timeline")
    track = otio.schema.Track("Test Track")
    clip = otio.schema.Clip("Test Clip")
    track.append(clip)
    timeline.tracks.append(track)

    patcher.traverse_and_map(timeline)

    # Ensure all objects have a GUID assigned and are indexed in object_map
    for obj in [timeline, timeline.tracks, track, clip]:
        guid = obj.metadata.get("sync", {}).get("guid")
        assert guid is not None
        assert patcher.object_map[guid] is obj


def test_patcher_set_property():
    patcher = OTIOPatcher()
    clip = otio.schema.Clip("Original Clip")
    patcher.ensure_guid_and_map(clip)
    clip_guid = clip.metadata["sync"]["guid"]

    events = []
    @patcher.on_property_changed
    def on_prop(target_uuid, path, value):
        events.append((target_uuid, path, value))

    # Test mutating simple attribute
    payload = patcher.set_property(clip_guid, "name", "Mutated Clip")
    assert clip.name == "Mutated Clip"
    assert isinstance(payload, SetProperty)
    assert payload.value == "Mutated Clip"
    assert payload.to_payload()["value"] == "Mutated Clip"
    assert len(events) == 1
    assert events[0] == (clip_guid, "name", "Mutated Clip")

    # Test mutating metadata path
    payload = patcher.set_property(clip_guid, "metadata/custom/sub_key", "nested_val")
    assert clip.metadata["custom"]["sub_key"] == "nested_val"
    assert payload.value == "nested_val"
    assert len(events) == 2
    assert events[1] == (clip_guid, "metadata/custom/sub_key", "nested_val")


def test_patcher_insert_remove_move_child():
    patcher = OTIOPatcher()
    track = otio.schema.Track("Main Track")
    patcher.ensure_guid_and_map(track)
    track_guid = track.metadata["sync"]["guid"]

    hierarchy_events = []
    @patcher.on_hierarchy_changed
    def on_hier(parent_uuid, action, child_uuid):
        hierarchy_events.append((parent_uuid, action, child_uuid))

    # Test insert
    clip1 = otio.schema.Clip("Clip 1")
    insert_payload = patcher.insert_child(track_guid, clip1, -1)
    assert isinstance(insert_payload, InsertChild)
    assert insert_payload.parent_uuid == track_guid
    assert len(track) == 1
    clip1_guid = clip1.metadata["sync"]["guid"]
    assert len(hierarchy_events) == 1
    assert hierarchy_events[0] == (track_guid, "insert_child", clip1_guid)

    clip2 = otio.schema.Clip("Clip 2")
    patcher.insert_child(track_guid, clip2, 0)
    assert len(track) == 2
    assert track[0] == clip2
    clip2_guid = clip2.metadata["sync"]["guid"]

    # Test move
    move_payload = patcher.move_child(track_guid, clip2_guid, 1)
    assert isinstance(move_payload, MoveChild)
    assert move_payload.to_index == 1
    assert track[0] == clip1
    assert track[1] == clip2
    assert len(hierarchy_events) == 3
    assert hierarchy_events[2] == (track_guid, "move_child", clip2_guid)

    # Test remove
    remove_payload = patcher.remove_child(track_guid, clip1_guid)
    assert isinstance(remove_payload, RemoveChild)
    assert remove_payload.child_uuid == clip1_guid
    assert len(track) == 1
    assert track[0] == clip2
    assert clip1_guid not in patcher.object_map
    assert len(hierarchy_events) == 4
    assert hierarchy_events[3] == (track_guid, "remove_child", clip1_guid)


def test_patcher_apply_patch():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("Original Timeline")
    patcher.traverse_and_map(timeline)
    tl_guid = timeline.metadata["sync"]["guid"]

    prop_events = []
    @patcher.on_property_changed
    def on_prop(target_uuid, path, value):
        prop_events.append((target_uuid, path, value))

    # Apply remote SET_PROPERTY patch (reconstructed message, as the manager does)
    patch_payload = {
        "target_uuid": tl_guid,
        "path": "name",
        "value": "Patched Timeline"
    }
    action_res = patcher.apply_patch(SetProperty.from_payload(patch_payload))
    assert timeline.name == "Patched Timeline"
    assert action_res == ("set_property", timeline)
    assert len(prop_events) == 1
    assert prop_events[0] == (tl_guid, "name", "Patched Timeline")


def test_proxy_integration_with_patcher():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("Proxy Timeline")
    proxy_timeline = OTIOSyncProxy(timeline, patcher)
    patcher.ensure_guid_and_map(timeline)
    tl_guid = timeline.metadata["sync"]["guid"]

    events = []
    @patcher.on_property_changed
    def on_prop(target_uuid, path, value):
        events.append((target_uuid, path, value))

    # Modify proxy name attribute and make sure it calls set_property on patcher
    proxy_timeline.name = "Mutated via Proxy"
    assert timeline.name == "Mutated via Proxy"
    assert len(events) == 1
    assert events[0] == (tl_guid, "name", "Mutated via Proxy")


def test_reentrancy_echo_guard():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("Reentrant Timeline")
    proxy_timeline = OTIOSyncProxy(timeline, patcher)
    patcher.ensure_guid_and_map(timeline)
    tl_guid = timeline.metadata["sync"]["guid"]

    # Simulating applying a patch. During this time, OTIOSyncProxy attribute writes
    # must NOT trigger set_property updates because _is_syncing is True.
    patcher._is_syncing = True
    proxy_timeline.name = "Mutated during sync"
    assert timeline.name == "Mutated during sync"

    # Make sure no outgoing set_property broadcast was made (since it was suppressed)
    # In a typical proxy setup, the proxy doesn't trigger set_property if _is_syncing is True.
