#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CLI tool to convert a session recording (.jsonl) to an OTIO timeline."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

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
from otio_sync_core.frame_resolution import (
    FrameResolutionError,
    clip_effective_range,
    resolve_view_frame,
)
from sync_recorder.annotation_renderer import render_annotations



class VisualSegment:
    """A continuous visual run in the recorded session, in media frame space.

    ``start_frame`` is the **media** source frame the run begins on (already
    resolved from the protocol view frame through the active clip), not the raw
    view frame. ``n_frames`` is the output duration in frames: for a playing
    run it equals the number of media frames advanced; for a freeze it is the
    wall-clock hold length (the single ``start_frame`` is held via a
    ``LinearTimeWarp``).
    """

    def __init__(
        self,
        template_clip: Any,
        clip_guid: str | None,
        start_frame: int,
        fps: float,
        start_t: float,
        end_t: float,
        playing: bool,
        n_frames: int,
    ) -> None:
        self.template_clip = template_clip
        self.clip_guid = clip_guid
        self.start_frame = start_frame
        self.fps = fps
        self.start_t = start_t
        self.end_t = end_t
        self.playing = playing
        self.n_frames = n_frames

    def __repr__(self) -> str:
        return (
            f"<VisualSegment clip={self.clip_guid} media_start={self.start_frame} "
            f"n={self.n_frames} playing={self.playing} "
            f"t=[{self.start_t:.3f},{self.end_t:.3f}]>"
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


def get_primary_video_track(tl: otio.schema.Timeline) -> otio.schema.Track | None:
    """Return the main media track of a timeline (skipping the Annotations track)."""
    for track in tl.tracks:
        if track.kind == "Video" and track.name in ("Video Track", "Media", "tracks", "Video"):
            return track
    for track in tl.tracks:
        if track.kind == "Video" and track.name not in ("Dropped", "Annotations"):
            return track
    for track in tl.tracks:
        if track.kind == "Video":
            return track
    return None


def _clip_guid(clip: Any) -> str | None:
    """Return a clip's sync guid, if present."""
    if clip is None:
        return None
    meta = getattr(clip, "metadata", None)
    if meta is not None:
        try:
            return meta.get("sync", {}).get("guid")
        except Exception:
            return None
    return None


class PlaybackModel:
    """Live projection of the recorded session's playback state.

    Segments are emitted from transitions of this model rather than ad-hoc
    per-event arithmetic. ``view_frame`` is the current playhead in
    timeline/view coordinates; it is resolved to a media frame through the
    active clip only when a segment is flushed.
    """

    def __init__(self, fps: float) -> None:
        self.active_timeline_guid: str | None = None
        self.active_view_mode: str = "sequence"
        self.selected_clip_guid: str | None = None
        self.playing: bool = False
        self.playback_mode: str = "loop"
        self.view_frame: float = 0.0
        self.view_rate: float = fps


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

    model = PlaybackModel(target_fps)

    segments: list[VisualSegment] = []
    partial_annotations: dict[str, dict[int, dict[str, Any]]] = {}
    annotation_histories: dict[tuple[str, int], list] = {}
    last_recorded_signatures: dict[tuple[str, int], Any] = {}

    last_flush_t = 0.0

    # ------------------------------------------------------------------ model

    def active_track() -> otio.schema.Track | None:
        tl = timeline_map.get(model.active_timeline_guid)
        return get_primary_video_track(tl) if tl else None

    def view_resolvable() -> bool:
        """Whether the current view can be resolved to media yet.

        A recording can carry playback events that name a timeline/clip before
        the snapshot (or ``ADD_TIMELINE``) that defines it has arrived — early
        master-state noise. Such intervals have no structure to resolve
        against, so they are skipped rather than resolved. A timeline that *is*
        loaded but whose clip lacks any range still fails loudly downstream.
        """
        if model.active_view_mode == "source":
            return template_clips.get(model.selected_clip_guid) is not None
        return active_track() is not None

    def sequence_end_frame() -> float:
        """End of the active view in view frames (exclusive)."""
        if model.active_view_mode == "source":
            clip = template_clips.get(model.selected_clip_guid)
            eff = clip_effective_range(clip) if clip is not None else None
            return eff.duration.value if eff is not None else 0.0
        track = active_track()
        if track is None:
            return 0.0
        try:
            return track.duration().value
        except Exception:
            return 0.0

    def resolve(view_frame: float) -> tuple[Any, int]:
        """Resolve a view frame to ``(clip, media_frame)`` via the active view.

        A reported playhead may sit exactly at (or just past) the end of the
        view — e.g. a host that stops at the sequence end. Clamp into the valid
        range so such a frame maps to the last picture rather than raising; a
        clip that is *structurally* unresolvable (no range at all) still fails
        loudly inside :func:`resolve_view_frame`.
        """
        end = sequence_end_frame()
        vf = view_frame
        if end > 0:
            vf = max(0.0, min(vf, end - 1))
        if model.active_view_mode == "source":
            clip = template_clips.get(model.selected_clip_guid)
            return resolve_view_frame(
                None, vf, view_mode="source",
                selected_clip=clip, rate=model.view_rate,
            )
        return resolve_view_frame(
            active_track(), vf, view_mode="sequence", rate=model.view_rate,
        )

    def clip_view_end(view_frame: float, clip: Any) -> float:
        """View-frame end (exclusive) of *clip* within the active view."""
        if model.active_view_mode == "source":
            eff = clip_effective_range(clip)
            return eff.duration.value if eff is not None else view_frame + 1
        track = active_track()
        try:
            r = track.range_of_child(clip)
            return r.start_time.value + r.duration.value
        except Exception:
            return view_frame + 1

    def emit_playing(w_start: float, w_end: float, wall_frames: int, fps: float) -> None:
        """Emit one or more playing sub-segments, splitting at clip boundaries
        and wrapping at sequence end when in loop mode. Advances
        ``model.view_frame``."""
        vf = float(model.view_frame)
        seq_end = sequence_end_frame()
        frames_left = wall_frames
        frames_done = 0
        guard = 0
        while frames_left > 0 and guard < 100000:
            guard += 1
            clip, media = resolve(vf)
            view_end = clip_view_end(vf, clip)
            avail_clip = max(1.0, view_end - vf)
            avail_seq = (seq_end - vf) if seq_end > 0 else float(frames_left)
            take = int(min(frames_left, avail_clip, max(1.0, avail_seq)))
            if take <= 0:
                take = 1
            sub_ws = w_start + frames_done / fps
            sub_we = w_start + (frames_done + take) / fps
            segments.append(VisualSegment(
                template_clip=clip, clip_guid=_clip_guid(clip),
                start_frame=int(media), fps=fps,
                start_t=sub_ws, end_t=sub_we, playing=True, n_frames=take,
            ))
            vf += take
            frames_left -= take
            frames_done += take
            if seq_end > 0 and vf >= seq_end - 1e-6:
                if model.playback_mode == "loop":
                    vf = 0.0
                else:
                    if frames_left > 0:
                        hold_clip, hold_media = resolve(max(seq_end - 1, 0))
                        segments.append(VisualSegment(
                            template_clip=hold_clip, clip_guid=_clip_guid(hold_clip),
                            start_frame=int(hold_media), fps=fps,
                            start_t=w_start + frames_done / fps, end_t=w_end,
                            playing=False, n_frames=frames_left,
                        ))
                    vf = max(seq_end - 1, 0)
                    frames_left = 0
        model.view_frame = vf

    def flush(t: float) -> None:
        """Emit segments for the wall-clock interval since the last flush."""
        nonlocal last_flush_t
        wall_dur = t - last_flush_t
        if wall_dur <= 0.0:
            last_flush_t = t
            return
        fps = model.view_rate
        wall_frames = int(round(wall_dur * fps))
        if wall_frames <= 0:
            last_flush_t = t
            return
        if model.active_timeline_guid is None or not view_resolvable():
            last_flush_t = t
            return

        if not model.playing:
            clip, media = resolve(model.view_frame)
            segments.append(VisualSegment(
                template_clip=clip, clip_guid=_clip_guid(clip),
                start_frame=int(media), fps=fps,
                start_t=last_flush_t, end_t=t, playing=False, n_frames=wall_frames,
            ))
        else:
            emit_playing(last_flush_t, t, wall_frames, fps)
        last_flush_t = t

    # ------------------------------------------------------- annotations state

    def register_timeline(guid: str, tl: otio.schema.Timeline) -> None:
        timeline_map[guid] = tl
        patcher.traverse_and_map(tl)
        for clip in tl.find_clips():
            cg = _clip_guid(clip)
            if cg:
                template_clips[cg] = clip

    def resolve_annotation_media_frame(cg: str, drw_frame: float) -> int | None:
        """Resolve an annotation's clip-local view frame to a media frame."""
        clip = template_clips.get(cg)
        if clip is None:
            return None
        try:
            _, media = resolve_view_frame(
                None, float(drw_frame), view_mode="source",
                selected_clip=clip, rate=model.view_rate,
            )
            return media
        except FrameResolutionError:
            return None

    def update_annotation_histories(t_offset: float) -> None:
        committed_by_cg_frame: dict[str, dict[int, dict[str, Any]]] = {}
        for tl in timeline_map.values():
            for clip in tl.find_clips():
                if "clip_guid" in clip.metadata and "annotation_commands" in clip.metadata:
                    cg = clip.metadata["clip_guid"]
                    if clip.source_range:
                        view_frame = clip.source_range.start_time.value
                        media_frame = resolve_annotation_media_frame(cg, view_frame)
                        if media_frame is None:
                            continue
                        commands = clip.metadata["annotation_commands"]
                        if commands:
                            frame_dict = committed_by_cg_frame.setdefault(cg, {}).setdefault(media_frame, {})
                            clip_strokes: dict[str, list] = {}
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
            strokes_dict: dict[str, list] = {}
            if cg in committed_by_cg_frame and frame in committed_by_cg_frame[cg]:
                strokes_dict.update(committed_by_cg_frame[cg][frame])
            if cg in partial_annotations and frame in partial_annotations[cg]:
                for uuid_val, stroke_cmds in partial_annotations[cg][frame].items():
                    if uuid_val not in strokes_dict:
                        strokes_dict[uuid_val] = stroke_cmds

            flat_cmds: list = []
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
        active_cmds: list = []
        for t_entry, cmds in history:
            if t_entry <= query_t:
                active_cmds = cmds
            else:
                break
        return active_cmds

    # --------------------------------------------------------------- main loop

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

        # Flush visual state up to this event for playback/selection/snapshot events
        is_playback_event = (
            (cmd == "LiveSession.1" and evt == "STATE_SNAPSHOT") or
            (cmd == "PLAYBACK_SETTINGS_1.0" and evt == "SET") or
            (cmd == "SELECTION_1.0" and evt == "SET")
        )
        if is_playback_event:
            flush(t)

        if cmd == "LiveSession.1" and evt == "STATE_SNAPSHOT":
            timelines_dict = payload_data.get("timelines", {})
            for guid, tl_dict in timelines_dict.items():
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    register_timeline(guid, tl)
                except Exception as e:
                    import traceback
                    print(f"Error parsing timeline {guid}: {e}")
                    traceback.print_exc()
                    raise e
            model.active_timeline_guid = payload_data.get("active_timeline_guid")
            playback_state = payload_data.get("playback_state")
            if playback_state:
                model.playing = playback_state.get("playing", False)
                model.playback_mode = playback_state.get("playback_mode", model.playback_mode)
                current_time = playback_state.get("current_time")
                if current_time:
                    model.view_frame = current_time.get("value", 0.0)
                    model.view_rate = current_time.get("rate", model.view_rate)

        elif cmd == "TIMELINE_1.0" and evt in ("ADD_TIMELINE", "REPLACE_TIMELINE"):
            guid = payload_data.get("timeline_guid")
            tl_dict = payload_data.get("timeline")
            if guid and tl_dict:
                try:
                    tl = otio.adapters.read_from_string(json.dumps(tl_dict), "otio_json")
                    register_timeline(guid, tl)
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
                            media_frame = resolve_annotation_media_frame(cg, drw_frame)
                            # committed strokes are picked up via
                            # update_annotation_histories; nothing to store here.
                            _ = media_frame

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
            if cg and drw_frame is not None and events:
                media_frame = resolve_annotation_media_frame(cg, drw_frame)
                if media_frame is not None:
                    payload_strokes: dict[str, list] = {}
                    for ev in events:
                        uuid_val = ev.get("uuid") if isinstance(ev, dict) else getattr(ev, "uuid", None)
                        if uuid_val:
                            payload_strokes.setdefault(uuid_val, []).append(ev)
                    for uuid_val, stroke_cmds in payload_strokes.items():
                        partial_annotations.setdefault(cg, {}).setdefault(media_frame, {})[uuid_val] = stroke_cmds

        elif cmd == "PLAYBACK_SETTINGS_1.0" and evt == "SET":
            model.playing = payload_data.get("playing", False)
            model.playback_mode = payload_data.get("playback_mode", model.playback_mode)
            current_time = payload_data.get("current_time")
            if current_time:
                model.view_frame = current_time.get("value", 0.0)
                model.view_rate = current_time.get("rate", model.view_rate)
            if "view_mode" in payload_data:
                model.active_view_mode = payload_data["view_mode"]
            if payload_data.get("timeline_guid"):
                model.active_timeline_guid = payload_data["timeline_guid"]
            # In source view mode the selected clip rides on the playback event
            # itself (there may be no separate SELECTION event).
            if payload_data.get("clip_guid") is not None:
                model.selected_clip_guid = payload_data["clip_guid"]

        elif cmd == "SELECTION_1.0" and evt == "SET":
            model.selected_clip_guid = payload_data.get("clip_guid")
            if "view_mode" in payload_data:
                model.active_view_mode = payload_data["view_mode"]

        update_annotation_histories(t)

    # Final flush at end of file
    if lines:
        try:
            last_event = json.loads(lines[-1].strip())
            final_t = last_event.get("time_offset", last_flush_t)
            flush(final_t)
        except Exception:
            pass

    # ---------------------------------------------------------- reconstruct

    timeline = otio.schema.Timeline("Session Screen Recording")
    bg_track = otio.schema.Track("Background Media")

    for segment in segments:
        if segment.n_frames <= 0 or segment.template_clip is None:
            continue

        new_clip = otio.schema.Clip(
            name=segment.template_clip.name,
            media_reference=segment.template_clip.media_reference,
        )
        media_start = otio.opentime.RationalTime(segment.start_frame, segment.fps)
        media_dur = otio.opentime.RationalTime(segment.n_frames, segment.fps)
        new_clip.source_range = otio.opentime.TimeRange(start_time=media_start, duration=media_dur)

        if not segment.playing:
            new_clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=0.0))
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
    active_segments = [s for s in segments if s.n_frames > 0 and s.template_clip is not None]

    for segment, bg_clip in zip(active_segments, bg_track):
        fps = segment.fps
        clip_guid = segment.clip_guid
        duration_frames = segment.n_frames
        if duration_frames <= 0:
            continue

        res_w, res_h = get_media_resolution(bg_clip)

        # Build list of frame info: (F, sig, cmds) for each output frame.
        frames_sigs = []
        for offset_idx in range(duration_frames):
            t_query = segment.start_t + (offset_idx + 0.5) / fps
            if segment.playing:
                F = int(round(segment.start_frame + offset_idx))
            else:
                F = int(round(segment.start_frame))
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
                overlay_clip.effects.append(otio.schema.LinearTimeWarp(time_scalar=0.0))
                overlay_track.append(overlay_clip)

    if any(isinstance(child, otio.schema.Clip) for child in overlay_track):
        timeline.tracks.append(overlay_track)
    else:
        try:
            if os.path.exists(annotations_dir) and not os.listdir(annotations_dir):
                os.rmdir(annotations_dir)
        except Exception:
            pass

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
