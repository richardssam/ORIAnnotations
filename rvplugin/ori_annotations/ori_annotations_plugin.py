from __future__ import print_function

import os
import sys
import datetime
import tempfile
import subprocess
from pathlib import Path
import shutil
try:
    from PySide2 import QtGui, QtCore, QtWidgets
    from PySide2.QtGui import *
    from PySide2.QtCore import *
    from PySide2.QtWidgets import *
    from PySide2.QtUiTools import QUiLoader
except ImportError:
  try:
    from PySide6 import QtGui, QtCore, QtWidgets
    from PySide6.QtGui import *
    from PySide6.QtCore import *
    from PySide6.QtWidgets import *
    from PySide6.QtUiTools import QUiLoader
  except ImportError:
    pass

from rv import commands, extra_commands
from rv import rvtypes

import opentimelineio as otio
from otio_writer import get_source_node, get_movie_first_frame, get_movie_last_frame, get_movie_fps


class ORIAnnotationsPlugin(rvtypes.MinorMode):

    def set_directory(self, event):
        """
        Set the directory for OTIO event logging.
        """
        event.reject()
        dialog = QFileDialog()
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Directory for OTIO Event Log")
        dialog.setLabelText(QFileDialog.Accept, "Set Directory")
        if dialog.exec_() == QDialog.Accepted:
            directory = dialog.selectedFiles()[0]
            print("Setting OTIO event log directory to:", directory)
            self._logging_directory = directory
            commands.writeSettings("otioevents", "logging_directory", directory)
            print("OTIO event log directory set to:", self._logging_directory)
            self.setup_logging()

    def __init__(self):
        super(ORIAnnotationsPlugin, self).__init__()

        self.last_source = None
        self.logging_fh = None
        self._logging_directory = commands.readSettings("otioevents", "logging_directory", "")
        if self._logging_directory == "":
            self._logging_directory = None
        self.init(
            "otioevents",
            [
                #("graph-state-change", self.on_event, "Catch all events"),
                #("frame-changed", self.on_frame_changed, "Detect clip switch")
            ],
            None,
            [
                ("File",
                [
                    ("Import",
                    [
                        ("As OTIO Annotation Import ...", self.import_annotations, None, None),
                    ]
                    ),
                    ("Export",
                    [
                        ("OTIO Annotation Export ...", self.export_annotations, None, None),
                    ]
                    )
                ]
                )
            ]
        )


    def export_annotations(self, event):


        class QAnnotationFileDialog(QFileDialog):
            """
            Custom QFileDialog to handle the export of annotations.
            """
            def __init__(self, parent=None):
                super(QAnnotationFileDialog, self).__init__(parent)
                # Force the use of the non-native dialog
                self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
                # Create custom widgets
                self.extra_widget = QWidget()
                layout = QVBoxLayout()
                self.includeMedia = QCheckBox("Include media in export")
                self.includeMedia.setChecked(True)  # Default to checked
                self.exportAnnotationMedia = QCheckBox("Include Annotation Media")
                self.exportAnnotationMedia.setChecked(True)  # Default to checked
                #self.nestedStacks = QCheckBox("Export as Nested Stacks")
                #self.nestedStacks.setChecked(True)  # Default to Checked
                self.otioName = QLineEdit("OTIO Export Name")
                self.otioName.setText("annotationreview.otio")  # Default text
                #self.combobox = QComboBox()
                #self.combobox.addItems(["Option A", "Option B", "Option C"])

                layout.addWidget(QLabel("You are exporting annotations, media and the OTIO file to a directory."))

                layout.addWidget(QLabel("Additional Options:"))
                layout.addWidget(self.includeMedia)
                layout.addWidget(self.exportAnnotationMedia)
                #layout.addWidget(self.nestedStacks)
                layout.addWidget(QLabel("OTIO Export Name:"))
                layout.addWidget(self.otioName)
                #layout.addWidget(self.combobox)

                self.extra_widget.setLayout(layout)

                self.setFileMode(QFileDialog.Directory)
                self.setOption(QFileDialog.ShowDirsOnly, True)
                self.setWindowTitle("Select Directory to export the annotations to")
                self.setLabelText(QFileDialog.Accept, "Set Directory")

                # Get the dialog's layout and add custom widget after the standard widgets
                dialog_layout = self.layout()
                # Add at the bottom spanning all columns
                last_row = dialog_layout.rowCount()
                if hasattr(dialog_layout, 'addWidget'):
                    dialog_layout.addWidget(self.extra_widget, last_row, 0, 1, dialog_layout.columnCount())

        basepath = "/Users/sam/git/Annotations/test_export"
        dialog = QAnnotationFileDialog()
        #dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Directory to export the annotations to")
        dialog.setLabelText(QFileDialog.Accept, "Set Directory")
        if dialog.exec_() != QDialog.Accepted:
            return

        export_media = dialog.includeMedia.isChecked()
        export_annotation_media = dialog.exportAnnotationMedia.isChecked()
        as_nested_stacks = False # Disabling this for now. dialog.nestedStacks.isChecked()
        otio_export_name = dialog.otioName.text()
        if not otio_export_name.endswith(".otio"):
            otio_export_name += ".otio"
        basepath = dialog.selectedFiles()[0]

        self._export_annotations_to_directory(
            basepath, export_media, export_annotation_media,
            as_nested_stacks, otio_export_name)

    def _export_annotations_to_directory(self, basepath, export_media,
                                          export_annotation_media,
                                          as_nested_stacks, otio_export_name):
        """Headless export entry point (no Qt dialog) — callable from batch/test code."""
        from otio_sync_core import rv_annotation_codec, rv_paint_applier
        print("Setting OTIO event log directory to:", basepath)

        otio.schema.schemadef.module_from_name('SyncEvent')
        import ORIAnnotations

        frames = extra_commands.findAnnotatedFrames()
        if export_annotation_media:
            tf = tempfile.NamedTemporaryFile(suffix=".rv")
            filename = tf.name
            commands.saveSession(filename, asACopy=True)
            print("Saved to:", filename)
            frametoimages = {frame: f"{basepath}/export.{frame}.png" for frame in frames}
            cmd = [os.path.join(os.path.dirname(sys.argv[0]), "rvio"),
                filename,
                "-t",
                ",".join([str(f) for f in frames]),
                "-outsrgb",
                "-o",
                f"{basepath}/export.%d.png"
                    ]

            print("Command to run:", cmd)
            subprocess.run(cmd)

        annotations = {}
        # Loop over and build the initial structure.
        for globalframe in extra_commands.findAnnotatedFrames():
            for source in commands.sourcesAtFrame(globalframe):
                source_group = commands.nodeGroup(source)
                source_frame = extra_commands.sourceFrame(globalframe)
                paint_node = commands.metaEvaluateClosestByType(globalframe, "RVPaint")[0]['node']
                uiname = extra_commands.uiName(source_group)
                if uiname not in annotations:
                    movie = f"{source}.media.movie"
                    media_path = commands.getStringProperty(movie)[0]
                    annotations[uiname] = {'media': media_path, 
                                           'annotations': {}, 
                                           'paint_node': paint_node, 
                                           'frame_rate': get_movie_fps(source_group),
                                            'startframe': get_movie_first_frame(source_group),
                                            'duration': get_movie_last_frame(source_group) - get_movie_first_frame(source_group) +1,
                                           'source_group': source_group}
                if export_annotation_media:
                    inputframe = frametoimages[globalframe]
                    outputuiname = uiname.replace("@", "")
                    outputframe = f"{basepath}/{outputuiname}.{source_frame:05d}.png"
                    os.rename(inputframe, outputframe)
                else:
                    outputframe = None
                annotations[uiname]['annotations'][source_frame] = {'paint_node': paint_node, 'annotationframe': outputframe, 'note': ""}

        # Now we try to extract the brush strokes.
        for uiname, annotationmediainfo in annotations.items():
                print("Processing:", annotationmediainfo)
                paint_node = annotationmediainfo['paint_node']
                annotationframes = annotationmediainfo['annotations']
                for frame in annotationframes:
                    # Read RV paint-node properties → stroke dicts → SyncEvents,
                    # via the shared codec (same path the load plugin/batch use).
                    strokes = rv_paint_applier.read_frame_strokes(commands, paint_node, frame)
                    annotationframes[frame]['events'] = rv_annotation_codec.rv_strokes_to_sync_events(strokes)

        # print("Frames:", annotations)
        medialist = []
        reviewitems = []
        for uiname, annotationmediainfo in annotations.items():
            if export_media:
                media_path = annotationmediainfo['media']
                new_media_path = f"{basepath}/{os.path.basename(media_path)}"
                shutil.copy(media_path, new_media_path)
                media_path = os.path.basename(new_media_path)
            else:
                media_path_p = Path(media_path)
                if not media_path_p.is_absolute():
                    # We try to make it a relative path, to make it as portable as possible.
                    try:
                        media_path = media_path_p.relative_to(basepath).as_posix()
                    except ValueError:
                        media_path = media_path_p.absolute().as_posix()
            print("Media path for review item:", media_path, "UINAME:", uiname)
            media = ORIAnnotations.Media(media_path=media_path, 
                                 name=uiname, 
                                 frame_rate=annotationmediainfo['frame_rate'], 
                                 start_frame=annotationmediainfo['startframe'],
                                 duration=annotationmediainfo['duration']
                                 )
            medialist.append(media)
            ri = ORIAnnotations.ReviewItem(media=media)
            reviewitems.append(ri)
            # The annotation dict is keyed by the RV source frame (which carries
            # embedded timecode, e.g. 96899). Write portable 1-based media-local
            # frames to OTIO — the convention the import path and xStudio expect.
            source_start = annotations[uiname].get('startframe') or 1
            frames = []
            for rv_source_frame, frameinfo in annotations[uiname]['annotations'].items():
                otio_frame = rv_annotation_codec.rv_frame_to_media_local(
                    rv_source_frame, source_start)
                frameobj = ORIAnnotations.ReviewItemFrame(note=frameinfo['note'],
                                                          annotation_commands=frameinfo['events'],
                                                          annotation_image=frameinfo['annotationframe'],
                                                          frame=otio_frame, review_item=ri)
                frames.append(frameobj)
            ri.review_frames = frames

        review = ORIAnnotations.Review(title="Review", review_items=reviewitems)
        print("MediaList:", medialist)
        reviewgroup = ORIAnnotations.ReviewGroup(media=medialist, reviews=[review])
        timeline = reviewgroup.export_otio_timeline(as_nested_stacks=as_nested_stacks)

        otio.adapters.write_to_file(timeline, f"{basepath}/{otio_export_name}")
        print("Exported to:", f"{basepath}/{otio_export_name}")

    def import_annotations(self, event):
        otiofile = "/Users/sam/git/Annotations/test_export/test_export.otio"
        dialog = QFileDialog()
        dialog.setNameFilter("OpenTimelineIO (*.otio)")
        dialog.setWindowTitle("Pick the OTIO Annotation review file to load")
        dialog.setLabelText(QFileDialog.Accept, "Import")
        if dialog.exec_() == QDialog.Accepted:
            otiofile = dialog.selectedFiles()[0]
        else:
            return

        self._import_annotations_from_file(otiofile)

    def _import_annotations_from_file(self, otiofile):
        """Headless import entry point (no Qt dialog) — callable from batch/test code.

        Mirrors the proven batch pipeline (testchart/batch_openrv_helper.py):
        annotations are written to each media's own sequence-level paint node
        (``defaultSequence_p_<sourceGroup>*``). Those paint nodes use the
        source's RV frame numbering, which starts at the media's start frame —
        carrying any embedded timecode — so each 1-based media-local review
        frame is mapped onto that numbering before being applied.
        """
        from otio_sync_core import rv_annotation_codec, rv_paint_applier
        otio.schema.schemadef.module_from_name('SyncEvent')
        import otio_reader
        import ORIAnnotations

        newtimeline = otio.adapters.read_from_file(otiofile)

        # Convert file:// target URLs to plain absolute POSIX paths for OpenRV.
        from urllib.parse import urlparse, unquote
        for clip in newtimeline.find_clips():
            if clip.media_reference and isinstance(clip.media_reference, otio.schema.ExternalReference):
                url = clip.media_reference.target_url
                if url and url.startswith("file:"):
                    parsed = urlparse(url)
                    path = unquote(parsed.path)
                    if path.startswith("//"):
                        path = path[1:]
                    clip.media_reference.target_url = path

        rg = ORIAnnotations.ReviewGroup()
        rg.read_otio_timeline(newtimeline)

        # Load the media track (first track named "Media", else the first track).
        media_track = next((t for t in newtimeline.tracks if t.name == "Media"), None)
        if not media_track and len(newtimeline.tracks) > 0:
            media_track = newtimeline.tracks[0]

        commands.addSourceBegin()
        context = {"otio_file": otiofile}
        otio_reader._create_track(media_track, context)
        commands.addSourceEnd()
        # The sequence-level paint nodes live under "defaultSequence_p_*" — the
        # view the annotations render in — regardless of the media track's name.
        new_seq = "defaultSequence"
        commands.setViewNode(new_seq)

        # Build clip map using sequence-level paint nodes. Annotations must go to
        # defaultSequence_p_<sourceGroup>* nodes — the ones that render in the
        # sequence view. These paint nodes are per-source, so the review frame
        # value is applied directly.
        all_paint_nodes = commands.nodesOfType("RVPaint")
        seq_paint_map = {}  # sourceGroupName → sequence-level paint node
        prefix = f"{new_seq}_p_"
        for pn in all_paint_nodes:
            if not pn.startswith(prefix):
                continue
            sg = pn[len(prefix):].replace("_switchGroup", "")
            seq_paint_map[sg] = pn

        sg_to_media = {}
        for sourcenode in commands.nodesOfType("RVFileSource"):
            sg = commands.nodeGroup(sourcenode)
            movie_prop = f"{sourcenode}.media.movie"
            if commands.propertyExists(movie_prop):
                movie = commands.getStringProperty(movie_prop)[0]
                if movie:
                    sg_to_media[sg] = movie

        clipmap = {}
        for sg, media_path in sg_to_media.items():
            paint_node = seq_paint_map.get(sg)
            # RV numbers a source's frames from its media start frame, which for
            # media carrying embedded timecode is NOT 1 (e.g. 96899). The
            # per-source paint node uses that same source-local numbering, so we
            # need the start frame to place annotations on the right picture.
            try:
                source_start = get_movie_first_frame(sg)
            except Exception:
                source_start = None
            if source_start is None:
                source_start = 1
            clipinfo = {'media_path': media_path, 'paint_node': paint_node,
                        'source_start': int(source_start)}
            clipmap[media_path] = clipinfo
            clipmap[os.path.basename(media_path)] = clipinfo
            clipmap[os.path.splitext(os.path.basename(media_path))[0]] = clipinfo

        applied = 0
        for review in rg.reviews:
            for ri in review.review_items:
                strokeid = 1
                media_key = ri.media.media_path
                clipinfo = clipmap.get(media_key)
                if not clipinfo:
                    clipinfo = clipmap.get(os.path.basename(media_key))
                if not clipinfo:
                    clipinfo = clipmap.get(os.path.splitext(os.path.basename(media_key))[0])
                if not clipinfo:
                    print(f"ORIAnnotations: media not found in RV session for {media_key}")
                    continue

                rv_node = clipinfo['paint_node']
                if not rv_node:
                    print(f"ORIAnnotations: no paint node found for {media_key}")
                    continue

                source_start = clipinfo.get('source_start', 1)
                for frame in ri.review_frames:
                    if not frame.annotation_commands:
                        continue

                    # frame.frame is a 1-based media-local frame. Map it onto the
                    # source's RV frame numbering (which starts at source_start,
                    # carrying any embedded timecode) so the annotation lands on
                    # the right picture. For no-timecode media source_start == 1,
                    # so this reduces to frame.frame.
                    rv_frame = rv_annotation_codec.media_local_to_rv_frame(
                        frame.frame, source_start)
                    specs = rv_annotation_codec.sync_events_to_rv_specs(
                        frame.annotation_commands, {"frame": rv_frame})
                    try:
                        strokeid = rv_paint_applier.apply_specs(
                            specs, commands, rv_node=rv_node, frame=rv_frame,
                            mode="append", start_id=strokeid)
                        applied += 1
                    except Exception as e:
                        print(f"ORIAnnotations: error applying annotation to "
                              f"{media_key} frame {rv_frame}: {e}")

        # Force RV to refresh/update the display after applying all specs.
        commands.redraw()
        print(f"ORIAnnotations: imported {applied} annotation frame(s) from "
              f"{os.path.basename(otiofile)}")



def createMode():
    support_files_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "..", "Python", "otio_event_plugin"
    )
    print("Support files Annotation PLUGIN:", support_files_path, os.path.realpath(__file__))
    #print("About to run:", otio_mu)
    #commands.eval(otio_mu)

    manifest_path = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if manifest_path:
        manifest_path += os.pathsep
    os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.path.join(
        support_files_path, "plugin_manifest.json"
    )
    print("PLUGINS:", os.environ["OTIO_PLUGIN_MANIFEST_PATH"])
    sys.path.append(support_files_path)

    # Force OTIO to reload the plugin manifest after setting the environment variable
    try:
        otio.plugins.manifest._MANIFEST = None
    except:
        pass

    return ORIAnnotationsPlugin()

