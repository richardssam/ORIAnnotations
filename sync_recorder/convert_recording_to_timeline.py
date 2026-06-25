#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CLI tool to convert a session recording (.jsonl) to an OTIO timeline."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Ensure we can import otio_sync_core and sibling folders
project_root = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "python"))

# Setup OTIO plugin manifest path
manifest_file = project_root / "otio_event_plugin" / "plugin_manifest.json"
if manifest_file.exists():
    manifest_path_str = str(manifest_file.resolve())
    existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if manifest_path_str not in existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            existing + os.pathsep + manifest_path_str if existing else manifest_path_str
        )

import opentimelineio as otio

# Force manifest reload to register SyncEvent schema plugin only if not already registered
try:
    otio.schema.schemadef.module_from_name('SyncEvent')
except Exception:
    try:
        import opentimelineio.plugins.manifest as otio_manifest
        otio_manifest._MANIFEST = None
        otio.schema.schemadef.module_from_name('SyncEvent')
    except Exception as e:
        sys.stderr.write(f"Warning: failed to force load SyncEvent schemadef: {e}\n")

from PIL import Image
from otio_sync_core.patcher import OTIOPatcher
from otio_sync_core.protocol_messages import ProtocolMessage
from sync_recorder.annotation_renderer import render_annotations



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


def resolve_local_path(target_url: str) -> str | None:
    """Resolve a target_url string into a local file system path."""
    if not target_url:
        return None
    path_str = target_url
    if path_str.startswith("file://localhost"):
        path_str = path_str[len("file://localhost"):]
    elif path_str.startswith("file://"):
        path_str = path_str[len("file://"):]
    if not path_str.startswith("/") and os.name != "nt":
        path_str = "/" + path_str
    return path_str


def get_media_resolution(clip: otio.schema.Clip) -> tuple[int, int]:
    """Determine width and height for a clip, reading from file if it is an image."""
    is_portrait = "portrait" in clip.name.lower()
    ref = clip.media_reference
    if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
        local_path = resolve_local_path(ref.target_url)
        if local_path and os.path.exists(local_path):
            if local_path.lower().endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
                try:
                    with Image.open(local_path) as img:
                        return img.size
                except Exception:
                    pass
            if "portrait" in local_path.lower():
                is_portrait = True
    return (1080, 1920) if is_portrait else (1920, 1080)


def get_commands_signature(cmds: list[Any] | None) -> tuple[Any, ...] | None:
    """Compute a stable signature identifying unique drawing states on a frame."""
    if not cmds:
        return None
    from otio_sync_core.manager import sync_event_schema
    sig = []
    for cmd in cmds:
        schema = sync_event_schema(cmd)
        uuid_val = getattr(cmd, "uuid", None)
        if uuid_val is None and isinstance(cmd, dict):
            uuid_val = cmd.get("uuid")

        text = getattr(cmd, "text", None)
        if text is None and isinstance(cmd, dict):
            text = cmd.get("text")

        rgba = getattr(cmd, "rgba", None)
        if rgba is None and isinstance(cmd, dict):
            rgba = cmd.get("rgba")
        rgba_tuple = tuple(rgba) if rgba else None

        position = getattr(cmd, "position", None)
        if position is None and isinstance(cmd, dict):
            position = cmd.get("position")
        position_tuple = tuple(position) if position else None

        sig.append((schema, uuid_val, text, rgba_tuple, position_tuple))
    return tuple(sig)


def get_commands_hash(cmds: list[Any] | None) -> str:
    """Compute a stable hash identifying unique drawing states on a frame."""
    import hashlib
    sig = get_commands_signature(cmds)
    if not sig:
        return "empty"
    sig_str = str(sig)
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()[:8]


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
    patcher = OTIOPatcher()
    template_clips: dict[str, otio.schema.Clip] = {}

    active_timeline_guid = None
    active_clip_guid = None
    active_view_mode = "sequence"
    playing = False
    playhead_frame = 0.0
    fps = 24.0

    segments: list[VisualSegment] = []
    clip_frame_offsets: dict[str, float] = {}
    partial_annotations: dict[str, dict[int, dict[str, Any]]] = {}


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

    annotation_histories = {}
    last_recorded_signatures = {}

    def update_annotation_histories(t_offset: float) -> None:
        committed_by_cg_frame = {}
        for tl in timeline_map.values():
            for clip in tl.find_clips():
                if "clip_guid" in clip.metadata and "annotation_commands" in clip.metadata:
                    cg = clip.metadata["clip_guid"]
                    if clip.source_range:
                        frame = int(clip.source_range.start_time.value)
                        commands = clip.metadata["annotation_commands"]
                        if commands:
                            frame_dict = committed_by_cg_frame.setdefault(cg, {}).setdefault(frame, {})
                            clip_strokes = {}
                            for cmd in commands:
                                uuid_val = cmd.get("uuid") if isinstance(cmd, dict) else getattr(cmd, "uuid", None)
                                if uuid_val:
                                    clip_strokes.setdefault(uuid_val, []).append(cmd)
                            for uuid_val, stroke_cmds in clip_strokes.items():
                                frame_dict[uuid_val] = stroke_cmds

        all_keys = set()
        for cg, frames in committed_by_cg_frame.items():
            for frame in frames:
                all_keys.add((cg, frame))
        for cg, frames in partial_annotations.items():
            for frame in frames:
                all_keys.add((cg, frame))

        for cg, frame in all_keys:
            strokes_dict = {}
            if cg in committed_by_cg_frame and frame in committed_by_cg_frame[cg]:
                strokes_dict.update(committed_by_cg_frame[cg][frame])

            if cg in partial_annotations and frame in partial_annotations[cg]:
                for uuid_val, stroke_cmds in partial_annotations[cg][frame].items():
                    if uuid_val not in strokes_dict:
                        strokes_dict[uuid_val] = stroke_cmds

            flat_cmds = []
            for stroke_cmds in strokes_dict.values():
                flat_cmds.extend(stroke_cmds)

            sig = get_commands_signature(flat_cmds)
            last_sig = last_recorded_signatures.get((cg, frame))
            if sig != last_sig:
                last_recorded_signatures[(cg, frame)] = sig
                annotation_histories.setdefault((cg, frame), []).append((t_offset, flat_cmds))

    def get_annotations_at_time(cg: str, frame: int, query_t: float) -> list[Any]:
        history = annotation_histories.get((cg, frame))
        if not history:
            return []
        active_cmds = []
        for t_entry, cmds in history:
            if t_entry <= query_t:
                active_cmds = cmds
            else:
                break
        return active_cmds

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

        # Flush visual state up to this event only for playback, selection, or snapshot events
        is_playback_event = (
            (cmd == "LiveSession.1" and evt == "STATE_SNAPSHOT") or
            (cmd == "PLAYBACK_SETTINGS_1.0" and evt == "SET") or
            (cmd == "SELECTION_1.0" and evt == "SET")
        )
        if is_playback_event:
            flush_segment(t)

        # Update timeline structures and registry
        if cmd == "LiveSession.1" and evt == "STATE_SNAPSHOT":
            timelines_dict = payload_data.get("timelines", {})
            for guid, tl_dict in timelines_dict.items():
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    timeline_map[guid] = tl
                    patcher.traverse_and_map(tl)
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
                    patcher.traverse_and_map(tl)
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
                    patcher.traverse_and_map(tl)
                    for clip in tl.find_clips():
                        if "sync" in clip.metadata and "guid" in clip.metadata["sync"]:
                            clip_uuid = clip.metadata["sync"]["guid"]
                            template_clips[clip_uuid] = clip
                except Exception:
                    pass

        elif cmd == "OTIO_SESSION_1.0":
            if evt == "INSERT_CHILD":
                child_data = payload_data.get("child_data", {})
                if child_data and child_data.get("OTIO_SCHEMA") == "Clip.2":
                    meta = child_data.get("metadata", {})
                    cg = meta.get("clip_guid")
                    sr = child_data.get("source_range")
                    if cg and sr:
                        drw_frame = sr.get("start_time", {}).get("value")
                        if drw_frame is not None:
                            clip_rel_frame = playhead_frame
                            if active_view_mode == "sequence" and active_timeline_guid:
                                tl = timeline_map.get(active_timeline_guid)
                                if tl:
                                    seq_start = get_clip_sequence_start_time(tl, cg)
                                    if seq_start is not None:
                                        clip_rel_frame = playhead_frame - seq_start.value
                            offset = float(drw_frame) - clip_rel_frame
                            template_clip = template_clips.get(cg)
                            duration = 100.0
                            if template_clip:
                                if template_clip.source_range:
                                    duration = template_clip.source_range.duration.value
                                elif template_clip.media_reference and template_clip.media_reference.available_range:
                                    duration = template_clip.media_reference.available_range.duration.value
                            if abs(offset) <= duration:
                                offset = 0.0
                            clip_frame_offsets[cg] = offset

            from otio_sync_core.protocol_messages import message_for
            msg_cls = message_for(cmd, evt)
            if msg_cls:
                try:
                    msg = msg_cls.from_payload(payload_data)
                    patcher.apply_patch(msg)
                except Exception:
                    pass

        elif cmd == "Annotation.1":
            cg = payload_data.get("clip_guid")
            drw_frame = payload_data.get("frame")
            events = payload_data.get("events", [])
            if cg and drw_frame is not None:
                if events:
                    frame_num = int(drw_frame)
                    payload_strokes = {}
                    for ev in events:
                        uuid_val = ev.get("uuid") if isinstance(ev, dict) else getattr(ev, "uuid", None)
                        if uuid_val:
                            payload_strokes.setdefault(uuid_val, []).append(ev)
                    for uuid_val, stroke_cmds in payload_strokes.items():
                        partial_annotations.setdefault(cg, {}).setdefault(frame_num, {})[uuid_val] = stroke_cmds

                clip_rel_frame = playhead_frame
                if active_view_mode == "sequence" and active_timeline_guid:
                    tl = timeline_map.get(active_timeline_guid)
                    if tl:
                        seq_start = get_clip_sequence_start_time(tl, cg)
                        if seq_start is not None:
                            clip_rel_frame = playhead_frame - seq_start.value
                offset = float(drw_frame) - clip_rel_frame
                template_clip = template_clips.get(cg)
                duration = 100.0
                if template_clip:
                    if template_clip.source_range:
                        duration = template_clip.source_range.duration.value
                    elif template_clip.media_reference and template_clip.media_reference.available_range:
                        duration = template_clip.media_reference.available_range.duration.value
                if abs(offset) <= duration:
                    offset = 0.0
                clip_frame_offsets[cg] = offset


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

        # Update annotation history state after processing this event
        update_annotation_histories(t)

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
        duration_frames = int(round(duration_sec * segment.fps))
        if duration_frames <= 0:
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
        media_dur = otio.opentime.RationalTime(duration_frames, segment.fps)
        new_clip.source_range = otio.opentime.TimeRange(start_time=media_start, duration=media_dur)

        if not segment.playing:
            # Add freeze-frame TimeWarp effect
            freeze_effect = otio.schema.LinearTimeWarp(time_scalar=0.0)
            new_clip.effects.append(freeze_effect)
        bg_track.append(new_clip)

    timeline.tracks.append(bg_track)

    # Determine subfolder for PNG files
    output_path_obj = pathlib.Path(output_path)
    output_dir = output_path_obj.parent
    output_stem = output_path_obj.stem
    annotations_dir = output_dir / f"{output_stem}_annotations"
    os.makedirs(annotations_dir, exist_ok=True)

    # Reconstruct Annotations Overlay Track
    overlay_track = otio.schema.Track("Annotations Overlay")
    active_segments = [s for s in segments if (s.end_t - s.start_t) > 0.0]

    # We iterate over the background clips to align annotation clips perfectly
    for segment, bg_clip in zip(active_segments, bg_track):
        fps = segment.fps
        clip_guid = segment.clip_guid
        duration_sec = segment.end_t - segment.start_t
        duration_frames = int(round(duration_sec * fps))
        if duration_frames <= 0:
            continue

        res_w, res_h = get_media_resolution(bg_clip)
        offset = clip_frame_offsets.get(clip_guid, 0.0)

        # Build list of frame info: (F, sig, cmds) for each frame in the segment
        frames_sigs = []
        for offset_idx in range(duration_frames):
            # Query time is the middle of the frame interval
            t_query = segment.start_t + (offset_idx + 0.5) / fps
            
            # Determine F (the media frame)
            if segment.playing:
                F = int(round(segment.start_frame + offset_idx + offset))
            else:
                F = int(round(segment.start_frame + offset))
            
            cmds = get_annotations_at_time(clip_guid, F, t_query)
            sig = get_commands_signature(cmds)
            frames_sigs.append((F, sig, cmds))

        # Group consecutive frames with the same signature
        groups = []
        curr_group = []
        for F, sig, cmds in frames_sigs:
            if not curr_group:
                curr_group = [(F, sig, cmds)]
            elif curr_group[0][1] == sig:
                curr_group.append((F, sig, cmds))
            else:
                groups.append(curr_group)
                curr_group = [(F, sig, cmds)]
        if curr_group:
            groups.append(curr_group)

        for grp in groups:
            grp_start_frame = grp[0][0]
            grp_sig = grp[0][1]
            grp_cmds = grp[0][2]
            grp_len = len(grp)

            if grp_sig is None:
                overlay_track.append(otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(grp_len, fps)
                    )
                ))
            else:
                grp_hash = get_commands_hash(grp_cmds)
                png_name = f"{clip_guid}_{grp_start_frame}_{grp_hash}.png"
                png_path = annotations_dir / png_name
                if not png_path.exists():
                    print(f"[INFO] Rendering annotation frame: {png_path}")
                    img = render_annotations(grp_cmds, res_w, res_h)
                    img.save(png_path)
                else:
                    print(f"[INFO] Skipping rendering, frame already exists: {png_path}")

                rel_target_url = f"{output_stem}_annotations/{png_name}"
                overlay_clip = otio.schema.Clip(
                    name=f"Annotation Overlay ({png_name})",
                    media_reference=otio.schema.ExternalReference(target_url=rel_target_url),
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(grp_len, fps)
                    )
                )
                # Use LinearTimeWarp(0.0) to hold the static image frame
                overlay_clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=0.0))
                overlay_track.append(overlay_clip)

    # Only append the overlay track if it contains actual annotation clips
    if any(isinstance(child, otio.schema.Clip) for child in overlay_track):
        timeline.tracks.append(overlay_track)
    else:
        # Clean up empty directory if no annotations were generated
        try:
            if os.path.exists(annotations_dir) and not os.listdir(annotations_dir):
                os.rmdir(annotations_dir)
        except Exception:
            pass

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
