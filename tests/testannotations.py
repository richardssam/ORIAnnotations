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
                       'image': '/Users/sam/git/Annotations/test_export/chimera_cars_srgb-test_mov-prores_ks.00015.png',
                       "annotation_commands": """
                           {"OTIO_SCHEMA": "PaintStart.1", "brush": "circle","friendly_name": "defaultSequence_p_sourceGroup000000.pen:13:1:sam","rgba": [1,1,1,1],"source_index": 0,"timestamp": "2025-07-06T23:21:48.664116","type": "color","uuid": "6500d2f3-e758-4cd6-9bf2-83c7d712786d","visible": true}
                           {"OTIO_SCHEMA": "PaintPoint.1", "point": [{"OTIO_SCHEMA": "PaintVertex.1", "size": 0.00995635986328125, "x": -0.47882136702537537,"y": 0.046961307525634766},{"OTIO_SCHEMA": "PaintVertex.1","size": 0.00995635986328125,"x": -0.480663001537323,"y": 0.046961307525634766}]}
"""
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
            # For testing we assume this is a jsonlines format string, so we are going to read it in and turn it into OTIO schema objects.
            lines = reviewframe['annotation_commands'].splitlines()
            events = []
            for line in lines:
                if len(line) == 0:
                    continue
                print("DECODING:", line)
                event = otio.adapters.read_from_string(line, adapter_name="otio_json")
                events.append(event)
            frame.annotation_commands = events
        frames.append(frame)
    ri.review_frames = frames

review = ORIAnnotations.Review(title="Review", review_items=reviewitems)
print("MediaList:", medialist)
reviewgroup = ORIAnnotations.ReviewGroup(media=medialist, reviews=[review])
timeline = reviewgroup.export_otio_timeline()
print("About to export:")
outputfile = "tests/test_export_unittest.otio"
otio.adapters.write_to_file(timeline, outputfile)
print("Exported to:", outputfile)


# Now we read the file back in.

newtimeline = otio.adapters.read_from_file(outputfile)
rg = ORIAnnotations.ReviewGroup()
rg.read_otio_timeline(newtimeline)
alt_test_output_file = "tests/test_export_unittest2.otio"
otio.adapters.write_to_file(newtimeline, alt_test_output_file)
print("Exported to:", alt_test_output_file)