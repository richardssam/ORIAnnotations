import sys, os

project_root = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")

manifest_path = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
if manifest_path:
    manifest_path += os.pathsep
os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.path.join(
    project_root, "otio_event_plugin", "plugin_manifest.json"
)
sys.path.append(os.path.join(project_root, "python"))



import opentimelineio as otio
import ORIAnnotations
import os
import dataclasses


reviewdata = [
    {'media': "/Users/sam/git/Annotations/testmedia/chimera_cars_srgb-test_mov-prores_ks.mov",
     'startframe': 0,
     'framerate': 25,
     'duration': 200,
     'reviewframes': [{'note': "Hello", 
                       'frame': 1, 
                       'image': '/Users/sam/git/Annotations/test_export/chimera_cars_srgb-test_mov-prores_ks.00001.png'
                       },
                      {'note': "Hello", 
                       'frame': 15, 
                       'image': '/Users/sam/git/Annotations/test_export/chimera_cars_srgb-test_mov-prores_ks.00015.png'
                       "annotation_commands": [
                                {
                                    "OTIO_SCHEMA": "PaintStart.1",
                                    "brush": "circle",
                                    "friendly_name": "defaultSequence_p_sourceGroup000000.pen:13:1:sam",
                                    "ghost": null,
                                    "ghost_after": null,
                                    "ghost_before": null,
                                    "hold": null,
                                    "layer_range": null,
                                    "participant_hash": null,
                                    "rgba": [
                                        1.0,
                                        1.0,
                                        1.0,
                                        1.0
                                    ],
                                    "source_index": 0,
                                    "timestamp": "2025-07-06T23:21:48.664116",
                                    "type": "color",
                                    "uuid": "6500d2f3-e758-4cd6-9bf2-83c7d712786d",
                                    "visible": true
                                },
                                {
                                    "OTIO_SCHEMA": "PaintPoint.1",
                                    "layer_range": null,
                                    "point": [
                                        {
                                            "OTIO_SCHEMA": "PaintVertex.1",
                                            "size": 0.00995635986328125,
                                            "x": -0.47882136702537537,
                                            "y": 0.046961307525634766
                                        },
                                        {
                                            "OTIO_SCHEMA": "PaintVertex.1",
                                            "size": 0.00995635986328125,
                                            "x": -0.480663001537323,
                                            "y": 0.046961307525634766
                                        },

                                    ]
                                },
                       },
     ]
     },
     {'media': "/Users/sam/git/Annotations/testmedia/chimera_coaster_srgb-test_mov-dnxhd.mov",
     'startframe': 0,
     'framerate': 25,
     'duration': 200,
     'reviewframes': [{'note': "Hello", 'frame': 1, 'image': '/Users/sam/git/Annotations/test_export/chimera_coaster_srgb-test_mov-dnxhd.00001.png'},
                      {'note': "Hello", 'frame': 25, 'image': '/Users/sam/git/Annotations/test_export/chimera_coaster_srgb-test_mov-dnxhd.00025.png'},
     ]
      },
      {'media': "/Users/sam/git/Annotations/testmedia/chimera_fountains_srgb-test_mov-dnxhd.mov",
     'startframe': 0,
     'framerate': 25,
     'duration': 200,
     'reviewframes': [{'note': "Hello", 'frame': 28, 'image': '/Users/sam/git/Annotations/test_export/chimera_fountains_srgb-test_mov-dnxhd.00028.png'},
     ]
       }
]


medialist = []
reviewitems = []

for reviewmedia in reviewdata:
    media = ORIAnnotations.Media(media_path=reviewmedia['media'], 
                                 name=os.path.basename(reviewmedia['media']), 
                                 frame_rate=reviewmedia['framerate'], 
                                 start_frame=reviewmedia['startframe'],
                                 duration=reviewmedia['duration']
                                 )
    medialist.append(media)
    ri = ORIAnnotations.ReviewItem(media=media)
    reviewitems.append(ri)
    frames = []
    for reviewframe in reviewmedia['reviewframes']:
        frame = ORIAnnotations.ReviewItemFrame(note=reviewframe['note'], annotation_image=reviewframe['image'], frame=reviewframe['frame'], review_item=ri)
        if 'annotation_commands' in reviewframe:
        frame.annotation_commands = reviewframe['annotation_commands']
        frames.append(frame)
    ri.review_frames = frames

review = ORIAnnotations.Review(title="Review", review_items=reviewitems)
print("MediaList:", medialist)
reviewgroup = ORIAnnotations.ReviewGroup(media=medialist, reviews=[review])
timeline = reviewgroup.export_otio_timeline()
print("About to export:")
otio.adapters.write_to_file(timeline, "/Users/sam/git/Annotations/test_export/test_export_unittest.otio")
print("Exported to:", "/Users/sam/git/Annotations/test_export/test_export_unittest.otio")

newtimeline = otio.adapters.read_from_file("/Users/sam/git/Annotations/test_export/test_export_unittest.otio")
rg = ORIAnnotations.ReviewGroup()
rg.read_otio_timeline(newtimeline)
otio.adapters.write_to_file(newtimeline, "/Users/sam/git/Annotations/test_export/test_export_unittest2.otio")
print("Exported to:", "/Users/sam/git/Annotations/test_export/test_export_unittest2.otio")