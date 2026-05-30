#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Batch script helper for OpenRV execution.
This module is designed to be executed inside OpenRV via -pyeval.
"""

import os
import sys
import time
import tempfile
import subprocess
import uuid

# Force prints to go to the terminal stdout stream (sys.__stdout__) immediately
def print(*args, **kwargs):
    msg = " ".join(str(arg) for arg in args) + "\n"
    try:
        sys.__stdout__.write(msg)
        sys.__stdout__.flush()
    except Exception:
        pass

def run_batch():
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide6 import QtCore
    
    app = QtCore.QCoreApplication.instance()
    print(f"RV Batch: QCoreApplication instance = {app}")
    
    if app is not None:
        print("RV Batch: Scheduling via QTimer.singleShot...")
        QtCore.QTimer.singleShot(1000, _run_batch_impl)
    else:
        from rv import commands
        print("RV Batch: No Qt application yet. Binding to session-began event...")
        commands.bind("session-began", lambda event: _run_batch_impl())

def _run_batch_impl():
    try:
        from rv import commands, extra_commands
        import opentimelineio as otio
        import otio_reader
        import ORIAnnotations
        
        # Read parameters from environment variables
        otio_path = os.environ.get("BATCH_OTIO_PATH")
        output_dir = os.environ.get("BATCH_OUTPUT_DIR")
        
        if not otio_path or not output_dir:
            print("Error: BATCH_OTIO_PATH or BATCH_OUTPUT_DIR not set in environment.")
            commands.quit()
            return
            
        otio_path = os.path.abspath(otio_path)
        output_dir = os.path.abspath(output_dir)
        
        print(f"RV Batch: Processing {otio_path} -> {output_dir}")
        
        # Clear/initialize session
        commands.clearSession()
        
        # Import the OTIO file
        newtimeline = otio.adapters.read_from_file(otio_path)
        
        # Convert file:// target URLs to plain absolute POSIX paths for OpenRV compatibility
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
        
        commands.addSourceBegin()
        context = {"otio_file": otio_path}
        
        # Find the media track
        media_track = next((t for t in newtimeline.tracks if t.name == "Media"), None)
        if not media_track and len(newtimeline.tracks) > 0:
            media_track = newtimeline.tracks[0]
            
        # Create track and load media
        otio_reader._create_track(media_track, context)
        commands.addSourceEnd()
        
        # Allow a brief moment for RV to populate sources and groups
        time.sleep(1.0)
        
        # Build clip map of media paths to RV source groups/paint nodes
        clipmap = {}
        sourcenodes = commands.nodesOfType("RVFileSource")
        for sourcenode in sourcenodes:
            sourcegroup = commands.nodeGroup(sourcenode)
            movie = f"{sourcenode}.media.movie"
            if commands.propertyExists(movie):
                media_path = commands.getStringProperty(movie)[0]
                if media_path:
                    paintnodes = extra_commands.nodesInGroupOfType(sourcegroup, 'RVPaint')
                    clipinfo = {
                        'media_path': media_path,
                        'source_group': sourcegroup,
                        'paint_node': paintnodes[0] if paintnodes else None
                    }
                    clipmap[media_path] = clipinfo
                    clipmap[os.path.basename(media_path)] = clipinfo
                    clipmap[os.path.splitext(os.path.basename(media_path))[0]] = clipinfo
                    
        # Apply annotations (logic matching the RV plugin)
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
                    print(f"Warning: Media not found in RV session for {media_key}")
                    continue
                    
                rv_node = clipinfo['paint_node']
                if not rv_node:
                    print(f"Warning: No paint node found for source {clipinfo['source_group']}")
                    continue
                    
                for frame in ri.review_frames:
                    if not frame.annotation_commands:
                        continue
                        
                    strokes = []
                    strokemap = {}
                    for event in frame.annotation_commands:
                        if isinstance(event, otio.schemadef.SyncEvent.PaintStart):
                            stroke = {
                                'type': event.type,
                                'color': event.rgba,
                                'brush': event.brush,
                                'user': event.friendly_name.split(":")[-1] if event.friendly_name else "user",
                                'width': [],
                                'points': []
                            }
                            strokemap[event.uuid] = stroke
                            strokes.append(stroke)
                        elif isinstance(event, otio.schemadef.SyncEvent.PaintPoints):
                            stroke = strokemap.get(event.uuid)
                            if stroke and event.points:
                                stroke['width'] = [v for v in event.points.size]
                                stroke['points'] = [val for pair in zip(event.points.x, event.points.y) for val in pair]
                        elif isinstance(event, otio.schemadef.SyncEvent.PaintEnd):
                            stroke = strokemap.get(event.uuid)
                            if stroke and event.points:
                                stroke['width'].extend(event.points.size)
                                stroke['points'].extend([val for pair in zip(event.points.x, event.points.y) for val in pair])
                        elif isinstance(event, otio.schemadef.SyncEvent.TextAnnotation):
                            stroke = {
                                'type': 'text',
                                'color': event.rgba,
                                'user': event.friendly_name or "user",
                                'position': event.position,
                                'spacing': event.spacing,
                                'font_size': event.font_size,
                                'font': event.font,
                                'text': event.text,
                                'scale': event.scale,
                                'rotation': event.rotation
                            }
                            strokes.append(stroke)

                    # Create properties if they don't exist
                    if not commands.propertyExists(f"{rv_node}.tag.annotate"):
                        commands.newProperty(f"{rv_node}.tag.annotate", commands.StringType, 1)
                    commands.setStringProperty(f"{rv_node}.tag.annotate", [''], True)
                    if not commands.propertyExists(f"{rv_node}.internal.creationContext"):
                        commands.newProperty(f"{rv_node}.internal.creationContext", commands.IntType, 1)
                    commands.setIntProperty(f"{rv_node}.internal.creationContext", [1], True)

                    order = []
                    for stroke in strokes:
                        if stroke['type'] == 'text':
                            text_node = f"{rv_node}.text:{strokeid}:{int(frame.frame)}:{stroke['user']}"
                            if not commands.propertyExists(f"{text_node}.position"):
                                commands.newProperty(f"{text_node}.position", commands.FloatType, 2)
                            commands.setFloatProperty(f"{text_node}.position", [x for x in stroke['position']], True)
                            if not commands.propertyExists(f"{text_node}.color"):
                                commands.newProperty(f"{text_node}.color", commands.FloatType, 4)
                            commands.setFloatProperty(f"{text_node}.color", [float(x) for x in stroke['color']], True)
                            if not commands.propertyExists(f"{text_node}.spacing"):
                                commands.newProperty(f"{text_node}.spacing", commands.FloatType, 1)
                            commands.setFloatProperty(f"{text_node}.spacing", [stroke['spacing']], True)
                            if not commands.propertyExists(f"{text_node}.size"):
                                commands.newProperty(f"{text_node}.size", commands.FloatType, 1)
                            commands.setFloatProperty(f"{text_node}.size", [stroke['font_size']], True)
                            if not commands.propertyExists(f"{text_node}.font"):
                                commands.newProperty(f"{text_node}.font", commands.StringType, 1)
                            commands.setStringProperty(f"{text_node}.font", [stroke['font']], True)
                            if not commands.propertyExists(f"{text_node}.text"):
                                commands.newProperty(f"{text_node}.text", commands.StringType, 1)
                            commands.setStringProperty(f"{text_node}.text", [stroke['text']], True)
                            if not commands.propertyExists(f"{text_node}.scale"):
                                commands.newProperty(f"{text_node}.scale", commands.FloatType, 1)
                            commands.setFloatProperty(f"{text_node}.scale", [stroke['scale']], True)
                            if not commands.propertyExists(f"{text_node}.rotation"):
                                commands.newProperty(f"{text_node}.rotation", commands.FloatType, 1)
                            commands.setFloatProperty(f"{text_node}.rotation", [stroke['rotation']], True)
                            order.append(f"text:{strokeid}:{int(frame.frame)}:{stroke['user']}")
                            strokeid += 1
                            continue

                        pen_node = f"{rv_node}.pen:{strokeid}:{int(frame.frame)}:{stroke['user']}"
                        frame_node = f"{rv_node}.frame:{int(frame.frame)}"

                        brush_name = "gauss" if stroke['brush'] in ["gauss", "gaussian"] else "circle"
                        if not commands.propertyExists(f"{pen_node}.brush"):
                            commands.newProperty(f"{pen_node}.brush", commands.StringType, 1)
                        commands.setStringProperty(f"{pen_node}.brush", [brush_name], True)

                        if not commands.propertyExists(f"{pen_node}.color"):
                            commands.newProperty(f"{pen_node}.color", commands.FloatType, 4)
                        commands.setFloatProperty(f"{pen_node}.color", [float(x) for x in stroke['color']], True)

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
                        commands.setIntProperty(f"{pen_node}.splat", [1 if brush_name == "gauss" else 0], True)

                        if stroke['type'] == 'erase':
                            if not commands.propertyExists(f"{pen_node}.mode"):
                                commands.newProperty(f"{pen_node}.mode", commands.IntType, 1)
                            commands.setIntProperty(f"{pen_node}.mode", [1], True)

                        if not commands.propertyExists(f"{pen_node}.width"):
                            commands.newProperty(f"{pen_node}.width", commands.FloatType, 1)
                        commands.setFloatProperty(f"{pen_node}.width", stroke['width'], True)

                        if not commands.propertyExists(f"{pen_node}.points"):
                            commands.newProperty(f"{pen_node}.points", commands.FloatType, 2)
                        commands.setFloatProperty(f"{pen_node}.points", stroke['points'], True)

                        order.append(f"pen:{strokeid}:{int(frame.frame)}:{stroke['user']}")
                        strokeid += 1

                    if not commands.propertyExists(f"{frame_node}.order"):
                        commands.newProperty(f"{frame_node}.order", commands.StringType, 1)
                    commands.setStringProperty(f"{frame_node}.order", order, True)

                # Update next ID
                paint_node = f"{rv_node}.paint"
                if commands.propertyExists(f"{paint_node}.nextId"):
                    commands.setIntProperty(f"{paint_node}.nextId", [strokeid], False)

        # Save temporary session to render snapshots via rvio
        temp_session = tempfile.NamedTemporaryFile(suffix=".rv", delete=False)
        temp_session_name = temp_session.name
        temp_session.close()
        
        commands.saveSession(temp_session_name, asACopy=True)
        print(f"Saved temporary RV session to: {temp_session_name}")
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        # Get annotated frames across all sources
        annotated_frames = extra_commands.findAnnotatedFrames()
        if annotated_frames:
            # Locate rvio
            rvio_bin = os.path.join(os.path.dirname(sys.argv[0]), "rvio")
            if not os.path.exists(rvio_bin):
                rvio_bin = "/Applications/OpenRV.app/Contents/MacOS/rvio"
                
            # Export frames to temp png files
            frames_str = ",".join(str(f) for f in annotated_frames)
            export_pattern = os.path.join(output_dir, "export.%d.png")
            rvio_cmd = [
                rvio_bin,
                temp_session_name,
                "-t", frames_str,
                "-outsrgb",
                "-o", export_pattern
            ]
            print(f"Running rvio: {rvio_cmd}")
            subprocess.run(rvio_cmd, check=True)
            
            # Rename outputs to match media naming
            for globalframe in annotated_frames:
                for source in commands.sourcesAtFrame(globalframe):
                    source_group = commands.nodeGroup(source)
                    source_frame = extra_commands.sourceFrame(globalframe)
                    uiname = extra_commands.uiName(source_group)
                    outputuiname = uiname.replace("@", "").replace("/", "_").replace("\\", "_")
                    for char in [" ", "(", ")", "[", "]"]:
                        outputuiname = outputuiname.replace(char, "_")
                    while "__" in outputuiname:
                        outputuiname = outputuiname.replace("__", "_")
                    outputuiname = outputuiname.strip("_")

                    
                    src_file = os.path.join(output_dir, f"export.{globalframe}.png")
                    dest_file = os.path.join(output_dir, f"{outputuiname}.{source_frame:05d}.png")
                    if os.path.exists(src_file):
                        if os.path.exists(dest_file):
                            os.remove(dest_file)
                        os.rename(src_file, dest_file)
                        print(f"Generated: {dest_file}")
                        
        # Save exported OTIO with relative paths
        otio_export_path = os.path.join(output_dir, os.path.basename(otio_path))
        otio.adapters.write_to_file(newtimeline, otio_export_path)
        print(f"Exported OTIO to: {otio_export_path}")
        
        if os.path.exists(temp_session_name):
            os.remove(temp_session_name)
            
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        try:
            sys.__stderr__.write(f"RV Batch failed with exception:\n{tb}\n")
            sys.__stderr__.flush()
        except Exception:
            pass
    finally:
        try:
            from PySide2 import QtCore
        except ImportError:
            from PySide6 import QtCore
        QtCore.QCoreApplication.quit()

if __name__ == "__main__":
    run_batch()
