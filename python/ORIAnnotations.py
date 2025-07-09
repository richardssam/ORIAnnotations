from dataclasses import dataclass, field
import os

import opentimelineio as otio
SyncEvent = otio.schema.schemadef.module_from_name('SyncEvent')


from datetime import datetime
from typing import List


@dataclass
class Media:
    """
    The media class is the container for the media being reviewed.
    It will end up on its own track.

    Attributes:
        name (str): Name of the media, typically the file basename.
        media_path (str): Full path to the location of the media.
        frame_rate (float): The frame rate of the media
        duration (int): Duration of the media
        start_frame (int): Start frame.
        vendor_name (str): The name of the vendor
        artist_name (str): The artist name
        vendor_id (str): The unique vendor id
        client_id (str): The unique client id
        clip_uuid (str): The UUID of the clip
        

    """
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
    otio_clip: otio.schema.Clip = None

    def _otio_clip_read(self, clip):
        """
        Read an OTIO clip and populate the fields from its metadata.
        """
        from otio_reader import _get_media_path
        self.otio_clip = clip

        if isinstance(clip.media_reference, otio.schema.ExternalReference):
            self.media_path = _get_media_path(str(clip.media_reference.target_url), {}).replace("file://", "")
            self.frame_rate = clip.source_range.start_time.rate
            self.start_frame = clip.source_range.start_time.value
            self.duration = clip.source_range.duration.value

        self.name = clip.name
        for field in ['artist_name', 'vendor_id', 'client_id', 'clip_uuid']:
            if field in clip.metadata:
                setattr(self, field, clip.metadata[field])

    def _create_otio_clip(self):
        media_ref = otio.schema.ExternalReference(
                    target_url="file://"+self.media_path,
                    available_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(self.calc_otio_start_frame(), float(self.frame_rate)),
                        duration=otio.opentime.RationalTime(self.duration, float(self.frame_rate))
                    )
                )
        metadata = {}
        for field in ['artist_name', 'vendor_id', 'client_id', 'clip_uuid']:
                if getattr(self, field):
                    metadata[field] = getattr(self, field)

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
    """Find clips on target_track that overlap with source_clip on source_track.

    Args:
        source_clip (Clip): A OTIO clip that we want to find the associated clips in the target_track
        target_track (track): The track we are looking for the associated clips in.

    Return:
        [Clip]: The clip(s) that are in range.
    """
    
    # Get the time range of the source clip in the timeline's coordinate system
    source_range_in_timeline = source_clip.range_in_parent()
    print(f"Found {source_range_in_timeline} for clip {source_clip.name}")
    
    # Find overlapping clips on the target track
    overlapping_clips = []
    
    return target_track.children_in_range(source_range_in_timeline)

@dataclass
class ReviewItemFrame:
    """
    The frame that is being reviewed for a particular piece of media.
    There will be at least one of:
    * A note
    * A annotated image
    * A set of annotated commands that should create the annotated image.

    Attributes:
        review_item (ReviewItem): The Review item associated with these frames.
        frame (int): The frame that is being reviewed.
        duration (int): The duration of the note, this is typically 1 frame, but it could be more.
        note (str): The reviewers note, this should be in markdown format.
        status (str): The status of the review, i.e. is it approved. Note, the task is stored on the media, so whether its a comp, or anim, etc.
        annotation_renderer (str): If you have chosen to add the annotation commands, we need to know which renderer you were using, in case there are incompabilities between the renderers.
        annotation_image (str): The path to the annotated image. Ideally this is just the annotations, or even better is a un-premultiplied PNG of the annotated image with an alpha, so you can choose whether you view just the annotation or both.
        canvas_size ([width, height]): For the annotation_commands what are the units of the brush-strokes.
        ocio_annotation_color_space (str): OCIO color space using the color-interop naming convention.
    
    """
    review_item: 'ReviewItem'
    frame: int = None
    duration: int = 1
    note: str = None
    status: str = None
    annotation_renderer: str = None
    annotation_image: str = None
    ocio_annotation_color_space: str = None
    canvas_size: List[int] = field(default_factory=list)
    annotation_commands: List[SyncEvent.SyncEvent] = field(default_factory=SyncEvent.SyncEvent)

    def export_svg(self):
        """Export a SVG of any annotations."""
        # TODO.
        return None
    
    def _otio_clip_read(self, clip):
        # Populate metadata
        for field in ['note', 'task', 'status', 'annotation_renderer', 'annotation_commands']:
            if field in clip.metadata:
                setattr(self, field, clip.metadata[field])

        


    def _export_otio_clip(self):
        """
        Export an OTIO Clip
        """
        if self.annotation_image is not None:
            if not os.path.exists(self.annotation_image):
                print("WARNING: annotation file {self.annotation_image} does not exist.")
            range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(self.frame, rate=self.review_item.media.frame_rate),
                                            duration=otio.opentime.RationalTime(self.duration, rate=self.review_item.media.frame_rate))
            newclip = otio.schema.Clip(name=f"{self.review_item.media.name}.{self.frame}",
                                               source_range=range)
            mediarange = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(0, rate=self.review_item.media.frame_rate),
                                                 duration=otio.opentime.RationalTime(self.duration, rate=self.review_item.media.frame_rate))
            media_ref = otio.schema.ExternalReference(
                        target_url=f"file:/{self.annotation_image}",
                        available_range=mediarange
                    )
            newclip.media_reference = media_ref
            newclip.metadata['annotation_commands'] = self.annotation_commands
            newclip.metadata['annotated_clip_name'] = self.review_item.media.name
            for field in ['note', 'status', 'annotation_renderer']:
                if getattr(self, field):
                    newclip.metadata[field] = getattr(self, field)

            return newclip

@dataclass
class ReviewItem:
    """
    This is a single piece of reviewed media, there might be multiple notes and annotations on it.

    Attributes:
        media (Media): A single piece of media associated with a list of things to be reviewed.
        review_frames (List[ReviewItemFrame]): A list of ReviewItemFrames that we have notes on.
    
    """
    media: Media = field(default_factory=Media)
    review_frames: List[ReviewItemFrame] = field(default_factory=list)

    def _export_otio_media(self, track):
        """Export the media to the specified track"""
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
            track.append(frameinfo._export_otio_clip())
        endframe = self.media.calc_otio_end_frame()
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

    Attributes:
    
        title (str): The title of the review
        review_start_time (datetime): When did this review start, useful if there is no timestamp in the title.
        participants (List[str]): A list of partipants.
        location (str): Where did this review happen.
        notes (str): Any other overall notes for the review, also in markdown format.
        Review_items (List[ReviewItem]): The list of things being reviewed.
    
    """
    title: str
    review_start_time: datetime = None
    participants: List[str] = field(default_factory=list)
    location: str = None
    notes: str = None
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
                            print(f"ERROR: cannot find media clip:{match_clip.name} Media:{mediamap.keys()}")
                            continue
                        self.review_items.append(clipmatch[match_clip.name])
                    ri = clipmatch[match_clip.name]
                    rf = ReviewItemFrame(review_item = ri, frame = clip.source_range.start_time.value)
                    rf._otio_clip_read(clip)
                    clipmatch[match_clip.name].review_frames.append(rf)


    def _export_otio_track(self, reviewgroup):
        """
        internal function for exporting a single track of a review group.
        """
        track = otio.schema.Track(self.title)

        # Populate metadata
        for field in ["title", "location", "notes", "participants"]:
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
                    ri._export_otio_media(track)
        return track
                

@dataclass
class ReviewGroup:
    """
    This is a container for a selection of media, and one or more review
    This will create a single OTIO file.
    """

    media: List[Media] = None
    reviews: List[Review] = None

    def _export_otio_media_track(self):
        """
        Internal function for exporting all the media-tracks.
        """
        track = otio.schema.Track("Media")
        for mediaitem in self.media:
            otioclip = mediaitem._create_otio_clip()
            track.append(otioclip)
        return track

    def export_otio_timeline(self):
        """
        Create an otio timeline of the whole reviewgroup
        """
        timeline = otio.schema.Timeline()
        master_track = self._export_otio_media_track()
        timeline.tracks.append(master_track)
        for review in self.reviews:
            timeline.tracks.append(review._export_otio_track(self))

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
                m._otio_clip_read(clip)
                media.append(m)
                if m.name is None:
                    print(f"WARNING: clip {clip} doesnt have a name")
                mediamap[m.name] = m

        self.media = media

        # Read the review info.
        reviews = []
        self.reviews = reviews
        for track in timeline.tracks[1:]:
            print("Got Track:", track.name)
            review = Review(title=track.name)
            review.otio_track_read(self, timeline, track, mediamap)
            reviews.append(review)
        print(f"Got {len(reviews)} reviews from timeline.")

