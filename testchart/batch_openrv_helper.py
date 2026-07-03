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
        new_seq = "defaultSequence"
        commands.setViewNode(new_seq)

        # Allow a brief moment for RV to populate sources and groups
        time.sleep(1.0)

        # Build clip map using sequence-level paint nodes.
        # Annotations must go to defaultSequence_p_<sourceGroup>* nodes — those are
        # the ones that render in the sequence view.  metaEvaluateClosestByType returns
        # the wrong layer (Media_p_*) when the active view is not the sequence, so
        # we find the nodes directly by their naming convention instead.
        all_paint_nodes = commands.nodesOfType("RVPaint")
        seq_paint_map = {}  # sourceGroupName → sequence-level paint node
        for pn in all_paint_nodes:
            prefix = f"{new_seq}_p_"
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
            print(f"Source group {sg}: media={os.path.basename(media_path)} paint_node={paint_node}")
            clipinfo = {'media_path': media_path, 'paint_node': paint_node}
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
                    
                media_height = 1080.0
                try:
                    seq_node = f"{new_seq}_sequence"
                    if seq_node and commands.propertyExists(f"{seq_node}.output.size"):
                        media_height = float(commands.getIntProperty(f"{seq_node}.output.size")[1])
                    else:
                        from PySide6.QtGui import QImage
                        img = QImage(clipinfo['media_path'])
                        if img.height() > 0:
                            media_height = float(img.height())
                except Exception:
                    pass
                print(f"DEBUG HEIGHT: media_key={media_key} resolved_height={media_height}")
                    
                global_start = 0.0
                if media_track:
                    for c in media_track:
                        if isinstance(c, otio.schema.Clip):
                            if c.media_reference:
                                target = c.media_reference.target_url
                                if target == media_key or os.path.basename(target) == os.path.basename(media_key):
                                    if c.range_in_parent():
                                        global_start = c.range_in_parent().start_time.value
                                        print(f"DEBUG SEARCH: media_key={media_key} matched c.name={c.name} target={target} global_start={global_start}")
                                    break
                                    
                rv_node = clipinfo['paint_node']
                if not rv_node:
                    print(f"Warning: No paint node found for {media_key}")
                    continue
                    
                for frame in ri.review_frames:
                    if not frame.annotation_commands:
                        continue
                        
                    strokes = []
                    strokemap = {}
                    for event in frame.annotation_commands:
                        schema = event.schema_name() if hasattr(event, "schema_name") else ""
                        print(f"DEBUG EVENT: schema={schema} uuid={getattr(event, 'uuid', None)}")
                        if schema == "PaintStart":
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
                        elif schema in ["PaintPoint", "PaintPoints"]:
                            stroke = strokemap.get(event.uuid)
                            if stroke and event.points:
                                stroke['width'] = [v for v in event.points.size]
                                stroke['points'] = [val for pair in zip(event.points.x, event.points.y) for val in pair]
                        elif schema == "PaintEnd":
                            stroke = strokemap.get(event.uuid)
                            if stroke and hasattr(event, "points") and event.points:
                                stroke['width'].extend(event.points.size)
                                stroke['points'].extend([val for pair in zip(event.points.x, event.points.y) for val in pair])
                        elif schema == "TextAnnotation":
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
                        elif schema == "EllipseAnnotation":
                            stroke = {
                                'type': 'ellipse',
                                'min': list(event.min),
                                'max': list(event.max),
                                'rgba': list(event.rgba),
                                'size': event.size,
                                'inner_rgba': list(event.inner_rgba),
                                'uuid': event.uuid or str(uuid.uuid4()),
                                'user': event.friendly_name.split(":")[-1] if getattr(event, "friendly_name", None) else "user"
                            }
                            strokes.append(stroke)
                        elif schema == "RectangleAnnotation":
                            stroke = {
                                'type': 'rect',
                                'min': list(event.min),
                                'max': list(event.max),
                                'rgba': list(event.rgba),
                                'size': event.size,
                                'inner_rgba': list(event.inner_rgba),
                                'uuid': event.uuid or str(uuid.uuid4()),
                                'user': event.friendly_name.split(":")[-1] if getattr(event, "friendly_name", None) else "user"
                            }
                            strokes.append(stroke)
                        elif schema == "ArrowAnnotation":
                            stroke = {
                                'type': 'arrow',
                                'start': list(event.start),
                                'end': list(event.end),
                                'rgba': list(event.rgba),
                                'size': event.size,
                                'uuid': event.uuid or str(uuid.uuid4()),
                                'user': event.friendly_name.split(":")[-1] if getattr(event, "friendly_name", None) else "user"
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
                    rv_frame = int(frame.frame)
                    frame_node = f"{rv_node}.frame:{rv_frame}"
                    for stroke in strokes:
                        def set_prop(node_path, name, ptype, val, dim=1):
                            if not commands.propertyExists(f"{node_path}.{name}"):
                                commands.newProperty(f"{node_path}.{name}", ptype, dim)
                            if ptype == commands.FloatType:
                                commands.setFloatProperty(f"{node_path}.{name}", val if isinstance(val, list) else [val], True)
                            elif ptype == commands.StringType:
                                commands.setStringProperty(f"{node_path}.{name}", val if isinstance(val, list) else [val], True)
                            else:
                                commands.setIntProperty(f"{node_path}.{name}", val if isinstance(val, list) else [val], True)

                        if stroke['type'] == 'text':
                            text_node = f"{rv_node}.text:{strokeid}:{rv_frame}:{stroke['user']}"
                            set_prop(text_node, "position",    commands.FloatType,  list(stroke['position']), dim=2)
                            set_prop(text_node, "color",       commands.FloatType,  [float(x) for x in stroke['color']], dim=4)
                            set_prop(text_node, "spacing",     commands.FloatType,  stroke['spacing'] or 0.8)
                            set_prop(text_node, "size",        commands.FloatType,  stroke['font_size'] / 5000.0)
                            set_prop(text_node, "font",        commands.StringType, "")
                            set_prop(text_node, "text",        commands.StringType, stroke['text'])
                            set_prop(text_node, "scale",       commands.FloatType,  stroke['scale'] or 1.0)
                            set_prop(text_node, "rotation",    commands.FloatType,  stroke['rotation'] or 0.0)
                            set_prop(text_node, "origin",      commands.StringType, "")
                            set_prop(text_node, "debug",       commands.IntType,    0)
                            set_prop(text_node, "startFrame",  commands.IntType,    rv_frame)
                            set_prop(text_node, "duration",    commands.IntType,    1)
                            set_prop(text_node, "mode",        commands.IntType,    0)
                            set_prop(text_node, "uuid",        commands.StringType, stroke.get('uuid', str(uuid.uuid4())))
                            set_prop(text_node, "softDeleted", commands.IntType,    0)
                            order.append(f"text:{strokeid}:{rv_frame}:{stroke['user']}")
                            strokeid += 1
                            continue

                        if stroke['type'] in ['ellipse', 'rect']:
                            shape_type = stroke['type']
                            shape_node = f"{rv_node}.{shape_type}:{strokeid}:{rv_frame}:{stroke['user']}"
                            set_prop(shape_node, "min",         commands.FloatType,  stroke['min'], dim=2)
                            set_prop(shape_node, "max",         commands.FloatType,  stroke['max'], dim=2)
                            set_prop(shape_node, "borderColor", commands.FloatType,  stroke['rgba'], dim=4)
                            set_prop(shape_node, "innerColor",  commands.FloatType,  stroke['inner_rgba'], dim=4)
                            set_prop(shape_node, "borderWidth", commands.FloatType,  stroke['size'] / 2.0)
                            set_prop(shape_node, "startFrame",  commands.IntType,    rv_frame)
                            set_prop(shape_node, "duration",    commands.IntType,    1)
                            set_prop(shape_node, "eye",         commands.IntType,    2)
                            set_prop(shape_node, "uuid",        commands.StringType, stroke['uuid'])
                            set_prop(shape_node, "softDeleted", commands.IntType,    0)
                            order.append(f"{shape_type}:{strokeid}:{rv_frame}:{stroke['user']}")
                            strokeid += 1
                            continue

                        if stroke['type'] == 'arrow':
                            shape_node = f"{rv_node}.arrow:{strokeid}:{rv_frame}:{stroke['user']}"
                            set_prop(shape_node, "startPos",    commands.FloatType,  stroke['start'], dim=2)
                            set_prop(shape_node, "endPos",      commands.FloatType,  stroke['end'], dim=2)
                            set_prop(shape_node, "borderColor", commands.FloatType,  stroke['rgba'], dim=4)
                            set_prop(shape_node, "innerColor",  commands.FloatType,  stroke['rgba'], dim=4)
                            set_prop(shape_node, "borderWidth", commands.FloatType,  0.0)
                            set_prop(shape_node, "thickness",   commands.FloatType,  stroke['size'] / 2.0)
                            set_prop(shape_node, "startFrame",  commands.IntType,    rv_frame)
                            set_prop(shape_node, "duration",    commands.IntType,    1)
                            set_prop(shape_node, "eye",         commands.IntType,    2)
                            set_prop(shape_node, "uuid",        commands.StringType, stroke['uuid'])
                            set_prop(shape_node, "softDeleted", commands.IntType,    0)
                            order.append(f"arrow:{strokeid}:{rv_frame}:{stroke['user']}")
                            strokeid += 1
                            continue

                        pen_node = f"{rv_node}.pen:{strokeid}:{rv_frame}:{stroke['user']}"

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
                        commands.setFloatProperty(f"{pen_node}.width", [w * 0.6 for w in stroke['width']], True)

                        if not commands.propertyExists(f"{pen_node}.points"):
                            commands.newProperty(f"{pen_node}.points", commands.FloatType, 2)
                        commands.setFloatProperty(f"{pen_node}.points", stroke['points'], True)

                        order.append(f"pen:{strokeid}:{rv_frame}:{stroke['user']}")
                        strokeid += 1

                    if commands.propertyExists(f"{frame_node}.order"):
                        commands.deleteProperty(f"{frame_node}.order")
                    commands.newProperty(f"{frame_node}.order", commands.StringType, 1)
                    commands.setStringProperty(f"{frame_node}.order", order, True)

                # Update next ID
                paint_node = f"{rv_node}.paint"
                if commands.propertyExists(f"{paint_node}.nextId"):
                    commands.setIntProperty(f"{paint_node}.nextId", [strokeid], False)

        # Save session for inspection and for rvio rendering
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        session_path = os.path.join(output_dir, "session.rv")
        commands.saveSession(session_path, asACopy=True)
        print(f"Saved RV session to: {session_path}")
        temp_session_name = session_path
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        # Get annotated frames across all sources
        annotated_frames = extra_commands.findAnnotatedFrames()
        if annotated_frames:
            # Locate rvio
            rvio_bin = os.path.join(os.path.dirname(sys.argv[0]), "rvio")
            if not os.path.exists(rvio_bin):
                rvio_bin = "/Applications/OpenRV.app/Contents/MacOS/rvio"
                
            for globalframe in annotated_frames:
                export_file = os.path.join(output_dir, f"export.{globalframe}.png")
                rvio_cmd = [
                    rvio_bin,
                    temp_session_name,
                    "-t", str(globalframe),
                    "-outsrgb",
                    "-o", export_file
                ]
                print(f"Running rvio for frame {globalframe}: {rvio_cmd}")
                try:
                    subprocess.run(rvio_cmd, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"Warning: rvio failed to render frame {globalframe}: {e}")
                    print(f"Attempting GUI fallback grab for frame {globalframe}...")
                    
                    # Locate RV binary
                    rv_bin = os.path.join(os.path.dirname(sys.argv[0]), "RV")
                    if not os.path.exists(rv_bin):
                        rv_bin = "/Users/sam/git/openrv_annotations/_build/stage/app/RV.app/Contents/MacOS/RV"
                        
                    grab_script = os.path.join(os.path.dirname(__file__), "grab_frame.py")
                    
                    env_grab = os.environ.copy()
                    env_grab["GRAB_FRAME"] = str(globalframe)
                    env_grab["GRAB_OUTPUT_PATH"] = export_file
                    
                    cmd_grab = [
                        rv_bin,
                        temp_session_name,
                        "-pyeval",
                        f"exec(open('{grab_script}').read())"
                    ]
                    try:
                        subprocess.run(cmd_grab, env=env_grab, check=True)
                        print(f"Successfully generated frame {globalframe} via GUI fallback grab.")
                    except Exception as e_grab:
                        print(f"Warning: GUI fallback grab failed: {e_grab}")
                        if os.path.exists(export_file):
                            try:
                                os.remove(export_file)
                            except Exception:
                                pass
                        continue
                
                # Rename the output to match media naming
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

                    dest_file = os.path.join(output_dir, f"{outputuiname}.{source_frame:05d}.png")
                    if os.path.exists(export_file):
                        if os.path.exists(dest_file):
                            try:
                                os.remove(dest_file)
                            except Exception:
                                pass
                        try:
                            os.rename(export_file, dest_file)
                            print(f"Generated: {dest_file}")
                        except Exception as rename_err:
                            print(f"Error renaming {export_file} to {dest_file}: {rename_err}")
                        
        # Save exported OTIO with relative paths
        otio_export_path = os.path.join(output_dir, os.path.basename(otio_path))
        otio.adapters.write_to_file(newtimeline, otio_export_path)
        print(f"Exported OTIO to: {otio_export_path}")
        
        print(f"Session saved at: {temp_session_name} — open in RV to inspect annotations")
            
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
