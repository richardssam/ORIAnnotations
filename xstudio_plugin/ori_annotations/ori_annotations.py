#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0

import datetime
import json
import logging
import os
import shutil
import sys
import traceback
from urllib.parse import urlsplit

import opentimelineio as otio
from xstudio.core import (
    BookmarkDetail,
    bookmark_detail_atom,
    serialise_atom,
)
from xstudio.plugin import PluginBase

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

from otio_sync_core.xs_annotation_codec import (  # noqa: E402
    xs_strokes_to_sync_events,
    xs_captions_to_sync_events,
    sync_events_to_xs_strokes,
    sync_events_to_xs_captions,
)

SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")

# ── logging ────────────────────────────────────────────────────────────────────
# Mirrors the pattern in ori_sync_plugin.py.
# Set ORI_ANNOTATIONS_LOG_FILE=/path/to/file.log before launching xStudio to
# get a persistent record alongside the console output.


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("ori_annotations")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s", datefmt="%H:%M:%S")
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    log_file = os.environ.get("ORI_ANNOTATIONS_LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


_logger = _make_logger()


def _log(msg: str) -> None:
    _logger.debug(msg)


def _log_exc(msg: str) -> None:
    _logger.exception(msg)


def _uri_to_posix_path(uri: str) -> str:
    """Convert a URI or xStudio internal URI string to a POSIX filesystem path.

    Handles the common forms returned by xStudio's ``MediaReference.uri()``:

    * ``file:///path`` → ``/path``
    * ``file://localhost/path`` → ``/path``
    * ``localhost//path`` (xStudio-specific, no ``file:`` scheme) → ``/path``
    * plain ``/path`` → ``/path`` (unchanged)
    """
    import urllib.parse
    if uri.startswith("file:"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        # file://localhost//path serialises with netloc='localhost' and
        # path='//absolute/path' — normalize the double leading slash.
        if path.startswith("//"):
            path = path[1:]
        return path
    if uri.startswith("localhost//"):
        # xStudio stores local URIs as "localhost//absolute/path"
        return uri[10:]  # strip "localhost/" leaving "/absolute/path"
    return uri


_EXPORT_DIALOG_QML = """
ORIAnnotationsExportDialog {
}
"""

_IMPORT_DIALOG_QML = """
ORIAnnotationsImportDialog {
}
"""


class ORIAnnotationsPlugin(PluginBase):
    """xStudio plugin that exports and imports ORI annotation OTIO files.

    Adds two entries to *File → Export / Import*:

    * **Export Annotations (OTIO)…** — gathers xStudio bookmarks from the
      current playlist and writes them to an OTIO file.
    * **Import Annotations (OTIO)…** — reads a previously exported OTIO file
      and recreates its annotations as xStudio bookmarks on the matching media.

    Stroke and caption conversion between xStudio's native dict format and the
    OTIO SyncEvent schema is handled by
    :mod:`otio_sync_core.xs_annotation_codec`, which is also used by
    :mod:`ori_sync_plugin` so the two plugins share identical conversion logic.

    :param connection: xStudio connection object passed by the plugin loader.
    """

    def __init__(self, connection):
        PluginBase.__init__(
            self,
            connection,
            name="ORIAnnotationsPlugin",
            qml_folder="qml/ORIAnnotationsExporter.1",
        )

        self.insert_menu_item(
            "main menu bar",
            "Export Annotations (OTIO)...",
            "File|Export",
            0.6,
            callback=self._export_menu_callback,
        )
        self.insert_menu_item(
            "main menu bar",
            "Import Annotations (OTIO)...",
            "File|Import",
            0.6,
            callback=self._import_menu_callback,
        )
        self.connect_to_ui()

    # ------------------------------------------------------------------
    # Menu / UI callbacks
    # ------------------------------------------------------------------

    def _export_menu_callback(self):
        self.create_qml_item(_EXPORT_DIALOG_QML)

    def _import_menu_callback(self):
        self.create_qml_item(_IMPORT_DIALOG_QML)

    # ------------------------------------------------------------------
    # Export entry point — called from QML via python_callback
    # ------------------------------------------------------------------

    def _resolve_playlist(self):
        """Helper to find a playlist container to operate on, falling back through options."""
        playlist = None
        try:
            playlist = self.connection.api.session.inspected_container
        except Exception:
            pass

        if playlist is None:
            try:
                playlist = self.connection.api.session.viewed_container
            except Exception:
                pass

        if playlist is None:
            try:
                playlists = self.connection.api.session.playlists
                if playlists:
                    playlist = playlists[0]
            except Exception:
                pass

        if playlist is None:
            try:
                _log("No playlist available. Creating 'Imported Annotations' playlist.")
                res = self.connection.api.session.create_playlist("Imported Annotations")
                playlist = res[1]
            except Exception:
                _log_exc("Failed to automatically create playlist 'Imported Annotations'")
        return playlist

    def export_annotations(self, output_folder, otio_name, include_media, include_images, playlist=None):
        """Synchronously export annotations from a playlist to an OTIO file.

        :param output_folder: Destination folder path (may be a ``file://`` URI).
        :param otio_name: Desired filename for the OTIO output.
        :param include_media: Whether to copy media files into *output_folder*.
        :param include_images: Whether to render annotation images as PNGs.
        :param playlist: Source xStudio Playlist. If None, resolves the active playlist.
        :returns: ``(success, message)`` tuple.
        :rtype: tuple
        """
        # QML's showFolderDialog returns a file:// URI; strip the scheme.
        output_folder = urlsplit(str(output_folder)).path or str(output_folder)
        otio_name = str(otio_name).strip() or "annotations.otio"
        if not otio_name.endswith(".otio"):
            otio_name += ".otio"

        if playlist is None:
            playlist = self._resolve_playlist()
        if playlist is None:
            return False, "No playlist is currently selected or available."

        media_list, review_items, annotated_count = self._collect_bookmarks(
            playlist, output_folder, bool(include_media), bool(include_images)
        )

        if not media_list:
            return False, "No media found in the source playlist."

        review = ORIAnnotations.Review(title="Review", review_items=review_items)
        reviewgroup = ORIAnnotations.ReviewGroup(media=media_list, reviews=[review])
        timeline = reviewgroup.export_otio_timeline(as_nested_stacks=False)

        output_path = os.path.join(output_folder, otio_name)
        otio.adapters.write_to_file(timeline, output_path)

        return True, f"Exported {annotated_count} annotated frame(s).\n\nOutput: {output_path}"

    def do_export(self, output_folder, otio_name, include_media, include_images):
        """Export current playlist annotations to an OTIO file.

        Called from ``ORIAnnotationsExportDialog.qml``.
        """
        try:
            success, msg = self.export_annotations(output_folder, otio_name, include_media, include_images)
            return [success, msg]
        except Exception:
            _log_exc("do_export failed")
            return [False, traceback.format_exc()]

    def import_annotations(self, otio_file_path, playlist=None):
        """Synchronously import annotations from an OTIO file into a playlist.

        :param otio_file_path: Path to the ``.otio`` file.
        :param playlist: Target xStudio Playlist. If None, resolves the active playlist.
        :returns: ``(success, message)`` tuple.
        :rtype: tuple
        """
        if playlist is None:
            playlist = self._resolve_playlist()
        if playlist is None:
            return False, "No playlist is selected or available."

        _log(f"Starting import of annotations from {otio_file_path} into playlist '{playlist.name}'")
        timeline = otio.adapters.read_from_file(otio_file_path)

        # Build a name → media lookup for the current playlist.
        media_by_name: dict = {}
        for media in playlist.media:
            name = media.name
            media_by_name[name] = media
            # Also index by basename stem so that xStudio playlists loaded
            # via full path still match exported clip names.
            stem = os.path.splitext(os.path.basename(name))[0]
            media_by_name.setdefault(stem, media)

        # Build a lookup of all clips in the "Media" track.
        otio_media_clips = {}
        media_track = next(
            (t for t in timeline.tracks if t.name == "Media"), None
        )
        if not media_track and len(timeline.tracks) > 0:
            media_track = timeline.tracks[0]
        if media_track:
            for clip in media_track:
                if isinstance(clip, otio.schema.Clip):
                    otio_media_clips[clip.name] = clip
                    stem = os.path.splitext(os.path.basename(clip.name))[0]
                    otio_media_clips.setdefault(stem, clip)

        applied = 0
        skipped = 0
        warnings: list = []

        # tracks[0] is the "Media" track or media_track; review/annotation tracks follow.
        for track in timeline.tracks:
            if track == media_track:
                continue
            for clip in track:
                if not isinstance(clip, otio.schema.Clip):
                    continue

                commands = clip.metadata.get("annotation_commands", [])
                note = clip.metadata.get("note", "")
                if not commands and not note:
                    continue

                # annotated_clip_name names the media this annotation
                # belongs to; fall back to the clip name with its ".frame"
                # suffix removed.
                annotated_name = (
                    clip.metadata.get("annotated_clip_name")
                    or clip.name.rsplit(".", 1)[0]
                )

                media = media_by_name.get(annotated_name)
                if media is None:
                    stem = os.path.splitext(os.path.basename(annotated_name))[0]
                    media = media_by_name.get(stem)

                # If not found in the playlist, try to load it from the OTIO media references.
                if media is None:
                    otio_clip = otio_media_clips.get(annotated_name)
                    if otio_clip is None:
                        stem = os.path.splitext(os.path.basename(annotated_name))[0]
                        otio_clip = otio_media_clips.get(stem)

                    if otio_clip is not None:
                        mr = otio_clip.media_reference
                        if isinstance(mr, otio.schema.ExternalReference):
                            uri = mr.target_url or ""
                            path = _uri_to_posix_path(uri)
                            if path:
                                _log(f"Media '{annotated_name}' not in playlist. Loading from {path}...")
                                try:
                                    media = self._safe_add_media(playlist, path)
                                    if media:
                                        # Update our cache
                                        media_by_name[media.name] = media
                                        media_by_name[os.path.splitext(os.path.basename(media.name))[0]] = media
                                except Exception:
                                    _log_exc(f"Failed to automatically load media from {path}")

                if media is None:
                    warnings.append(f"No media match for '{annotated_name}'")
                    skipped += 1
                    continue

                # Recover frame number and timing.
                sr = clip.source_range
                if sr is None:
                    skipped += 1
                    continue
                otio_frame = int(sr.start_time.value)
                fps = float(sr.start_time.rate) if sr.start_time.rate else 24.0
                # Exporter stored xStudio_frame + 1; recover 0-based frame.
                xs_frame = max(0, otio_frame - 1)
                start_sec = xs_frame / fps

                aspect_half = self._aspect_half_for_media(media)
                pen_strokes = sync_events_to_xs_strokes(commands, aspect_half)
                captions = sync_events_to_xs_captions(commands, aspect_half)

                if not pen_strokes and not captions and not note:
                    skipped += 1
                    continue

                # Create a new bookmark at the target frame.
                bm = self.connection.api.session.bookmarks.add_bookmark(
                    target=media
                )
                detail = BookmarkDetail()
                detail.start = datetime.timedelta(seconds=start_sec)
                # Duration < 1 frame so the bookmark displays on exactly
                # one frame (mirrors the approach in ori_sync_plugin.py).
                detail.duration = datetime.timedelta(seconds=0.9 / fps)
                if note:
                    try:
                        detail.note = note
                    except AttributeError:
                        pass  # older xStudio builds may not expose this field
                self.connection.request_receive(
                    bm.remote, bookmark_detail_atom(), detail
                )

                if pen_strokes or captions:
                    bm.set_annotation(strokes=pen_strokes, captions=captions)

                applied += 1

        msg = f"Imported {applied} annotated frame(s)."
        if skipped:
            msg += f"\n{skipped} frame(s) skipped."
        if warnings:
            msg += "\n\nWarnings:\n" + "\n".join(warnings[:10])

        if applied == 0:
            return False, f"No annotations found to import.\n\n{msg}"
        return True, msg

    def do_import(self, otio_file):
        """Import annotations from an OTIO file into the current playlist.

        Called from ``ORIAnnotationsImportDialog.qml``.
        """
        try:
            import threading
            otio_file_path = urlsplit(str(otio_file)).path or str(otio_file)

            playlist = self._resolve_playlist()
            if playlist is None:
                return [False, "No playlist is currently selected or available in xStudio. Please select or create a playlist first."]

            # Start thread to do the actual import asynchronously to avoid deadlocks on main UI thread.
            t = threading.Thread(target=self._async_import_worker, args=(otio_file_path, playlist))
            t.daemon = True
            t.start()

            return [True, "Import started in background. You will receive a popup notification when complete."]

        except Exception:
            _log_exc("do_import failed")
            tb = traceback.format_exc()
            try:
                sys.__stderr__.write(f"\n[ORI Annotations Error] do_import failed:\n{tb}\n")
                sys.__stderr__.flush()
            except Exception:
                pass
            print(f"do_import failed:\n{tb}", file=sys.stderr)
            return [False, tb]

    def _safe_add_media(self, playlist, path):
        """Add media to playlist using request_receive to bypass xStudio's add_media API bug."""
        from xstudio.core import add_media_atom, Uuid, parse_posix_path
        from xstudio.api.session.media.media import Media
        try:
            ppp = parse_posix_path(path)
            res_vec = self.connection.request_receive(playlist.remote, add_media_atom(), path, ppp[0], Uuid())[0]
            if res_vec:
                if hasattr(res_vec, "__getitem__") and not isinstance(res_vec, (str, bytes)):
                    item = res_vec[0]
                else:
                    item = res_vec
                return Media(self.connection, item.actor, item.uuid)
        except Exception:
            _log_exc(f"_safe_add_media failed for path: {path}")
        return None

    def _async_import_worker(self, otio_file_path, playlist):
        try:
            success, msg = self.import_annotations(otio_file_path, playlist)
            if success:
                self.popup_message_box("Import Annotations Complete", msg)
            else:
                self.popup_message_box("Import Annotations", msg)
        except Exception:
            _log_exc("do_import async worker failed")
            tb = traceback.format_exc()
            try:
                sys.__stderr__.write(f"\n[ORI Annotations Error] Async import worker failed:\n{tb}\n")
                sys.__stderr__.flush()
            except Exception:
                pass
            print(f"Async import worker failed:\n{tb}", file=sys.stderr)
            self.popup_message_box("Import Annotations Failed", tb)

    # ------------------------------------------------------------------
    # Bookmark collection (export helper)
    # ------------------------------------------------------------------

    def _collect_bookmarks(self, playlist, output_dir, include_media, include_images):
        """Collect all annotated bookmarks from *playlist* into ORIAnnotations objects.

        :param playlist: xStudio playlist object (the inspected container).
        :param output_dir: Directory into which media copies and PNG renders
            will be written when *include_media* or *include_images* is set.
        :param include_media: Copy source media files into *output_dir*.
        :param include_images: Render annotation PNGs via the viewport snapshot API.
        :returns: ``(media_list, review_items, annotated_count)`` tuple.
        :rtype: tuple
        """
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

                aspect_half = self._aspect_half_for_media(media)

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
                    # xStudio frames are 0-based; store as 1-based in the OTIO
                    # clip so the annotation track aligns with the media track.
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
                        self._render_annotated_media_frame(
                            output_dir, media.name, frame - 1, bookmark
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

            except Exception:
                _log_exc(f"_collect_bookmarks: skipping '{media.name}'")
                continue

        return media_list, review_items, annotated_count

    def _bookmark_to_sync_events(self, bookmark, aspect_half=0.8889):
        """Read annotation data from *bookmark* and convert to SyncEvent objects.

        Deserialises the bookmark's raw annotation JSON via ``serialise_atom``,
        then delegates stroke and caption conversion to the shared codec in
        :mod:`otio_sync_core.xs_annotation_codec`.

        :param bookmark: xStudio ``Bookmark`` object.
        :param aspect_half: ``W / (2H)`` coordinate scale factor.
        :returns: List of SyncEvent objects, or an empty list on failure.
        :rtype: list
        """
        try:
            raw = self.connection.request_receive(bookmark.remote, serialise_atom())[0]
            ann = json.loads(raw.dump())
            annotation = ann.get("base", {}).get("annotation") or {}
            data = annotation.get("Data", {})
            strokes = data.get("pen_strokes", [])
            return (
                xs_strokes_to_sync_events(strokes, aspect_half)
                + xs_captions_to_sync_events(data.get("captions", []), aspect_half)
            )
        except Exception:
            _log_exc("_bookmark_to_sync_events: could not read annotation data")
            return []

    def _render_annotation_image(self, output_dir, media_name, frame, bookmark):
        """Render the annotation overlay for *bookmark* as a PNG file.

        :param output_dir: Directory in which to write the PNG.
        :param media_name: Media display name (used to build the filename).
        :param frame: 1-based frame number (used in the filename).
        :param bookmark: xStudio ``Bookmark`` object whose annotation to render.
        :returns: Absolute path to the rendered PNG, or ``None`` on failure.
        :rtype: str or None
        """
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
        except Exception:
            _log_exc(f"_render_annotation_image: failed for '{media_name}' frame {frame}")
            return None

    def _render_annotated_media_frame(self, output_dir, media_name, frame, bookmark):
        """Render the media frame with the annotation drawings overlayed.

        The output image contains both the underlying media frame and the drawing.
        The filename format is `<media_name_stem>.<frame:05d>.png` starting at frame 0.
        """
        stem = os.path.splitext(os.path.basename(media_name))[0]
        img_path = os.path.join(output_dir, f"{stem}.{frame:05d}.png")
        try:
            self.connection.api.app.snapshot_viewport.render_bookmark_with_transparency(
                img_path,
                bookmark.uuid,
                include_image=True,
                include_drawings=True,
            )
            return img_path
        except Exception:
            _log_exc(f"_render_annotated_media_frame: failed for '{media_name}' frame {frame}")
            return None

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

    def _aspect_half_for_media(self, media) -> float:
        """Return ``W / (2H)`` for *media*'s first image stream.

        Used to convert between xStudio's W-normalised coordinate space and
        the H-normalised space used by OTIO SyncEvents / RV.

        :param media: xStudio ``Media`` object.
        :returns: ``aspect_half`` value (e.g. ``0.8889`` for 16:9 1920×1080).
            Falls back to ``0.8889`` on any error.
        :rtype: float
        """
        try:
            ms = media.media_source()
            if ms is None:
                return 0.8889
            streams = ms.image_streams
            res = streams[0].media_stream_detail.resolution() if streams else None
            img_w, img_h = (res[0], res[1]) if res and res[1] else (1920, 1080)
        except Exception:
            img_w, img_h = 1920, 1080
        return img_w / (2.0 * img_h)


def create_plugin_instance(connection):
    return ORIAnnotationsPlugin(connection)
