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
                        ("Import annotations", self.import_annotations, None, None),
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
        dialog = QFileDialog()
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Directory to export the annotations to")
        dialog.setLabelText(QFileDialog.Accept, "Set Directory")
        if dialog.exec_() == QDialog.Accepted:
            basepath = dialog.selectedFiles()[0]
            print("Setting OTIO event log directory to:", basepath)

        import ORIAnnotations

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
                    if ".frame:" in prop:
                        frame = int(prop.split(".frame:")[1].split(".order")[0])
                        order = commands.getStringProperty(prop)
                        print("Frame:", frame, " order:", order, uiname, paint_node)
                        print("existing frames:", annotationframes.keys())
                        annotationframes[frame]['order'] = order
                for frame in annotationframes:
                    strokes = []
                    otioevents = []
                    for order in annotationframes[frame]['order']:
                        baseprop = f'{paint_node}.{order}'
                        #print("Base prop:", baseprop)
                        stroke = {'paint_node': paint_node}
                        if order.startswith("pen:"):
                            stroke['stroketype'] = 'pen'
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
                                                                        friendly_name=baseprop.split(':')[-1],
                                                                        uuid=penuuid
                                                                        )
                            if  commands.propertyExists(f"{baseprop}.mode") and commands.getIntProperty(f"{baseprop}.mode")[0] == 1:
                                event.type = 'erase'
                                stroke['stroketype'] = 'erase'
                            otioevents.append(event)
                            points = stroke['points']
                            outpointlist = []
                            if len(stroke['width']) == 1:
                                # If we have a single width, we assume it's the same for all points.
                                stroke['width'] = [stroke['width'][0]] * (len(points) // 2)
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
                            stroke['stroketype'] = 'text'
                            stroke['user'] = order.split(":")[-1]
                            stroke['position'] = commands.getFloatProperty(f"{baseprop}.position")
                            stroke['color'] = commands.getFloatProperty(f"{baseprop}.color")
                            stroke['spacing'] = commands.getFloatProperty(f"{baseprop}.spacing")[0]
                            stroke['font_size'] = commands.getFloatProperty(f"{baseprop}.size")[0]
                            stroke['font'] = commands.getStringProperty(f"{baseprop}.font")[0]
                            stroke['text'] = commands.getStringProperty(f"{baseprop}.text")[0]
                            stroke['scale'] = commands.getFloatProperty(f"{baseprop}.scale")[0]
                            stroke['rotation'] = commands.getFloatProperty(f"{baseprop}.rotation")[0]

                            textuuid = str(uuid.uuid4())
                            event = otio.schemadef.SyncEvent.TextAnnotation(
                                                                        rgba=stroke['color'],
                                                                        position=stroke['position'],
                                                                        spacing=stroke['spacing'],
                                                                        friendly_name=stroke['user'],
                                                                        font_size=stroke['font_size'],
                                                                        font=stroke['font'],
                                                                        text=stroke['text'],
                                                                        rotation=stroke['rotation'],
                                                                        scale=stroke['scale'],
                                                                        uuid=textuuid
                                                                        )
                            otioevents.append(event)
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

        otio.adapters.write_to_file(timeline, f"{basepath}/annotationreview.otio")
        print("Exported to:", f"{basepath}/annotationreview.otio")

    def import_annotations(self, event):
        import opentimelineio as otio
        otiofile = "/Users/sam/git/Annotations/test_export/test_export.otio"
        dialog = QFileDialog()
        dialog.setNameFilter("OpenTimelineIO (*.otio)")
        dialog.setWindowTitle("Pick the OTIO Annotation review file to load")
        dialog.setLabelText(QFileDialog.Accept, "Import")
        if dialog.exec_() == QDialog.Accepted:
            otiofile = dialog.selectedFiles()[0]
        else:
            return
        
        import otio_reader
        import ORIAnnotations
        otio.schema.schemadef.module_from_name('SyncEvent')

        commands.addSourceBegin()
        newtimeline = otio.adapters.read_from_file(otiofile)
        print("Read OTIO file:", otiofile)
        rg = ORIAnnotations.ReviewGroup()
        rg.read_otio_timeline(newtimeline)
        context = {"otio_file": otiofile}
        new_seq = otio_reader._create_track(newtimeline.tracks[0], context)
        print("NEW SEQ:", new_seq)
        # new_seq = "DefaultSequence"  # We assume the first track is the one we want to use.
        # We want a mapping from the source group to the media file.
        clipmap = {}
        for media in rg.media:
            clipmap[media.media_path] = {'media': media.media_path,
                                         'media': media}
            
        paintnodes = extra_commands.nodesInGroupOfType(new_seq, 'RVPaint')


        paintnodes = extra_commands.nodesInGroupOfType(new_seq, 'RVPaint')
        sourcenodes = commands.nodesOfType("RVFileSource")
        for sourcenode in sourcenodes:
            print("Source node:", sourcenode)
            sourcegroup = commands.nodeGroup(sourcenode)
            if sourcegroup not in clipmap:
                media = commands.getStringProperty(f"{sourcenode}.media.movie")[0]
                if media is None or len(media) == 0:
                    continue
                media = media.replace("file://", "")
                print("Media for source node:", media)
                paintnodes = extra_commands.nodesInGroupOfType(sourcegroup, 'RVPaint')
                clipmap[media] = {'media': media, 'source_group': sourcegroup, 'paint_node': paintnodes[0] if paintnodes else None}
            else:
                print("Source group already in clipmap:", sourcegroup)

        for review in rg.reviews:
            for ri in review.review_items:
                strokeid = 1
                for frame in ri.review_frames:
                    if frame.annotation_commands is None:
                        continue
                    if ri.media.media_path not in clipmap:
                        print("WARNING: Media not found in clipmap for review item:", ri.media.media_path)
                        continue
                    print("Processing review item:", ri.media.media_path, "Frame:", frame.frame)
                    clipinfo = clipmap[ri.media.media_path]

                    strokes = []
                    stroke = {}
                    strokemap = {}

                    for event in frame.annotation_commands:
                        if isinstance(event, otio.schemadef.SyncEvent.PaintStart):
                            stroke = {'type': event.type,
                                      'color': event.rgba,
                                      'brush': event.brush,
                                      'user': event.friendly_name.split(":")[-1],
                                      'width': [],
                                      'points': [],}
                            strokemap[event.uuid] = stroke
                            strokes.append(stroke)
                        if isinstance(event, otio.schemadef.SyncEvent.PaintPoint):
                            stroke = strokemap[event.uuid]
                            for point in event.point:
                                stroke['points'].append(point.x)
                                stroke['points'].append(point.y)
                                stroke['width'].append(point.size)
                        if isinstance(event, otio.schemadef.SyncEvent.PaintEnd):
                            stroke = strokemap[event.uuid]
                            stroke['points'].append(event.point.x)
                            stroke['points'].append(event.point.y)
                            stroke['width'].append(event.point.size)

                        if isinstance(event, otio.schemadef.SyncEvent.TextAnnotation):
                            print(" TextAnnotation Event:", event)
                            strokemap[event.uuid] = {'type': 'text'}
                            stroke = strokemap[event.uuid]
                            strokes.append(stroke)
                            stroke['type'] = 'text'
                            stroke['color'] = event.rgba
                            stroke['user'] = event.friendly_name
                            stroke['position'] = event.position
                            stroke['spacing'] = event.spacing
                            stroke['font_size'] = event.font_size
                            stroke['font'] = event.font
                            stroke['text'] = event.text
                            stroke['scale'] = event.scale
                            stroke['rotation'] = event.rotation

                    print("Annotations for frame:", frame.frame, "Strokes:", len(strokes))
                    rv_node = clipinfo['paint_node']
            
                    if not commands.propertyExists(f"{rv_node}.tag.annotate"):
                        commands.newProperty(f"{rv_node}.tag.annotate", commands.StringType, 1)
                    commands.setStringProperty(
                                f"{rv_node}.tag.annotate", [''], True
                    )            
                    if not commands.propertyExists(f"{rv_node}.internal.creationContext"):
                        commands.newProperty(f"{rv_node}.internal.creationContext", commands.IntType, 1)
                    commands.setIntProperty(
                                f"{rv_node}.internal.creationContext", [1], True
                    )


                    order = []
                    for stroke in strokes:
                        if stroke['type'] == 'text':
                            text_node = f"{rv_node}.text:{strokeid}:{int(frame.frame)}:{stroke['user']}"
                            print("Text Node:", text_node, stroke)
                            if not commands.propertyExists(f"{text_node}.position"):
                                commands.newProperty(f"{text_node}.position", commands.FloatType, 2)
                            commands.setFloatProperty(
                                f"{text_node}.position", [x for x in stroke['position']], True
                            )
                            if not commands.propertyExists(f"{text_node}.color"):
                                commands.newProperty(f"{text_node}.color", commands.FloatType, 4)
                            commands.setFloatProperty(
                                f"{text_node}.color", [float(x) for x in stroke['color']], True
                            )
                            if not commands.propertyExists(f"{text_node}.spacing"):
                                commands.newProperty(f"{text_node}.spacing", commands.FloatType, 1)
                            commands.setFloatProperty(
                                f"{text_node}.spacing", [stroke['spacing']], True
                            )
                            if not commands.propertyExists(f"{text_node}.size"):
                                commands.newProperty(f"{text_node}.size", commands.FloatType, 1)
                            commands.setFloatProperty(
                                f"{text_node}.size", [stroke['font_size']], True
                            )
                            if not commands.propertyExists(f"{text_node}.font"):
                                commands.newProperty(f"{text_node}.font", commands.StringType, 1)
                            commands.setStringProperty(
                                f"{text_node}.font", [stroke['font']], True
                            )
                            if not commands.propertyExists(f"{text_node}.text"):
                                commands.newProperty(f"{text_node}.text", commands.StringType, 1)
                            commands.setStringProperty(
                                f"{text_node}.text", [stroke['text']], True
                            )
                            if not commands.propertyExists(f"{text_node}.scale"):
                                commands.newProperty(f"{text_node}.scale", commands.FloatType, 1)
                            commands.setFloatProperty(
                                f"{text_node}.scale", [stroke['scale']], True
                            )
                            if not commands.propertyExists(f"{text_node}.rotation"):
                                commands.newProperty(f"{text_node}.rotation", commands.FloatType, 1)
                            commands.setFloatProperty(
                                f"{text_node}.rotation", [stroke['rotation']], True
                            )
                            order.append(f"text:{strokeid}:{int(frame.frame)}:{stroke['user']}")
                            strokeid += 1
                            continue
                        paint_node = f"{rv_node}.paint"
                        pen_node = f"{rv_node}.pen:{strokeid}:{int(frame.frame)}:{stroke['user']}"
                        frame_node = f"{rv_node}.frame:{int(frame.frame)}"

                        if not commands.propertyExists(f"{pen_node}.brush"):
                            commands.newProperty(f"{pen_node}.brush", commands.StringType, 1)

                        commands.setStringProperty(f"{pen_node}.brush", [stroke['brush']], True)

                        if not commands.propertyExists(f"{pen_node}.color"):
                            commands.newProperty(f"{pen_node}.color", commands.FloatType, 4)

                        commands.setFloatProperty(
                            f"{pen_node}.color", [float(x) for x in stroke['color']], True
                        )

                        if not commands.propertyExists(f"{pen_node}.debug"):
                            commands.newProperty(f"{pen_node}.debug", commands.IntType, 1)

                            commands.setIntProperty(f"{pen_node}.debug", [False], True)

                        if not commands.propertyExists(f"{pen_node}.join"):
                            commands.newProperty(f"{pen_node}.join", commands.IntType, 1)

                        commands.setIntProperty(f"{pen_node}.join", [3], True)

                        if not commands.propertyExists(f"{pen_node}.cap"):
                            commands.newProperty(f"{pen_node}.cap", commands.IntType, 1)

                        commands.setIntProperty(f"{pen_node}.cap", [1], True)

                        if not commands.propertyExists(f"{pen_node}.splat"):
                            commands.newProperty(f"{pen_node}.splat", commands.IntType, 1)

                        commands.setIntProperty(f"{pen_node}.splat", [0], True)

                        if stroke['type'] == 'erase':
                            if not commands.propertyExists(f"{pen_node}.mode"):
                                commands.newProperty(f"{pen_node}.mode", commands.IntType, 1)

                            commands.setIntProperty(
                                f"{pen_node}.mode", [1], True
                            )


                        if not commands.propertyExists(f"{pen_node}.width"):
                            commands.newProperty(f"{pen_node}.width", commands.FloatType, 1)

                        commands.setFloatProperty(
                            f"{pen_node}.width", stroke['width'], True
                        )

                        if not commands.propertyExists(f"{pen_node}.points"):
                            commands.newProperty(f"{pen_node}.points", commands.FloatType, 2)

                        commands.setFloatProperty(
                            f"{pen_node}.points", stroke['points'], True
                        )

                        order.append(f"pen:{strokeid}:{int(frame.frame)}:{stroke['user']}")
                        strokeid += 1
                    
                    if not commands.propertyExists(f"{frame_node}.order"):
                        commands.newProperty(f"{frame_node}.order", commands.StringType, 1)

                    commands.setStringProperty(f"{frame_node}.order", order, True)
                    
                commands.setIntProperty(
                            f"{paint_node}.nextId", [strokeid], False
                        )
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

    return ORIAnnotationsPlugin()

