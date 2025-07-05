from __future__ import print_function

import os
import sys
import datetime
import tempfile
import subprocess
import uuid
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
import ORIAnnotations
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
                ("Tools",
                [
                        #("Set OTIO Event Log Directory", self.set_directory, None, None),
                        ("Export annotations", self.export_annotations, None, None),
                        #("test Module", self.test_module, None, None),
                        #("test Export", self.test_export, None, None),
                        #("OTIO Event Logging", self.enable_event_logging, None, None),
                ]
                )
            ]
        )


    def export_annotations(self, event):

        basepath = "/Users/sam/git/Annotations/test_export"

        otio.schema.schemadef.module_from_name('SyncEvent')

        frames = extra_commands.findAnnotatedFrames()
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
                print("ANNOTATION:", globalframe, source, source_group, source_frame, paint_node, uiname)
                if uiname not in annotations:
                    movie = f"{source}.media.movie"
                    media_path = commands.getStringProperty(movie)[0]
                    print("Media:", media_path)
                    annotations[uiname] = {'media': media_path, 
                                           'annotations': {}, 
                                           'paint_node': paint_node, 
                                           'frame_rate': get_movie_fps(source_group),
                                            'startframe': get_movie_first_frame(source_group),
                                            'duration': get_movie_last_frame(source_group) - get_movie_first_frame(source_group) +1,
                                           'source_group': source_group}
                inputframe = frametoimages[globalframe]
                outputuiname = uiname.replace("@", "")
                outputframe = f"{basepath}/{outputuiname}.{source_frame:05d}.png"
                os.rename(inputframe, outputframe)
                annotations[uiname]['annotations'][source_frame] = {'paint_node': paint_node, 'strokes': [], 'annotationframe': outputframe, 'note': ""}

        # Now we try to extract the brush strokes.
        for uiname, annotationmediainfo in annotations.items():
                print("Processing:", annotationmediainfo)
                paint_node = annotationmediainfo['paint_node']
                annotationframes = annotationmediainfo['annotations']
                for prop in commands.properties(paint_node):
                    print("Prop:", prop)
                    if ".frame:" in prop:
                        frame = int(prop.split(".frame:")[1].split(".order")[0])
                        order = commands.getStringProperty(prop)
                        print("Frame:", frame, " order:", order, uiname, paint_node)
                        print("existing frames:", annotationframes.keys())
                        annotationframes[frame]['order'] = order
                for frame in annotationframes:
                    print("Processing frame:", frame)
                    strokes = []
                    otioevents = []
                    for order in annotationframes[frame]['order']:
                        baseprop = f'{paint_node}.{order}'
                        #print("Base prop:", baseprop)
                        stroke = {'paint_node': paint_node}
                        if order.startswith("pen:"):
                            stroke['type'] = 'pen'
                            stroke['width'] = commands.getFloatProperty(f"{baseprop}.width")
                            stroke['color'] = commands.getFloatProperty(f"{baseprop}.color")
                            stroke['points'] = commands.getFloatProperty(f"{baseprop}.points")
                            stroke['join'] = commands.getIntProperty(f"{baseprop}.join")[0]
                            stroke['cap'] = commands.getIntProperty(f"{baseprop}.cap")[0]
                            stroke['splat'] = commands.getIntProperty(f"{baseprop}.splat")[0]
                            stroke['brush'] = commands.getStringProperty(f"{baseprop}.brush")[0]
                            penuuid = str(uuid.uuid4())
                            event = otio.schemadef.SyncEvent.PaintStart(brush=stroke['brush'],
                                                                        rgba=stroke['color'],
                                                                        friendly_name=baseprop,
                                                                        uuid=penuuid
                                                                        )
                            otioevents.append(event)
                            points = stroke['points']
                            outpointlist = []
                            for width in stroke['width']:
                                x = points.pop(0)
                                y = points.pop(0)
                                p = otio.schemadef.SyncEvent.PaintVertex(x, y, width)
                                outpointlist.append(p)
                            
                            event = otio.schemadef.SyncEvent.PaintPoint(
                                uuid=penuuid,
                                point=outpointlist
                            )
                            otioevents.append(event)

                        if order.startswith("text:"):
                            stroke['type'] = 'text'
                            stroke['position'] = commands.getFloatProperty(f"{baseprop}.position")
                            stroke['color'] = commands.getFloatProperty(f"{baseprop}.color")
                            stroke['spacing'] = commands.getFloatProperty(f"{baseprop}.spacing")
                            stroke['size'] = commands.getFloatProperty(f"{baseprop}.size")
                            stroke['font'] = commands.getStringProperty(f"{baseprop}.font")[0]
                            stroke['text'] = commands.getStringProperty(f"{baseprop}.text")[0]
                        strokes.append(stroke)
                    annotationframes[frame]['strokes'] = strokes
                    annotationframes[frame]['events'] = otioevents

        # print("Frames:", annotations)
        medialist = []
        reviewitems = []
        for uiname, annotationmediainfo in annotations.items():
            media = ORIAnnotations.Media(media_path=annotationmediainfo['media'], 
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
        timeline = reviewgroup.export_otio_timeline()

        otio.adapters.write_to_file(timeline, "/Users/sam/git/Annotations/test_export/test_export2.otio")

def createMode():
    support_files_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "..", "Python", "ori_annotations_plugin"
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
    sys.path.append(support_files_path)

    return ORIAnnotationsPlugin()

