#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil
import sys
import uuid
from urllib.parse import urlsplit

from xstudio.plugin import PluginBase
from xstudio.core import serialise_atom

import opentimelineio as otio

# Make the ORIAnnotations Python module importable from within this plugin's
# package tree (../../python/ relative to this file).
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
_python_dir = os.path.join(_repo_root, "python")
_manifest_dir = os.path.join(_repo_root, "otio_event_plugin")
_manifest_file = os.path.join(_manifest_dir, "plugin_manifest.json")

if _python_dir not in sys.path:
    sys.path.insert(0, _python_dir)
if _manifest_dir not in sys.path:
    sys.path.insert(0, _manifest_dir)

# Extend OTIO_PLUGIN_MANIFEST_PATH so SyncEvent schemadefs are discoverable.
if os.path.exists(_manifest_file):
    existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if _manifest_file not in existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            existing + os.pathsep + _manifest_file if existing else _manifest_file
        )

import ORIAnnotations

SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")

_DIALOG_QML = """
ORIAnnotationsExportDialog {
}
"""


class ORIAnnotationsExporter(PluginBase):

    def __init__(self, connection):
        PluginBase.__init__(
            self,
            connection,
            name="ORIAnnotationsExporter",
            qml_folder="qml/ORIAnnotationsExporter.1",
        )

        self.insert_menu_item(
            "main menu bar",
            "Export Annotations (OTIO)...",
            "File|Export",
            0.6,
            callback=self._menu_callback,
        )
        self.connect_to_ui()

    # ------------------------------------------------------------------
    # Menu / UI
    # ------------------------------------------------------------------

    def _menu_callback(self):
        self.create_qml_item(_DIALOG_QML)

    # ------------------------------------------------------------------
    # Export entry point — called from QML via python_callback
    # ------------------------------------------------------------------

    def do_export(self, output_folder, otio_name, include_media, include_images):
        """Called from QML. Returns [True, message] on success, [False, message] on failure."""
        try:
            # QML's showFolderDialog returns a file:// URI; strip the scheme.
            output_folder = urlsplit(str(output_folder)).path or str(output_folder)
            otio_name = str(otio_name).strip() or "annotations.otio"
            if not otio_name.endswith(".otio"):
                otio_name += ".otio"

            playlist = self.connection.api.session.inspected_container
            if playlist is None:
                return [False, "No playlist is currently selected."]

            media_list, review_items, annotated_count = self._collect_bookmarks(
                playlist, output_folder, bool(include_media), bool(include_images)
            )

            if not media_list:
                return [False, "No media found in the current playlist."]

            review = ORIAnnotations.Review(title="Review", review_items=review_items)
            reviewgroup = ORIAnnotations.ReviewGroup(media=media_list, reviews=[review])
            timeline = reviewgroup.export_otio_timeline(as_nested_stacks=False)

            output_path = os.path.join(output_folder, otio_name)
            otio.adapters.write_to_file(timeline, output_path)

            return [True, f"Exported {annotated_count} annotated frame(s).\n\nOutput: {output_path}"]

        except Exception as exc:
            import traceback
            return [False, f"{exc}\n\n{traceback.format_exc()}"]

    # ------------------------------------------------------------------
    # Bookmark collection
    # ------------------------------------------------------------------

    def _collect_bookmarks(self, playlist, output_dir, include_media, include_images):
        media_list = []
        review_items = []
        annotated_count = 0

        for media in playlist.media:
            try:
                ms = media.media_source()
                if ms is None:
                    continue
                mr = ms.media_reference
                if mr is None:
                    continue

                rate = ms.rate
                fps = rate.fps() if rate else 24.0
                seconds_per_frame = rate.seconds() if rate else (1.0 / 24.0)
                frame_count = mr.frame_count()

                # xstudio stores annotations in W-normalised coords (X ∈ [-1,+1],
                # Y ∈ [-H/W,+H/W]).  RV/OTIO paint nodes use H-normalised WCS
                # (Y ∈ [-0.5,+0.5], X ∈ [-aspect/2,+aspect/2]).
                # Scale factor to convert: W/(2H) = aspect/2.
                try:
                    streams = ms.image_streams
                    res = streams[0].media_stream_detail.resolution() if streams else None
                    img_w, img_h = (res[0], res[1]) if res and res[1] else (1920, 1080)
                except Exception:
                    img_w, img_h = 1920, 1080
                aspect_half = img_w / (2.0 * img_h)

                media_path = urlsplit(str(mr.uri())).path

                if include_media and os.path.exists(media_path):
                    dest = os.path.join(output_dir, os.path.basename(media_path))
                    if os.path.abspath(media_path) != os.path.abspath(dest):
                        shutil.copy(media_path, dest)
                    export_path = os.path.basename(media_path)
                else:
                    export_path = media_path

                ori_media = ORIAnnotations.Media(
                    media_path=export_path,
                    name=media.name,
                    frame_rate=fps,
                    start_frame=0,
                    duration=frame_count,
                )
                media_list.append(ori_media)

                ri = ORIAnnotations.ReviewItem(media=ori_media)
                review_items.append(ri)
                frames = []

                bms = media.ordered_bookmarks()
                for bookmark in bms:
                    # xstudio frames are 0-based; RV paint nodes use 1-based frame numbers.
                    frame = int(round(bookmark.start.total_seconds() / seconds_per_frame)) + 1
                    note = bookmark.note or ""
                    events = self._bookmark_to_sync_events(bookmark, aspect_half)

                    if not events and not note:
                        continue

                    ann_image = None
                    if include_images and events:
                        ann_image = self._render_annotation_image(
                            output_dir, media.name, frame, bookmark
                        )

                    rf = ORIAnnotations.ReviewItemFrame(
                        review_item=ri,
                        frame=frame,
                        note=note,
                        annotation_commands=events,
                        annotation_image=ann_image,
                    )
                    frames.append(rf)
                    annotated_count += 1

                ri.review_frames = frames

            except Exception as exc:
                print(f"[ORIAnnotations] Warning: skipping '{media.name}': {exc}")
                continue

        return media_list, review_items, annotated_count

    def _bookmark_to_sync_events(self, bookmark, aspect_half=0.8889):
        try:
            raw = self.connection.request_receive(bookmark.remote, serialise_atom())[0]
            ann = json.loads(raw.dump())
            annotation = ann.get("base", {}).get("annotation") or {}
            data = annotation.get("Data", {})
            strokes = data.get("pen_strokes", [])
            return (
                self._strokes_to_sync_events(strokes, aspect_half)
                + self._captions_to_sync_events(data.get("captions", []), aspect_half)
            )
        except Exception as exc:
            print(f"[ORIAnnotations] Warning: could not read annotation data: {exc}")
            return []

    def _render_annotation_image(self, output_dir, media_name, frame, bookmark):
        safe_name = media_name.replace("/", "_").replace("\\", "_")
        img_path = os.path.join(output_dir, f"{safe_name}.{frame:05d}.png")
        try:
            self.connection.api.app.snapshot_viewport.render_bookmark_with_transparency(
                img_path,
                bookmark.uuid,
                include_image=False,
                include_drawings=True,
            )
            return img_path
        except Exception as exc:
            print(f"[ORIAnnotations] Warning: could not render annotation image: {exc}")
            return None

    # ------------------------------------------------------------------
    # Stroke / caption conversion
    # ------------------------------------------------------------------

    def _strokes_to_sync_events(self, pen_strokes, aspect_half=0.8889):
        events = []
        for stroke in pen_strokes:
            stroke_uuid = str(uuid.uuid4())
            r = stroke.get("r", 1.0)
            g = stroke.get("g", 1.0)
            b = stroke.get("b", 1.0)
            opacity = stroke.get("opacity", 1.0)
            thickness = stroke.get("thickness", 0.003)
            is_erase = stroke.get("is_erase_stroke", False)
            raw_pts = stroke.get("points", [])

            # Convert from xstudio W-normalised (Y-down) to RV H-normalised (Y-up).
            # Scale factor: W/(2H) = aspect/2 = aspect_half.
            xs  = [x * aspect_half for x in raw_pts[0::4]]
            ys  = [-y * aspect_half for y in raw_pts[1::4]]
            sps = raw_pts[2::4]

            if xs and any(sp != 0.0 for sp in sps):
                widths = [thickness * aspect_half * sp for sp in sps]
            else:
                widths = [thickness * aspect_half] * len(xs)

            start_evt = SyncEvent.PaintStart(
                brush="oval",
                rgba=[r, g, b, opacity],
                friendly_name="",
                uuid=stroke_uuid,
            )
            if is_erase:
                start_evt.type = "erase"
            events.append(start_evt)

            verts = SyncEvent.PaintVertices(list(xs), list(ys), widths)
            events.append(SyncEvent.PaintPoints(uuid=stroke_uuid, points=verts))

        return events

    def _captions_to_sync_events(self, captions, aspect_half=0.8889):
        events = []
        for caption in captions:
            caption_uuid = str(uuid.uuid4())

            colour = caption.get("colour", ["colour", 1, 1.0, 1.0, 1.0])
            if isinstance(colour, list) and len(colour) >= 5:
                r, g, b = float(colour[2]), float(colour[3]), float(colour[4])
            else:
                r, g, b = 1.0, 1.0, 1.0
            opacity = float(caption.get("opacity", 1.0))

            pos = caption.get("position", ["vec2", 1, 0.0, 0.0])
            if isinstance(pos, list) and len(pos) >= 4:
                position = [float(pos[2]) * aspect_half, -float(pos[3]) * aspect_half]
            else:
                position = [0.0, 0.0]

            event = SyncEvent.TextAnnotation(
                rgba=[r, g, b, opacity],
                position=position,
                spacing=0.0,
                friendly_name=caption.get("font_name", ""),
                font_size=float(caption.get("font_size", 50.0)),
                font=caption.get("font_name", ""),
                text=caption.get("text", ""),
                rotation=0.0,
                scale=1.0,
                uuid=caption_uuid,
            )
            events.append(event)

        return events


def create_plugin_instance(connection):
    return ORIAnnotationsExporter(connection)
