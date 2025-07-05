import opentimelineio as otio
import ORIAnnotations
import os
import dataclasses


reviewdata = [
    {'media': "/Users/sam/git/Annotations/testmedia/chimera_cars_srgb-test_mov-prores_ks.mov",
     'startframe': 0,
     'framerate': 25,
     'duration': 200,
     'reviewframes': [{'note': "Hello", 'frame': 1, 'image': '/Users/sam/git/Annotations/test_export/chimera_cars_srgb-test_mov-prores_ks.00001.png'},
                      {'note': "Hello", 'frame': 15, 'image': '/Users/sam/git/Annotations/test_export/chimera_cars_srgb-test_mov-prores_ks.00015.png'},
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