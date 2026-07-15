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
                self.nestedStacks = QCheckBox("Export as Nested Stacks")
                self.nestedStacks.setChecked(True)  # Default to Checked
                self.otioName = QLineEdit("OTIO Export Name")
                self.otioName.setText("annotationreview.otio")  # Default text
                #self.combobox = QComboBox()
                #self.combobox.addItems(["Option A", "Option B", "Option C"])

                layout.addWidget(QLabel("You are exporting annotations, media and the OTIO file to a directory."))

                layout.addWidget(QLabel("Additional Options:"))
                layout.addWidget(self.includeMedia)
                layout.addWidget(self.exportAnnotationMedia)
                layout.addWidget(self.nestedStacks)
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
        as_nested_stacks = dialog.nestedStacks.isChecked()
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
            frames = []
            for frame, frameinfo in annotations[uiname]['annotations'].items():
                frame = ORIAnnotations.ReviewItemFrame(note=frameinfo['note'], 
                                                       annotation_commands=frameinfo['events'],
                                                       annotation_image=frameinfo['annotationframe'], 
                                                       frame=frame, review_item=ri)
                frames.append(frame)
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
        """Headless import entry point (no Qt dialog) — callable from batch/test code."""
        from otio_sync_core import rv_annotation_codec, rv_paint_applier
        otio.schema.schemadef.module_from_name('SyncEvent')
        import otio_reader
        import ORIAnnotations

        commands.addSourceBegin()
        newtimeline = otio.adapters.read_from_file(otiofile)
        print("Read OTIO file:", otiofile)
        rg = ORIAnnotations.ReviewGroup()
        rg.read_otio_timeline(newtimeline)
        context = {"otio_file": otiofile}
        new_seq = otio_reader._create_track(newtimeline.tracks[0], context)
        commands.addSourceEnd()
        print("NEW SEQ:", new_seq)

        # Build clipmap using sequence-level paint nodes.
        # Annotations must go to defaultSequence_p_<sourceGroup>* nodes.
        all_paint_nodes = commands.nodesOfType("RVPaint")
        seq_paint_map = {}
        for pn in all_paint_nodes:
            if not pn.startswith("defaultSequence_p_"):
                continue
            sg = pn[len("defaultSequence_p_"):].replace("_switchGroup", "")
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
            print(f"Source group {sg}: media={os.path.basename(media_path)} paint_node={paint_node}")
            clipinfo = {'media_path': media_path, 'paint_node': paint_node}
            clipmap[media_path] = clipinfo
            clipmap[os.path.basename(media_path)] = clipinfo
        print("Clipmap:", clipmap)

        for review in rg.reviews:
            for ri in review.review_items:
                if ri.media.media_path not in clipmap:
                    print("WARNING: Media not found in clipmap for review item:", ri.media.media_path)
                    continue
                rv_node = clipmap[ri.media.media_path]['paint_node']
                if not rv_node:
                    print(f"Warning: No paint node found for {ri.media.media_path}")
                    continue

                strokeid = 1
                for frame in ri.review_frames:
                    if frame.annotation_commands is None:
                        continue
                    print("Processing review item:", ri.media.media_path, "Frame:", frame.frame)

                    # Convert SyncEvents → RV paint-node specs (pure codec) and
                    # write them via the shared applier — the same code path
                    # the testchart batch and live-sync plugin use.
                    rv_frame = int(frame.frame)
                    specs = rv_annotation_codec.sync_events_to_rv_specs(
                        frame.annotation_commands, {"frame": rv_frame})
                    strokeid = rv_paint_applier.apply_specs(
                        specs, commands, rv_node=rv_node, frame=rv_frame,
                        mode="append", start_id=strokeid)
        commands.addSourceEnd()



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

