"""Integration tests: color metadata rides the existing sync infrastructure.

These confirm that the color-pipeline-sync capability needs **no** new protocol
message — color changes travel over the existing ``SetProperty`` path and over
the OTIO-bearing ``AddTimeline`` payload, and the existing echo guard suppresses
re-broadcast when applying a received change.
"""

import sys
import os

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import color
from otio_sync_core.patcher import OTIOPatcher
from otio_sync_core.protocol_messages import SetProperty, AddTimeline


def _mapped_clip(patcher, name="Clip"):
    clip = otio.schema.Clip(name)
    patcher.ensure_guid_and_map(clip)
    return clip, clip.metadata["sync"]["guid"]


# --- 2.1 clip color change broadcasts via SetProperty ----------------------


def test_clip_color_space_via_set_property():
    patcher = OTIOPatcher()
    clip, clip_guid = _mapped_clip(patcher)

    payload = patcher.set_property(
        clip_guid, "metadata/" + color.COLOR_SPACE, "ocio:ARRI LogC3"
    )

    assert isinstance(payload, SetProperty)  # existing message, not a new type
    assert payload.path == "metadata/color_space"
    assert clip.metadata[color.COLOR_SPACE] == "ocio:ARRI LogC3"
    assert color.read_color_space(clip) == "ocio:ARRI LogC3"


# --- 2.2 timeline color change relies on intermediate-dict creation --------


def test_timeline_color_group_via_set_property():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("TL")
    patcher.ensure_guid_and_map(timeline)
    tl_guid = timeline.metadata["sync"]["guid"]

    # The "color" group does not exist yet; the patcher must create it.
    patcher.set_property(
        tl_guid, "metadata/%s/%s" % (color.COLOR_GROUP, color.WORKING_SPACE), "ACEScg"
    )
    patcher.set_property(
        tl_guid,
        "metadata/%s/%s" % (color.COLOR_GROUP, color.OUTPUT_SPACE),
        "ocio:Rec.1886 Rec.709 - Display",
    )

    assert color.read_timeline_color(timeline) == {
        "working_space": "ACEScg",
        "output_space": "ocio:Rec.1886 Rec.709 - Display",
    }


# --- 2.3 color survives AddTimeline serialization round-trip ---------------


def test_color_survives_add_timeline_round_trip():
    timeline = otio.schema.Timeline("Reel")
    timeline.metadata[color.COLOR_GROUP] = {
        color.CONFIG: "ocio://studio-config-v3.0.0",
        color.WORKING_SPACE: "ACEScg",
    }
    clip = otio.schema.Clip("shot010")
    # Unknown vocabulary must round-trip verbatim (no translation).
    clip.metadata[color.COLOR_SPACE] = "resolve:DaVinci Wide Gamut Intermediate"
    track = otio.schema.Track()
    track.append(clip)
    timeline.tracks.append(track)

    msg = AddTimeline(timeline_guid="g", timeline=timeline)
    wire = msg.to_payload()
    received = AddTimeline.from_payload(wire)
    rebuilt = received.as_otio()

    assert color.read_timeline_color(rebuilt) == {
        "config": "ocio://studio-config-v3.0.0",
        "working_space": "ACEScg",
    }
    rebuilt_clip = rebuilt.tracks[0][0]
    assert color.read_color_space(rebuilt_clip) == "resolve:DaVinci Wide Gamut Intermediate"


# --- 2.4 applying a received color change does not echo --------------------


def test_applying_color_change_is_echo_guarded():
    patcher = OTIOPatcher()
    timeline = otio.schema.Timeline("Echo")
    patcher.ensure_guid_and_map(timeline)
    tl_guid = timeline.metadata["sync"]["guid"]

    # Model the manager's broadcast callback: it only re-broadcasts when not
    # applying a received patch (manager.py registers exactly this guard).
    broadcasts = []

    @patcher.on_property_changed
    def _broadcast(target_uuid, path, value):
        if not patcher._is_syncing:
            broadcasts.append((target_uuid, path, value))

    # Apply a received color SetProperty the way the manager does: flag on,
    # apply, flag off.
    msg = SetProperty.from_payload(
        {
            "target_uuid": tl_guid,
            "path": "metadata/%s/%s" % (color.COLOR_GROUP, color.WORKING_SPACE),
            "value": "ACEScg",
        }
    )
    patcher._is_syncing = True
    patcher.apply_patch(msg)
    patcher._is_syncing = False

    # The change applied locally, but nothing was re-broadcast (no echo).
    assert color.read_timeline_color(timeline) == {"working_space": "ACEScg"}
    assert broadcasts == []


# --- 5.4 color keys do not collide with existing metadata namespaces -------


def test_color_keys_distinct_from_reserved_namespaces():
    # The new keys must not clash with the namespaces the sync layer already
    # uses, nor with the separate annotation colorspace concept.
    reserved = {"sync", "annotation_commands", "annotated_clip_name", "clip_guid"}
    assert color.COLOR_GROUP not in reserved
    assert color.COLOR_SPACE not in reserved
    # Media/timeline color is a different concept from annotation color space.
    assert color.COLOR_SPACE != "ocio_annotation_color_space"
