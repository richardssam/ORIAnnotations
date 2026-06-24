#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CLI tool to convert a session recording (.jsonl) to an OTIO timeline."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Ensure we can import otio_sync_core
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "python"))

import opentimelineio as otio


class VisualSegment:
    """Represents a continuous visual segment in the recorded session."""

    def __init__(
        self,
        clip_guid: str,
        start_t: float,
        end_t: float,
        start_frame: float,
        fps: float,
        playing: bool,
    ) -> None:
        self.clip_guid = clip_guid
        self.start_t = start_t
        self.end_t = end_t
        self.start_frame = start_frame
        self.fps = fps
        self.playing = playing

    def __repr__(self) -> str:
        return (
            f"<VisualSegment clip={self.clip_guid} start_t={self.start_t} "
            f"end_t={self.end_t} start_frame={self.start_frame} playing={self.playing}>"
        )


def convert_recording(
    recording_path: str,
    output_path: str,
    target_fps: float = 24.0,
) -> None:
    """Convert a session recording to an OTIO timeline.

    :param recording_path: Path to the JSONL recording file.
    :param output_path: Path to save the converted OTIO timeline.
    :param target_fps: Target frame rate for the output timeline.
    """
    if not os.path.exists(recording_path):
        raise FileNotFoundError(f"Recording file not found: {recording_path}")

    with open(recording_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    timeline_map: dict[str, otio.schema.Timeline] = {}
    template_clips: dict[str, otio.schema.Clip] = {}

    active_timeline_guid = None
    active_clip_guid = None
    active_view_mode = "sequence"
    playing = False
    playhead_frame = 0.0
    fps = 24.0

    segments: list[VisualSegment] = []

    # Segment tracking state variables
    current_segment_clip_guid = None
    current_segment_playing = False
    current_segment_start_t = 0.0
    current_segment_playhead = otio.opentime.RationalTime(0.0, 24.0)

    def get_primary_video_track(tl: otio.schema.Timeline) -> otio.schema.Track | None:
        for track in tl.tracks:
            if track.kind == "Video" and track.name in ("Video Track", "Media", "tracks", "Video"):
                return track
        for track in tl.tracks:
            if track.kind == "Video" and track.name != "Dropped":
                return track
        for track in tl.tracks:
            if track.kind == "Video":
                return track
        return None

    def get_default_clip_guid(tl_guid: str | None) -> str | None:
        if not tl_guid or tl_guid not in timeline_map:
            return None
        tl = timeline_map[tl_guid]
        track = get_primary_video_track(tl)
        if track:
            for child in track:
                if isinstance(child, otio.schema.Clip):
                    if "sync" in child.metadata and "guid" in child.metadata["sync"]:
                        return child.metadata["sync"]["guid"]
        return None

    def get_clip_sequence_start_time(tl: otio.schema.Timeline, clip_guid: str) -> otio.opentime.RationalTime | None:
        track = get_primary_video_track(tl)
        if not track:
            return None
        current_time = otio.opentime.RationalTime(0.0, 24.0)
        for child in track:
            child_guid = None
            if "sync" in child.metadata and "guid" in child.metadata["sync"]:
                child_guid = child.metadata["sync"]["guid"]
            if child_guid == clip_guid:
                return current_time
            duration = child.duration()
            if current_time.rate != duration.rate:
                current_time = current_time.rescaled_to(duration.rate)
            current_time += duration
        return None

    def flush_segment(t: float) -> None:
        nonlocal current_segment_start_t, current_segment_playhead
        duration_t = t - current_segment_start_t
        if duration_t > 0.0 and current_segment_clip_guid is not None:
            local_start_frame = current_segment_playhead.value
            fps = current_segment_playhead.rate

            template_clip = template_clips.get(current_segment_clip_guid)
            if template_clip:
                offset = current_segment_playhead
                if active_view_mode == "sequence" and active_timeline_guid:
                    tl = timeline_map.get(active_timeline_guid)
                    if tl:
                        seq_start = get_clip_sequence_start_time(tl, current_segment_clip_guid)
                        if seq_start is not None:
                            p_head = current_segment_playhead
                            if p_head.rate != seq_start.rate:
                                p_head = p_head.rescaled_to(seq_start.rate)
                            offset = p_head - seq_start

                if template_clip.source_range:
                    clip_start = template_clip.source_range.start_time
                    clip_dur = template_clip.source_range.duration
                    if offset.rate != clip_start.rate:
                        offset = offset.rescaled_to(clip_start.rate)

                    zero_time = otio.opentime.RationalTime(0.0, clip_start.rate)
                    if offset < zero_time:
                        offset = zero_time
                    elif offset > clip_dur:
                        offset = clip_dur

                    local_start = clip_start + offset
                    local_start_frame = local_start.value
                    fps = local_start.rate

            segments.append(
                VisualSegment(
                    clip_guid=current_segment_clip_guid,
                    start_t=current_segment_start_t,
                    end_t=t,
                    start_frame=local_start_frame,
                    fps=fps,
                    playing=current_segment_playing,
                )
            )
        current_segment_start_t = t
        if current_segment_playing:
            delta_rt = otio.opentime.RationalTime(duration_t, 1.0).rescaled_to(current_segment_playhead.rate)
            current_segment_playhead += delta_rt

    # Parse JSONL lines sequentially
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            event = json.loads(line_str)
        except Exception:
            continue

        t = event.get("time_offset", 0.0)
        inner = event.get("payload", {})
        if "payload" in inner and isinstance(inner["payload"], dict) and "command_schema" in inner["payload"]:
            inner = inner["payload"]

        cmd = inner.get("command_schema")
        evt = inner.get("command", {}).get("event")
        payload_data = inner.get("command", {}).get("payload", {})

        # Flush visual state up to this event
        flush_segment(t)

        # Update timeline structures and registry
        if cmd == "LiveSession.1" and evt == "STATE_SNAPSHOT":
            timelines_dict = payload_data.get("timelines", {})
            for guid, tl_dict in timelines_dict.items():
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    timeline_map[guid] = tl
                    for clip in tl.find_clips():
                        if "sync" in clip.metadata and "guid" in clip.metadata["sync"]:
                            clip_uuid = clip.metadata["sync"]["guid"]
                            template_clips[clip_uuid] = clip
                except Exception as e:
                    import traceback
                    print(f"Error parsing timeline {guid}: {e}")
                    traceback.print_exc()
                    raise e
            active_timeline_guid = payload_data.get("active_timeline_guid")
            playback_state = payload_data.get("playback_state")
            if playback_state:
                playing = playback_state.get("playing", False)
                current_time = playback_state.get("current_time")
                if current_time:
                    playhead_frame = current_time.get("value", 0.0)
                    fps = current_time.get("rate", 24.0)

                # Fallback active clip if not set
                if not active_clip_guid:
                    active_clip_guid = get_default_clip_guid(active_timeline_guid)

                # Apply to segment state
                current_segment_playing = playing
                current_segment_playhead = otio.opentime.RationalTime(playhead_frame, fps)
                if active_clip_guid:
                    current_segment_clip_guid = active_clip_guid

        elif cmd == "TIMELINE_1.0" and evt == "ADD_TIMELINE":
            guid = payload_data.get("timeline_guid")
            tl_dict = payload_data.get("timeline")
            if guid and tl_dict:
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    timeline_map[guid] = tl
                    for clip in tl.find_clips():
                        if "sync" in clip.metadata and "guid" in clip.metadata["sync"]:
                            clip_uuid = clip.metadata["sync"]["guid"]
                            template_clips[clip_uuid] = clip
                except Exception:
                    pass

        elif cmd == "TIMELINE_1.0" and evt == "REPLACE_TIMELINE":
            guid = payload_data.get("timeline_guid")
            tl_dict = payload_data.get("timeline")
            if guid and tl_dict:
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    timeline_map[guid] = tl
                    for clip in tl.find_clips():
                        if "sync" in clip.metadata and "guid" in clip.metadata["sync"]:
                            clip_uuid = clip.metadata["sync"]["guid"]
                            template_clips[clip_uuid] = clip
                except Exception:
                    pass

        elif cmd == "PLAYBACK_SETTINGS_1.0" and evt == "SET":
            playing = payload_data.get("playing", False)
            current_time = payload_data.get("current_time")
            if current_time:
                playhead_frame = current_time.get("value", 0.0)
                fps = current_time.get("rate", 24.0)

            # Apply to segment state
            current_segment_playing = playing
            current_segment_playhead = otio.opentime.RationalTime(playhead_frame, fps)
            if not active_clip_guid:
                active_clip_guid = get_default_clip_guid(active_timeline_guid)
                current_segment_clip_guid = active_clip_guid

        elif cmd == "SELECTION_1.0" and evt == "SET":
            active_clip_guid = payload_data.get("clip_guid")
            current_segment_clip_guid = active_clip_guid
            if "view_mode" in payload_data:
                active_view_mode = payload_data["view_mode"]

            template_clip = template_clips.get(active_clip_guid)
            if template_clip and active_view_mode == "source":
                if template_clip.source_range:
                    current_segment_playhead = otio.opentime.RationalTime(0.0, template_clip.source_range.start_time.rate)

    # Final flush at end of file
    if lines:
        try:
            last_event = json.loads(lines[-1].strip())
            final_t = last_event.get("time_offset", current_segment_start_t)
            flush_segment(final_t)
        except Exception:
            pass

    # Reconstruct OTIO Timeline
    timeline = otio.schema.Timeline("Session Screen Recording")
    bg_track = otio.schema.Track("Background Media")

    for segment in segments:
        duration_sec = segment.end_t - segment.start_t
        if duration_sec <= 0.0:
            continue

        template_clip = template_clips.get(segment.clip_guid)
        if not template_clip:
            dummy_ref = otio.schema.MissingReference()
            template_clip = otio.schema.Clip(
                name=f"Missing Clip ({segment.clip_guid})",
                media_reference=dummy_ref,
            )

        new_clip = otio.schema.Clip(
            name=template_clip.name,
            media_reference=template_clip.media_reference,
        )

        media_start = otio.opentime.RationalTime(segment.start_frame, segment.fps)
        media_dur = otio.opentime.RationalTime(duration_sec * segment.fps, segment.fps)
        new_clip.source_range = otio.opentime.TimeRange(start_time=media_start, duration=media_dur)

        if not segment.playing:
            # Add freeze-frame TimeWarp effect
            freeze_effect = otio.schema.LinearTimeWarp(time_scalar=0.0)
            new_clip.effects.append(freeze_effect)

        bg_track.append(new_clip)

    timeline.tracks.append(bg_track)

    # Save to file
    otio.adapters.write_to_file(timeline, output_path)
    print(f"[*] Converted recording to timeline: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert sync session recording to an OTIO timeline."
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to input recording .jsonl",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Path to output timeline .otio",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=24.0,
        help="Target frame rate (default: 24.0)",
    )
    args = parser.parse_args()

    try:
        convert_recording(
            recording_path=args.input,
            output_path=args.output,
            target_fps=args.fps,
        )
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
