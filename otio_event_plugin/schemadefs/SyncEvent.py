
import opentimelineio as otio
import datetime



class SyncEvent(otio.core.SerializableObject):
    """
    This is the base-class of all schema events.

    Attributes:
        timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change. This is used to track when media changes occur in the timeline.

    """


    timestamp = otio.core.serializable_field(
        "timestamp",
        doc="The timestamp of the media change, in ISO 8601 format",
    )

    def __init__(
            self,
            timestamp=None 
    ):
        otio.core.SerializableObject.__init__(self)

        self.timestamp = timestamp if timestamp is not None else datetime.datetime.now().isoformat()



@otio.core.register_type
class AnnotationEffect(otio.schema.Effect):
    """A schema for annotations."""

    _serializable_label = "AnnotationEffect.1"
    _name = "AnnotationEffect"

    def __init__(
        self, name: str = "", visible: bool = True, layers: list | None = None
    ) -> None:
        super().__init__(name=name, effect_name="Annotation.1")
        self.visible = visible
        self.commands = commands

    _visible = otio.core.serializable_field(
        "visible", required_type=bool, doc=("visible: expects either true or false")
    )

    _commands = otio.core.serializable_field(
        "commands", required_type=list, doc=("commands: expects a list of sync commands")
    )

    @property
    def layers(self) -> list:
        return self._layers

    @layers.setter
    def layers(self, val: list):
        self._layers = val

    def __str__(self) -> str:
        return (
            f"Annotation({self.name}, {self.effect_name}, {self.metadata}, "
            f"{self.layers}), {self.visible}"
        )

    def __repr__(self) -> str:
        return (
            f"otio.schema.Annotation(name={self.name!r}, "
            f"effect_name={self.effect_name!r}, "
            f"metadata={self.metadata!r}, "
            f"visible={self.visible!r}, layers={self.layers!r})"
        )


@otio.core.register_type
class Play(SyncEvent):
    """A schema for the event system to define when play is enabled.

    Attributes:
        timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change. This is used to track when media changes occur in the timeline.
        value (bool): value is a boolean indicating whether play is enabled or not.
    """

    _serializable_label = "play.1"
    _name = "Play"

    value = otio.core.serializable_field(
        "value",
        required_type=bool,
        doc="The value of the play event",
    )

    def __init__(
            self,
            value=True,
            timestamp=None 
    ):
        SyncEvent.__init__(self, timestamp)
        if not isinstance(value, bool):
            raise TypeError("value must be a boolean")
        self.value = value


    def __str__(self):
        
        return "Play({})".format(
            repr(self.value)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.Play(value={})".format(
            repr(self.value)
        )
    

@otio.core.register_type
class SetCurrentFrame(SyncEvent):
    """A schema for the event system to define when the current frame is set.

    Attributes:
        time (RationalTime): time is a RationalTime representing the current frame in the timeline.
        timestamp (str): Timestamp is an ISO 8601 formatted string representing the time of the change.
    
    """

    _serializable_label = "set_current_frame.1"
    _name = "SetCurrentFrame"


    time = otio.core.serializable_field(
        "time",
        required_type=otio.opentime.RationalTime,
        doc="The current time in the timeline"
    )

    def __init__(
            self,
            time=None,
            timestamp=None 
    ):
        SyncEvent.__init__(self, timestamp)

        if time is not None and not isinstance(time, otio.opentime.RationalTime):
            raise TypeError("time must be an otio.core.RationalTime")
        self.time = time


    def __str__(self):
        
        return "SetCurrentFrame({})".format(
            repr(self.time)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.SetCurrentFrame(time={})".format(
            repr(self.time)
        )
    

@otio.core.register_type
class NewPresenter(SyncEvent):
    """
    New Presenter 

    Attributes:
       presenter_hash (str): The hash of the presenter.
    """

    _serializable_label = "NewPresenter.1"
    _name = "NewPresenter"

    def __init__(
            self,
            presenter_hash=None,
            timestamp=None 
    ):
        SyncEvent.__init__(self, timestamp)
        self.presenter_hash = presenter_hash

    presenter_hash = otio.core.serializable_field(
        "presenter_hash",
        doc="The hash of the presenter"
    )

    def __str__(self):
        
        return "NewPresenter({})".format(
            repr(self.presenter_hash)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.NewPresenter(presenter_hash={})".format(
            repr(self.presenter_hash)
        )


@otio.core.register_type
class NewParticipant(SyncEvent):
    """A new participant for the sync review

    """

    _serializable_label = "NewParticipant.1"
    _name = "NewParticipant"

    def __init__(
            self,
            timestamp=None 
    ):
        SyncEvent.__init__(self, timestamp)

    def __str__(self):
        
        return "NewParticipant({})".format(
            repr()
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.NewParticipant()"


@otio.core.register_type
class SharedKeyRequest(SyncEvent):
    """Shared Key Request
    
    Attributes:
       key (str): The shared key
    """

    _serializable_label = "SharedKeyRequest.1"
    _name = "SharedKeyRequest"

    def __init__(
            self,
            key=None,
            timestamp=None
    ):
        SyncEvent.__init__(self, timestamp)
        self.key = key

    key = otio.core.serializable_field(
        "key",
        doc="The shared key"
    )

    def __str__(self):

        return "SharedKeyRequest({})".format(
            repr(self.key)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.SharedKeyRequest(key={})".format(
            repr(self.key)
        )



@otio.core.register_type
class SharedKeyResponse(SyncEvent):
    """SharedKeyResponse

    Attributes:
       key (str): The shared key
    """

    _serializable_label = "SharedKeyResponse.1"
    _name = "SharedKeyResponse"

    def __init__(
            self,
            key=None,
            timestamp=None
    ):
        SyncEvent.__init__(self, timestamp)
        self.key = key

    key = otio.core.serializable_field(
        "key",
        doc="The shared key"
    )

    def __str__(self):

        return "SharedKeyResponse({})".format(
            repr(self.key)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.SharedKeyResponse(key={})".format(
            repr(self.key)
        )


@otio.core.register_type
class GetSession(SyncEvent):
    """
    Get a sync Session

    Attributes:
       user (str): The user making the request
       app (str): The app making the request
    """

    _serializable_label = "GetSession.1"
    _name = "GetSession"

    def __init__(
            self,
            user=None,
            app=None,
            timestamp=None
    ):
        SyncEvent.__init__(self, timestamp)
        self.user = user
        self.app = app

    user = otio.core.serializable_field(
        "user",
        doc="The user making the request"
    )

    app = otio.core.serializable_field(
        "app",
        doc="The app making the request"
    )

    def __str__(self):

        return "GetSession({})".format(
            repr(self.user) + ", " + repr(self.app)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.GetSession(user={}, app={})".format(
            repr(self.user), repr(self.app)
        )

@otio.core.register_type
class RequestSyncPlayback(SyncEvent):
    """RequestSyncPlayback
    
    
    """

    _serializable_label = "RequestSyncPlayback.1"
    _name = "RequestSyncPlayback"

    def __init__(
            self,
            timestamp=None
    ):
        SyncEvent.__init__(self, timestamp)

    def __str__(self):

        return "RequestSyncPlayback({})".format(
            repr(self.user) + ", " + repr(self.app)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.RequestSyncPlayback(user={}, app={})".format(
            repr(self.user), repr(self.app)
        )


@otio.core.register_type
class SyncPlayback(SyncEvent):
    """SyncPlayback
    
    Attributes:
       looping (bool): Whether the playback is looping.
       playing (bool): Is the media playing.
       muted (bool): Is the playback muted.
       scrubbing (bool): Is the playback currently scrubbing.
       current_time (RationalTime): What is the current position on the timeline.
       output_bounds (box2d): The output bounds of the playback.
       source (str): The source of the playback.
       source_index (int): The source index.
       playback_range (TimeRange): What is the playback range.


    """

    _serializable_label = "SyncPlayback.1"
    _name = "SyncPlayback"

    def __init__(
            self,
            looping=None,
            playing=None,
            muted=None,
            scrubbing=None,
            playback_range=None, # Missing enabled, zoomed
            current_time=None,
            output_bounds=None,
            source=None,
            source_index=0,
            timestamp=None
    ):
        SyncEvent.__init__(self, timestamp)
        self.looping = looping
        self.playing = playing
        self.muted = muted
        self.scrubbing = scrubbing
        self.playback_range = playback_range
        self.current_time = current_time
        self.output_bounds = output_bounds
        self.source = source
        self.source_index = source_index

    looping = otio.core.serializable_field(
        "looping",
        required_type=bool,
        doc="Whether the playback is looping"
    )

    playing = otio.core.serializable_field(
        "playing",
        required_type=bool,
        doc="Whether the playback is currently playing"
    )

    muted = otio.core.serializable_field(
        "muted",
        required_type=bool,
        doc="Whether the playback is muted"
    )

    playback_range = otio.core.serializable_field(
        "playback_range",
        required_type=otio.opentime.TimeRange,
        doc="The range of playback"
    )

    scrubbing = otio.core.serializable_field(
        "scrubbing",
        required_type=bool,
        doc="Whether the playback is currently scrubbing"
    )
    current_time = otio.core.serializable_field(
        "current_time",
        required_type=otio.opentime.RationalTime,
        doc="The current time in the playback"
    )
    output_bounds = otio.core.serializable_field(
        "output_bounds",
        required_type=otio.schema.box2d,
        doc="The output bounds of the playback")
    source = otio.core.serializable_field(
        "source",
        doc="The source of the playback")
    source_index = otio.core.serializable_field(
        "source_index",
        required_type=int,
        doc="The index of the source in the playback, used for multi-source playback"
    )

    def __str__(self):

        return "SyncPlayback({})".format(
            repr(self.looping) + ", " + repr(self.playing) + ", " + repr(self.muted) + ", " + repr(self.playback_range)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.SyncPlayback(looping={}, playing={}, muted={}, playback_range={})".format(
            repr(self.looping), repr(self.playing), repr(self.muted), repr(self.playback_range)
        )

@otio.core.register_type
class MediaChange(SyncEvent):
    """A schema for the event system to denote when media changes.

    Attributes:
        mediaReference (MediaReference): mediaReference is an otio.core.MediaReference
        timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change.
    """

    _serializable_label = "MediaChange.1"
    _name = "MediaChange"

    def __init__(
            self,
            mediaReference=None,
            timestamp=None 
        ):
        SyncEvent.__init__(self, timestamp)
        if mediaReference is not None and not isinstance(mediaReference, otio.core.MediaReference):
            raise TypeError("mediaReference must be an otio.core.MediaReference")
        self.mediaReference = mediaReference

    mediaReference = otio.core.serializable_field(
        "mediaReference",
        required_type=otio.core.MediaReference,
        doc="The reference to the media"
    )

    def __str__(self):
        
        return "MediaChange({})".format(
            repr(self.mediaReference)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.MediaChange(mediaReference={})".format(
            repr(self.mediaReference)
        )


@otio.core.register_type
class PaintStart(SyncEvent):
    """A schema for the event system to denote when painting starts.

    Attributes:
       source_index (int): A reference to the media source.
       uuid (int): A Unique ID for the brush-stroke, used for subsequence brushes.
       friendly_name (str):
       rgba ([r,g,b,a]): The color + alpha to be painted.
       type (str): The type of paint stroke.
       brush (str): The type of brush, currently one of circle, gaussian.
       visible (bool): Is the stroke visible.
       timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change.
        TODO.
        """

    _serializable_label = "PaintStart.1"
    _name = "PaintStart"

    def __init__(
            self,
            source_index=0,
            uuid=None,
            friendly_name=None,
            participant_hash=None,
            rgba=None,
            type="color",
            brush="circle",
            visible=True,
            name=None,
            effect_name=None,
            layer_range=None,
            hold=None,
            ghost=None,
            ghost_before=None,
            ghost_after=None,
            timestamp=None 
        ):
        SyncEvent.__init__(self, timestamp)
        self.source_index = source_index
        self.uuid = uuid
        self.friendly_name = friendly_name
        self.participant_hash = participant_hash
        self.rgba = rgba
        self.type = type
        self.brush = brush
        self.visible = visible
        self.name = name
        self.effect_name = effect_name
        self.layer_range = layer_range
        self.hold = hold
        self.ghost = ghost
        self.ghost_before = ghost_before
        self.ghost_after = ghost_after

        if not isinstance(source_index, int):
            raise TypeError("source_index must be an integer")

        if rgba is not None and (not isinstance(rgba, list) or len(rgba) != 4 or not all(isinstance(x, (int, float)) for x in rgba)):
            raise TypeError(f"rgba must be an list of numbers got {rgba}")

    source_index = otio.core.serializable_field(
        "source_index",
        required_type=int,
        doc="The index of the source media for the paint."
    )
    uuid = otio.core.serializable_field(
        "uuid",
        doc="The unique identifier for the paint event"
    )
    friendly_name = otio.core.serializable_field(
        "friendly_name",
        doc="The friendly artist name for the paint event creator"
    )
    participant_hash = otio.core.serializable_field(
        "participant_hash",
        doc="The unique identifier for the participant"
    )
    rgba = otio.core.serializable_field(
        "rgba",
        required_type=list,
        doc="The color of the paint event in RGBA format"
    )
    type = otio.core.serializable_field(
        "type",
        doc="The type of the paint event"
    )
    brush = otio.core.serializable_field(
        "brush",
        doc="The brush type of the paint event"
    )
    visible = otio.core.serializable_field(
        "visible",
        required_type=bool,
        doc="The visible type of the paint event"
    )
    layer_range = otio.core.serializable_field(
        "layer_range",
        required_type=otio.opentime.TimeRange,
        doc="The range of the layer for the paint event"
    )
    hold = otio.core.serializable_field(
        "hold",
        required_type=bool,
        doc="The hold of the paint event"
    )
    ghost = otio.core.serializable_field(
        "ghost",
        required_type=bool,
        doc="Is ghosting of the paint strokes enabled"
    )
    ghost_before = otio.core.serializable_field(
        "ghost_before",
        required_type=bool,
        doc="Number of frames to ghost before the current frame"
    )
    ghost_after = otio.core.serializable_field(
        "ghost_after",
        required_type=bool,
        doc="Number of frames to ghost after the current frame"
    )

    def __str__(self):
        
        return "PaintStart(type={})".format(
            repr(self.type)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.PaintStart(type={})".format(
            repr(self.type)
        )
    



@otio.core.register_type
class PaintVertex(otio.core.SerializableObject):
    """A schema for the definition of a point vertex in a paint stroke.
    
    Attributes:
       x (float): X position
       y (float): Y position
       size (float): Size of paint vertex.
    """

    _serializable_label = "PaintVertex.1"
    _name = "PaintVertex"

    def __init__(
            self,
            x=0.0,
            y=0.0,
            size=1.0
        ):
        otio.core.SerializableObject.__init__(self)
        self.x = x
        self.y = y
        self.size = size

        if not isinstance(x, float):
            raise TypeError("x must be an float")
        if not isinstance(y, float):
            raise TypeError("y must be an float")
        if not isinstance(size, float):
            raise TypeError("size must be an float")
    x = otio.core.serializable_field(
        "x",
        required_type=float,
        doc="The x coordinate of the point vertex"
    )
    y = otio.core.serializable_field(
        "y",
        required_type=float,
        doc="The y coordinate of the point vertex"
    )
    size = otio.core.serializable_field(
        "size",
        required_type=float,
        doc="The size of the point vertex"
    )


    def __str__(self):
        
        return "PaintVertex({})".format(
            repr(self.x) + ", " + repr(self.y) + ", " + repr(self.size)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.PaintVertex(x={},y={},size={})".format(
            repr(self.x), repr(self.y), repr(self.size)
        )

@otio.core.register_type
class PaintPoint(SyncEvent):
    """A schema for the event system to denote when adding onto a paint stroke.

    Attribute:
       source_index:
       uuid (str): The UUID of the initial brush stroke.
       layer_range:
       point: [List[PaintVertex]]: List of paint vertices.
       timestamp (str):    timestamp is an ISO 8601 formatted string representing the time of the change.
    """

    _serializable_label = "PaintPoint.1"
    _name = "PaintPoint"

    def __init__(
            self,
            source_index=0,
            uuid=None,
            layer_range=None,
            point=None,
            timestamp=None 
        ):
        SyncEvent.__init__(self, timestamp)
        self.source_index = source_index
        self.uuid = uuid
        self.layer_range = layer_range
        self.point = point

        if not isinstance(source_index, int):
            raise TypeError("source_index must be an integer")

        if point is not None and not isinstance(point, list):
            print("Point type: ", type(point))
            raise TypeError(f"point must be an PaintVertex got {point}")

    source_index = otio.core.serializable_field(
        "source_index",
        required_type=int,
        doc="The index of the source media for the paint."
    )
    uuid = otio.core.serializable_field(
        "uuid",
        doc="The unique identifier for the paint event"
    )
    layer_range = otio.core.serializable_field(
        "layer_range",
        required_type=otio.opentime.TimeRange,
        doc="The range of the layer for the paint event"
    )
    point = otio.core.serializable_field(
        "point",
        required_type=list,
        doc="The vertex of the paint event"
    )

    def __str__(self):
        
        return "PaintPoint({})".format(
            repr(self.point)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.MediaChange(point={})".format(
            repr(self.point)
        )
    

@otio.core.register_type
class PaintEnd(SyncEvent):
    """A schema for the event system to denote when painting ends.

    Attributes:
       uuid (str): uuid of curve to finish.
       point (VertexPoint): Last point in curve (if any)
       timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change.
    """

    _serializable_label = "PaintEnd.1"
    _name = "PaintEnd"

    def __init__(
            self,
            uuid=None,
            point=None,
            timestamp=None
        ):
        SyncEvent.__init__(self, timestamp)
        self.uuid = uuid
        self.point = point


        if point is not None and not isinstance(point, list):
            raise TypeError("point must be a list of PaintVertex")

    uuid = otio.core.serializable_field(
        "uuid",
        doc="The unique identifier for the paint event"
    )

    point = otio.core.serializable_field(
        "point",
        required_type=PaintVertex,
        doc="The vertex of the paint event"
    )

    def __str__(self):
        return "PaintEnd({})".format(
            repr(self.point)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.PaintEnd(point={})".format(
            repr(self.point)
        )
    

@otio.core.register_type
class TextAnnotation(SyncEvent):
    """A schema for the event system to denote entering text.

    Attributes:
       uuid (str): uuid of curve to finish.
       point (VertexPoint): Last point in curve (if any)
       timestamp (str): timestamp is an ISO 8601 formatted string representing the time of the change.
    """

    _serializable_label = "TextAnnotation.1"
    _name = "TextAnnotation"

    def __init__(
            self,
            uuid=None,
            rgba=None,
            friendly_name=None,
            text=None,
            spacing=None,
            font_size=None,
            scale=None,
            rotation=None,
            font=None,
            position=None,
            timestamp=None
        ):
        SyncEvent.__init__(self, timestamp)
        self.uuid = uuid
        self.position = position
        self.friendly_name = friendly_name
        self.rgba = rgba
        self.text = text
        self.spacing = spacing
        self.font_size = font_size
        self.scale = scale
        self.rotation = rotation
        self.font = font
        self.timestamp = timestamp


        if position is not None and not isinstance(position, list):
            raise TypeError("position must be a list of Floats")

    uuid = otio.core.serializable_field(
        "uuid",
        doc="The unique identifier for the paint event"
    )
    friendly_name = otio.core.serializable_field(
        "friendly_name",
        doc="The human usable name of the user who created the annotation"
    )

    position = otio.core.serializable_field(
        "position",
        required_type=list,
        doc="The position of the text annotation in the format [x, y]"
    )

    rgba = otio.core.serializable_field(
        "rgba",
        required_type=list,
        doc="The color of the text annotation in RGBA format"
    )

    text = otio.core.serializable_field(
        "text",
        required_type=str,
        doc="The text of the annotation"
    )

    spacing = otio.core.serializable_field(
        "spacing",
        required_type=float,
        doc="The spacing between lines of text"
    )

    font_size = otio.core.serializable_field(
        "font_size",
        required_type=float,
        doc="The size of the font for the text annotation"
    )

    scale = otio.core.serializable_field(
        "scale",
        required_type=float,
        doc="The scale of the text annotation"
    )

    rotation = otio.core.serializable_field(
        "rotation", 
        required_type=float,
        doc="The rotation of the text annotation in degrees"
    )

    font = otio.core.serializable_field(
        "font",
        required_type=str,
        doc="The font family of the text annotation"
    )

    def __str__(self):
        return "TextAnnotation({})".format(
            repr(self.position)+ ", " +repr(self.text)
        )

    def __repr__(self):
        return "otio.schemadef.SyncEvent.TextAnnotation(position={}, text={})".format(
            repr(self.position), repr(self.text)
        )