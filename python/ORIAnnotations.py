from dataclasses import dataclass, field
import os

import opentimelineio as otio
SyncEvent = otio.schema.schemadef.module_from_name('SyncEvent')


from datetime import datetime
from typing import List

@dataclass
class Media:
    name: str = None
    media_path: str = None
    frame_rate: float = None,
    duration: int = None,
    start_frame: int = 0,
    vendor_name: str = None
    artist_name: str = None
    vendor_id: str = None
    client_id: str = None
    clip_uuid: str = None
    otio_clip: otio.schema.Clip = field(default_factory=otio.schema.Clip)

    def otio_clip_read(self, clip):
        #TODO DO MAGIC HERE.
        pass

    def create_otio_clip(self):
        media_ref = otio.schema.ExternalReference(
                    target_url="file://"+self.media_path,
                    available_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(self.calc_otio_start_frame(), float(self.frame_rate)),
                        duration=otio.opentime.RationalTime(self.duration, float(self.frame_rate))
                    )
                )
        metadata = {}
        if self.artist_name is not None:
            metadata['artist_name'] = self.artist_name
        if self.vendor_id is not None:
            metadata['vendor_id'] = self.vendor_id
        if self.client_id is not None:
            metadata['client_id'] = self.client_id
        if self.clip_uuid is not None:
            metadata['clip_uuid'] = self.clip_uuid

        clip = otio.schema.Clip(
            name=self.name,
            media_reference=media_ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(self.calc_otio_start_frame(), self.frame_rate),
                duration=otio.opentime.RationalTime(self.duration, self.frame_rate)
            ),
            metadata=metadata
        )
        return clip
    def calc_otio_start_frame(self):
        return 0 # for now.

    def calc_otio_end_frame(self):
        return self.duration # for now.


def find_overlapping_clips(source_clip, target_track):
    """Find clips on target_track that overlap with source_clip on source_track."""
    
    # Get the time range of the source clip in the timeline's coordinate system
    source_range_in_timeline = source_clip.range_in_parent()
    print(f"Found {source_range_in_timeline} for clip {source_clip.name}")
    
    # Find overlapping clips on the target track
    overlapping_clips = []
    
    return target_track.children_in_range(source_range_in_timeline)
    for item in target_track:
        if isinstance(item, otio.schema.Clip):
            # Get this clip's range in the timeline
            item_range_in_timeline = target_track.range_of_child(item)
            
            # Check if ranges overlap
            if source_range_in_timeline.overlaps(item_range_in_timeline):
                overlap_range = source_range_in_timeline.intersect(item_range_in_timeline)
                overlapping_clips.append({
                    'clip': item,
                    'overlap_range': overlap_range
                })
    
    return overlapping_clips

@dataclass
class ReviewItemFrame:
    """
    The frame that is being reviewed for a particular piece of media.
    """
    review_item: 'ReviewItem'
    frame: int = None
    note: str = None
    task: str = None
    status: str = None
    annotation_renderer: str = None
    annotation_image: str = None
    canvas_size: List[int] = field(default_factory=list)
    annotation_commands: List[SyncEvent.SyncEvent] = field(default_factory=SyncEvent.SyncEvent)

    def export_svg(self):
        """Export a SVG of any annotations."""
        # TODO.
        return None
    
    def otio_clip_read(self, clip):
        # Populate metadata
        for field in ['note', 'task', 'status', 'annotation_renderer', 'annotation_commands']:
            if field in clip.metadata:
                setattr(self, field, clip.metadata[field])

        


    def export_otio_clip(self):
        """
        Export an OTIO Clip
        """
        if self.annotation_image is not None:
            if not os.path.exists(self.annotation_image):
                print("WARNING: annotation file {self.annotation_image} does not exist.")
            range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(self.frame, rate=self.review_item.media.frame_rate),
                                            duration=otio.opentime.RationalTime(1, rate=self.review_item.media.frame_rate))
            newclip = otio.schema.Clip(name=f"{self.review_item.media.name}.{self.frame}",
                                               source_range=range)
            mediarange = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(0, rate=self.review_item.media.frame_rate),
                                                  duration=otio.opentime.RationalTime(1, rate=self.review_item.media.frame_rate))
            media_ref = otio.schema.ExternalReference(
                        target_url=f"file:/{self.annotation_image}",
                        available_range=mediarange
                    )
            newclip.media_reference = media_ref
            newclip.metadata['annotation_commands'] = self.annotation_commands
            newclip.metadata['annotated_clip_name'] = self.review_item.media.name
            for field in ['note', 'task', 'status', 'annotation_renderer']:
                if getattr(self, field):
                    newclip.metadata[field] = getattr(self, field)

            return newclip

@dataclass
class ReviewItem:
    """
    This is a single piece of reviewed media, there might be multiple notes and annotations on it.
    """
    media: Media = field(default_factory=Media)
    review_frames: List[ReviewItemFrame] = field(default_factory=list)

    def export_otio_media(self, track):
        """Export the media to the specified track"""
        print("Exporting:", self.media.name)
        lastframe = self.media.calc_otio_start_frame()
        for frameinfo in self.review_frames:
            frame = frameinfo.frame
            print(f"\t{frame}")
            if frame > lastframe:
                range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(lastframe+1, rate=self.media.frame_rate), 
                                                duration=otio.opentime.RationalTime(frame - lastframe - 1, rate=self.media.frame_rate))
                gap = otio.schema.Gap(name='', source_range=range)
                track.append(gap)
            lastframe = frame
            track.append(frameinfo.export_otio_clip())
        endframe = self.media.calc_otio_end_frame()
        print("END FRAME:", endframe)
        if endframe > lastframe + 1:
            print(f"\tDuration:{endframe - lastframe + 1}")
            range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(0, rate=self.media.frame_rate), 
                                            duration=otio.opentime.RationalTime(endframe - lastframe, rate=self.media.frame_rate))
            gap = otio.schema.Gap(name='', source_range=range)
            track.append(gap)


@dataclass
class Review:
    """
    Top level review, treated as a separate timeline.
    """
    title: str
    review_start_time: datetime = None
    participants: List[str] = field(default_factory=list)
    location: str = None
    description: str = None
    vendor_name: str = None
    review_items: List[ReviewItem] = field(default_factory=list)

    def otio_track_read(self, reviewgroup, timeline, track, mediamap):

        # Populate metadata
        for field in ["title", "location", "description", "vendor_name", "participants"]:
            if field in track.metadata:
                setattr(self, field, track.metadata[field])
        ris = []
        
        clipmatch = {}
    
        for clip in track:
            if isinstance(clip, otio.schema.Clip):
                # Need to find what clip in the main timeline matches this clip.
                match_clips = find_overlapping_clips(clip, timeline.tracks[0])
                if match_clips:
                    print("Found:", match_clips)
                    match_clip = match_clips[0]
                    if match_clip.name not in clipmatch:
                        # NEED TO FIND Media Entry.
                        clipmatch[match_clip.name] = ReviewItem()
                        if match_clip.name in mediamap:
                            main_media = mediamap[match_clip.name]
                            clipmatch[match_clip.name].media = main_media
                        else:
                            print("ERROR: cannot find media clip:{match_clip.name}")
                            continue
                        self.review_items.append(clipmatch[match_clip.name])
                    ri = clipmatch[match_clip.name]
                    rf = ReviewItemFrame(review_item = ri, frame = clip.source_range.start_time.value)
                    rf.otio_clip_read(clip)
                    clipmatch[match_clip.name].review_frames.append(rf)


    def export_otio_track(self, reviewgroup):
        track = otio.schema.Track(self.title)

        # Populate metadata
        for field in ["title", "location", "description", "vendor_name", "participants"]:
            if getattr(self, field):
                track.metadata[field] = getattr(self, field)

        if self.review_start_time:
            # Special case since we are converting it too.
            track.metadata['review_start_time'] = self.review_start_time.isoformat()
        
        # Loop over media exporting it.
        for media in reviewgroup.media:
            # Lets find the media in the review item list.
            for ri in self.review_items:
                if ri.media == media:
                    ri.export_otio_media(track)
        return track
                

@dataclass
class ReviewGroup:
    """
    This is a container for a selection of media, and one or more review
    This will create a single OTIO file.
    """

    media: List[Media] = None
    reviews: List[Review] = None

    def export_otio_media_track(self):
        track = otio.schema.Track("Media")
        for mediaitem in self.media:
            otioclip = mediaitem.create_otio_clip()
            track.append(otioclip)
        return track

    def export_otio_timeline(self):
        """
        Create an otio timeline of the whole reviewgroup
        """
        timeline = otio.schema.Timeline()
        master_track = self.export_otio_media_track()
        timeline.tracks.append(master_track)
        for review in self.reviews:
            timeline.tracks.append(review.export_otio_track(self))

        return timeline

    def read_otio_timeline(self, timeline):
        """
        Read from an existing timeline, creating a full datastructure from that timeline.
        """
        track = timeline.tracks[0]

        media = []
        mediamap = {}
        for clip in track:
            if isinstance(clip, otio.schema.Clip):
                m = Media()
                m.otio_clip_read(clip)
                media.append(m)
                mediamap[m.name] = clip

        self.media = media

        # Read the review info.
        for track in timeline.tracks[1:]:
            print("Got Track:", track.name)
            review = Review(title=track.name)
            review.otio_track_read(self, timeline, track, mediamap)

