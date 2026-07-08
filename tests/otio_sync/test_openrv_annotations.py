import sys
import os

# Setup manifest path before importing opentimelineio
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
manifest_path = os.path.join(project_root, "otio_event_plugin", "plugin_manifest.json")
if "OTIO_PLUGIN_MANIFEST_PATH" in os.environ:
    os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.pathsep + os.environ["OTIO_PLUGIN_MANIFEST_PATH"]
else:
    os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path

import unittest
from unittest.mock import MagicMock, patch

# Setup paths
sys.path.append(os.path.join(project_root, "python"))
sys.path.append(os.path.join(project_root, "rvplugin/ori_sync"))

# Create PySide / PySide6 / PySide2 mocks
class MockQtCore:
    QTimer = MagicMock()
sys.modules['PySide'] = MagicMock()
sys.modules['PySide.QtCore'] = MockQtCore
sys.modules['PySide.QtGui'] = MagicMock()
sys.modules['PySide2'] = MagicMock()
sys.modules['PySide2.QtCore'] = MockQtCore
sys.modules['PySide2.QtGui'] = MagicMock()
sys.modules['PySide6'] = MagicMock()
sys.modules['PySide6.QtCore'] = MockQtCore
sys.modules['PySide6.QtGui'] = MagicMock()

# Create rv/rv.commands/rv.rvtypes mocks
mock_rv = MagicMock()
mock_rv.commands = MagicMock()

class MinorModeMock:
    def __init__(self):
        pass
    def init(self, name, events, key_map, menus):
        pass

mock_rv.rvtypes.MinorMode = MinorModeMock
sys.modules['rv'] = mock_rv
sys.modules['rv.commands'] = mock_rv.commands
sys.modules['rv.rvtypes'] = mock_rv.rvtypes
sys.modules['rv.extra_commands'] = MagicMock()
sys.modules['rv.qt_utils'] = MagicMock()

import opentimelineio as otio
from plugin import OpenRVSyncPlugin
from otio_sync_core.rv_annotation_codec import RV_FONT_SCALE

# Ensure SyncEvent schema is registered (force reload manifest only if not already registered to avoid pytest alphabetical load caching issues)
try:
    otio.schema.schemadef.module_from_name('SyncEvent')
except Exception:
    try:
        import opentimelineio.plugins.manifest as otio_manifest
        otio_manifest._MANIFEST = None
        otio.schema.schemadef.module_from_name('SyncEvent')
    except Exception as e:
        print(f"Warning: failed to force load SyncEvent: {e}")

class TestOpenRVAnnotations(unittest.TestCase):
    def setUp(self):
        mock_rv.commands.reset_mock()
        # Setup mock property store
        self.properties = {}
        
        def mock_property_exists(prop):
            return prop in self.properties
            
        def mock_get_string_property(prop):
            return self.properties.get(prop, [])
            
        def mock_get_float_property(prop):
            return self.properties.get(prop, [])
            
        def mock_get_int_property(prop):
            return self.properties.get(prop, [])

        def mock_new_property(prop, prop_type, width):
            self.properties[prop] = []
            
        def mock_set_string_property(prop, val, exists):
            self.properties[prop] = val

        def mock_set_float_property(prop, val, exists):
            self.properties[prop] = val

        def mock_set_int_property(prop, val, exists):
            self.properties[prop] = val

        def mock_insert_string_property(prop, val):
            if prop not in self.properties:
                self.properties[prop] = []
            self.properties[prop].extend(val)

        mock_rv.commands.propertyExists.side_effect = mock_property_exists
        mock_rv.commands.getStringProperty.side_effect = mock_get_string_property
        mock_rv.commands.getFloatProperty.side_effect = mock_get_float_property
        mock_rv.commands.getIntProperty.side_effect = mock_get_int_property
        mock_rv.commands.newProperty.side_effect = mock_new_property
        mock_rv.commands.setStringProperty.side_effect = mock_set_string_property
        mock_rv.commands.setFloatProperty.side_effect = mock_set_float_property
        mock_rv.commands.setIntProperty.side_effect = mock_set_int_property
        mock_rv.commands.insertStringProperty.side_effect = mock_insert_string_property


    def test_broadcast_and_apply_text_annotation(self):
        # Instantiate plugin with mocked ORI_SESSION to avoid network startup
        with patch.dict(os.environ, {"ORI_SESSION": ""}):
            plugin = OpenRVSyncPlugin()
            
        # Mock finding paint node
        plugin.annotation._find_paint_node_for_media = MagicMock(return_value="sourceGroup000004_paint")
        
        # Mock media path resolution
        mock_rv.commands.nodesInGroup.side_effect = lambda sg: ["file_source_node_1"] if sg == "sourceGroup000004" else []
        mock_rv.commands.nodeType.side_effect = lambda node: "RVFileSource" if node == "file_source_node_1" else ""
        mock_rv.commands.fps.return_value = 24.0
        self.properties["file_source_node_1.media.movie"] = ["/path/to/movie.mov"]

        # Mock clip guid & track guid resolution
        plugin.playback._clip_guid_for_media_path = MagicMock(return_value="test-clip-guid-456")
        plugin.playback._clip_guid_for_media_and_frame = MagicMock(return_value="test-clip-guid-456")
        plugin.annotation._find_annotation_track_guid_for_clip = MagicMock(return_value="test-track-guid-789")

        # --- TEST BROADCAST (SEND) ---
        # Mock RV property values for a text annotation component
        node_name = "sourceGroup000004_paint"
        component = "text:2:12:sam_859"
        full_prop = f"{node_name}.{component}"
        
        self.properties[f"{full_prop}.text"] = ["Hello OpenRV Annotation"]
        self.properties[f"{full_prop}.color"] = [1.0, 0.0, 0.0, 1.0] # red
        self.properties[f"{full_prop}.position"] = [0.1, 0.2]
        self.properties[f"{full_prop}.size"] = [1.0] # 1.0 * RV_FONT_SCALE font_size
        self.properties[f"{full_prop}.spacing"] = [0.8]
        self.properties[f"{full_prop}.scale"] = [1.2]
        self.properties[f"{full_prop}.rotation"] = [15.0]
        self.properties[f"{full_prop}.font"] = ["LiberationSans"]
        self.properties[f"{full_prop}.uuid"] = ["test-text-uuid-123"]
        
        # Spy on sync_manager.broadcast_add_annotation or intercept serialized event
        plugin.sync_manager = MagicMock()
        plugin.sync_manager.annotation_track_guid_for_clip.return_value = "test-track-guid-789"
        
        plugin.annotation._broadcast_annotation(node_name, component)
        
        # Verify sync_manager.broadcast_add_annotation was called
        plugin.sync_manager.broadcast_add_annotation.assert_called_once()
        call_args = plugin.sync_manager.broadcast_add_annotation.call_args[1]
        
        self.assertEqual(call_args["annotation_track_guid"], "test-track-guid-789")
        self.assertEqual(call_args["clip_guid"], "test-clip-guid-456")
        self.assertEqual(call_args["clip_local_time"].value, 11) # 12 - 1 = 11 (0-indexed)
        self.assertEqual(len(call_args["events"]), 1)
        
        event_dict = call_args["events"][0]
        self.assertEqual(event_dict["OTIO_SCHEMA"], "TextAnnotation.1")
        self.assertEqual(event_dict["text"], "Hello OpenRV Annotation")
        self.assertEqual(event_dict["rgba"], [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(event_dict["position"], [0.1, 0.2])
        self.assertEqual(event_dict["font_size"], RV_FONT_SCALE)
        self.assertEqual(event_dict["font"], "LiberationSans")
        self.assertEqual(event_dict["scale"], 1.2)
        self.assertEqual(event_dict["rotation"], 15.0)
        self.assertEqual(event_dict["uuid"], "test-text-uuid-123")
        
        # --- TEST RECEIVE (APPLY) ---
        # Apply the remote annotation event
        remote_data = {
            "frame": 12,
            "position": [0.1, 0.2],
            "color": [1.0, 0.0, 0.0, 1.0],
            "spacing": 0.8,
            "size": 0.015, # passed straight through to the RV property, untouched by RV_FONT_SCALE
            "scale": 1.2,
            "rotation": 15.0,
            "font": "LiberationSans",
            "text": "Hello OpenRV Annotation",
            "uuid": "test-text-uuid-123",
            "media_path": "/path/to/movie.mov",
            "node_name": "sourceGroup000004_paint"
        }
        
        # Reset properties and mock nextId
        self.properties.clear()
        self.properties["sourceGroup000004_paint.paint.nextId"] = [42]
        
        plugin.annotation._apply_text_annotation(remote_data)
        
        # Verify the new text component was created with properties matching the remote data
        text_node_name = "text:42:12:remote"
        full_remote_prop = f"sourceGroup000004_paint.{text_node_name}"
        
        self.assertEqual(self.properties[f"{full_remote_prop}.text"], ["Hello OpenRV Annotation"])
        self.assertEqual(self.properties[f"{full_remote_prop}.color"], [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(self.properties[f"{full_remote_prop}.position"], [0.1, 0.2])
        self.assertEqual(self.properties[f"{full_remote_prop}.size"], [0.015])
        self.assertEqual(self.properties[f"{full_remote_prop}.scale"], [1.2])
        self.assertEqual(self.properties[f"{full_remote_prop}.rotation"], [15.0])
        self.assertEqual(self.properties[f"{full_remote_prop}.font"], ["LiberationSans"])
        self.assertEqual(self.properties[f"{full_remote_prop}.uuid"], ["test-text-uuid-123"])
        self.assertEqual(self.properties[f"{full_remote_prop}.startFrame"], [12])
        
        # Check order list
        order_prop = "sourceGroup000004_paint.frame:12.order"
        self.assertEqual(self.properties[order_prop], [text_node_name])
        # nextId incremented
        self.assertEqual(self.properties["sourceGroup000004_paint.paint.nextId"], [43])
        print("✓ OpenRV text annotation broadcast & receive test passed!")

    def test_find_paint_node_for_media_frame_mapping(self):
        with patch.dict(os.environ, {"ORI_SESSION": ""}):
            plugin = OpenRVSyncPlugin()

        # Mock sync_manager and object_map
        plugin.sync_manager = MagicMock()
        mock_clip = MagicMock()
        mock_parent = MagicMock()
        mock_clip.parent.return_value = mock_parent
        
        # Clip starts at sequence/timeline frame 200 (second clip)
        mock_range = MagicMock()
        mock_range.start_time.value = 200.0
        mock_clip.trimmed_range_in_parent.return_value = mock_range
        
        plugin.sync_manager._object_map = {
            "test-clip-guid-2": mock_clip
        }
        plugin.playback._clip_guid_for_media_path = MagicMock(return_value="test-clip-guid-2")
        
        # Mock metaEvaluateClosestByType to expect sequence frame 400 when we ask for local frame 200
        mock_rv.commands.metaEvaluateClosestByType.return_value = [{"node": "defaultSequence_p_sourceGroup000001"}]
        
        node = plugin.annotation._find_paint_node_for_media("/path/to/movie.mov", 200)
        
        # Verify it mapped local_frame=200 to seq_frame=400 (200.0 + 200 - 1 + 1)
        mock_rv.commands.metaEvaluateClosestByType.assert_called_once_with(400, "RVPaint")
        self.assertEqual(node, "defaultSequence_p_sourceGroup000001")
        print("✓ OpenRV paint node frame mapping test passed!")

    def test_rebuild_rv_session_view_switching(self):
        with patch.dict(os.environ, {"ORI_SESSION": ""}):
            plugin = OpenRVSyncPlugin()

        # Mock sync_manager and timeline structure
        plugin.sync_manager = MagicMock()
        
        # We need two timelines so len(timelines) > 1 is True, triggering sequence group creation
        timeline1 = otio.schema.Timeline("TimelineOne")
        timeline1.metadata["sync"] = {"guid": "tl-guid-1"}
        
        # timeline1 tracks: Media track and Annotations track
        media_track = otio.schema.Track("Media")
        
        clip1 = otio.schema.Clip("Clip1")
        clip1.media_reference = otio.schema.ExternalReference(target_url="/path/to/movie1.mov")
        clip1.metadata["sync"] = {"guid": "clip-guid-1"}
        media_track.append(clip1)
        
        ann_track = otio.schema.Track("Annotations")
        
        ann_clip = otio.schema.Clip("Annotation_0")
        ann_clip.metadata.update({
            "clip_guid": "clip-guid-1",
            "annotation_commands": []
        })
        ann_track.append(ann_clip)
        
        timeline1.tracks.extend([media_track, ann_track])
        
        timeline2 = otio.schema.Timeline("TimelineTwo")
        timeline2.metadata["sync"] = {"guid": "tl-guid-2"}
        
        plugin.sync_manager._timelines = {
            "tl-guid-1": timeline1,
            "tl-guid-2": timeline2
        }
        plugin.sync_manager.active_timeline_guid = "tl-guid-1"
        plugin.sync_manager._object_map = {
            "clip-guid-1": clip1
        }
        
        # Mock RV commands
        mock_rv.commands.newNode.side_effect = lambda node_type, name: f"{name}_node"
        mock_rv.commands.viewNode.return_value = "some_other_node"
        plugin.sequence._path_to_source_group_map = MagicMock(return_value={"/path/to/movie1.mov": "sourceGroup000000"})
        
        # We want to verify that setViewNode is called with "TimelineOne_node"
        plugin.sequence._rebuild_rv_session()
        
        mock_rv.commands.setViewNode.assert_any_call("TimelineOne_node")
        print("✓ OpenRV rebuild view switching test passed!")

    def test_apply_annotation_render_update_in_place(self):
        with patch.dict(os.environ, {"ORI_SESSION": ""}):
            plugin = OpenRVSyncPlugin()

        # Mock finding paint node
        plugin.annotation._find_paint_node_for_media = MagicMock(return_value="sourceGroup000004_paint")

        # Mock media clip lookup in sync_manager
        plugin.sync_manager = MagicMock()
        media_clip = otio.schema.Clip("MediaClip")
        media_clip.media_reference = otio.schema.ExternalReference(target_url="/path/to/movie.mov")
        plugin.sync_manager._object_map = {
            "test-clip-guid-123": media_clip
        }

        # Create annotation clip with TextAnnotation command metadata
        ann_clip = otio.schema.Clip("Annotation_Clip")
        ann_clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(3.0, 25.0), # frame 4 (0-indexed 3)
            duration=otio.opentime.RationalTime(1.0, 25.0)
        )
        
        text_ann_command = {
            "OTIO_SCHEMA": "TextAnnotation.1",
            "font": "LiberationSans",
            "font_size": 75.0,
            "position": [0.2, 0.3],
            "rgba": [0.0, 1.0, 0.0, 1.0], # green
            "text": "Updated text annotation",
            "uuid": "test-uuid-999"
        }
        
        ann_clip.metadata.update({
            "clip_guid": "test-clip-guid-123",
            "annotation_commands": [text_ann_command]
        })

        # Set up properties to represent an existing text annotation with same UUID
        node = "sourceGroup000004_paint"
        text_node_name = "text:42:4:remote"
        order_prop = f"{node}.frame:4.order"
        
        self.properties[order_prop] = [text_node_name]
        self.properties[f"{node}.{text_node_name}.uuid"] = ["test-uuid-999"]
        self.properties[f"{node}.{text_node_name}.text"] = ["Original text"]
        self.properties[f"{node}.{text_node_name}.position"] = [0.1, 0.2]
        self.properties[f"{node}.{text_node_name}.color"] = [1.0, 0.0, 0.0, 1.0] # red
        self.properties[f"{node}.{text_node_name}.size"] = [0.01]

        # Call _apply_annotation_render
        plugin.annotation._apply_annotation_render(ann_clip)

        # Check that the existing properties were updated in place
        self.assertEqual(self.properties[f"{node}.{text_node_name}.text"], ["Updated text annotation"])
        self.assertEqual(self.properties[f"{node}.{text_node_name}.position"], [0.2, 0.3])
        self.assertEqual(self.properties[f"{node}.{text_node_name}.color"], [0.0, 1.0, 0.0, 1.0])
        self.assertEqual(self.properties[f"{node}.{text_node_name}.size"], [75.0 / RV_FONT_SCALE])
        
        # Check that no new property order was added
        self.assertEqual(self.properties[order_prop], [text_node_name])
        print("✓ OpenRV annotation render update in place test passed!")

if __name__ == "__main__":
    unittest.main()
