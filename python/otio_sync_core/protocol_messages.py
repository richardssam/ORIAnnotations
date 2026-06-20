"""Typed protocol message definitions for the OTIO Sync transport layer.

Each message class defined here is the **single source of truth** for one
transport-layer message: its ``command_schema``, its ``event`` name, and the
shape of its payload.  This mirrors how :mod:`SyncEvent` is the source of truth
for the OTIO message layer, and lets a documentation generator describe the
protocol directly from these classes (see ``docs/`` generator).

Design constraints (see the ``typed-protocol-messages`` change design doc):

* Messages are **pure data** — handler logic lives in the manager/patcher, not
  on the message classes — so the classes stay importable in isolation for
  documentation.
* Registration is explicit via the :func:`register` decorator, keyed on
  ``(SCHEMA, EVENT)``, so the receive-side dispatch registry cannot drift from
  the definitions.
* Serialization is explicit: ``to_payload()`` builds a plain ``dict`` without
  reflective whole-object walking (no :func:`dataclasses.asdict`) and without
  per-message ``isinstance`` validation, so hot-path messages
  (:class:`PartialAnnotation`, :class:`PlaybackSettingsSet`) stay cheap.
* The settings messages declare their known fields for documentation but
  **tolerate** unknown fields (carried in ``extras``) for forward-compatibility
  with independent producers.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# OTIO wire conversion
#
# These helpers are the single place that converts between OTIO objects and
# their wire form.  ``opentimelineio`` is imported lazily *inside* them so this
# module stays importable without OTIO installed (the documentation generator
# only reads class/field metadata, never calls these).  The format must stay
# byte-identical to the prior call-site serialization (``otio_json``,
# ``indent=-1``) so peers on older code keep interoperating.
# ---------------------------------------------------------------------------


def _to_wire(obj: Any) -> Any:
    """Serialize an OTIO object to its wire ``dict``; pass through a ``dict``.

    :param obj: An OTIO ``SerializableObject`` or an already-serialized dict.
    :returns: The wire-form dict.
    """
    if isinstance(obj, dict):
        return obj
    import opentimelineio as otio

    return json.loads(otio.adapters.write_to_string(obj, "otio_json", indent=-1))


def _from_wire(data: Any) -> Any:
    """Deserialize a wire ``dict`` to an OTIO object; pass through a non-dict.

    :param data: A wire-form dict, or an already-deserialized OTIO object.
    :returns: The OTIO ``SerializableObject``.
    """
    if not isinstance(data, dict):
        return data
    import opentimelineio as otio

    return otio.adapters.read_from_string(json.dumps(data), "otio_json")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Maps ``(command_schema, event)`` to the message class that defines it.
_REGISTRY: dict[tuple[str, str], type["ProtocolMessage"]] = {}


def register(cls: type["ProtocolMessage"]) -> type["ProtocolMessage"]:
    """Register *cls* in the protocol registry keyed on ``(SCHEMA, EVENT)``.

    Used as a class decorator.  Raises if two classes claim the same
    ``(SCHEMA, EVENT)`` pair, so collisions surface at import time.

    :param cls: A :class:`ProtocolMessage` subclass with ``SCHEMA``/``EVENT`` set.
    :returns: *cls* unchanged (decorator-compatible).
    """
    key = (cls.SCHEMA, cls.EVENT)
    if not cls.SCHEMA or not cls.EVENT:
        raise ValueError(f"{cls.__name__} must define non-empty SCHEMA and EVENT")
    if key in _REGISTRY:
        raise ValueError(
            f"Duplicate protocol message registration for {key}: "
            f"{_REGISTRY[key].__name__} and {cls.__name__}"
        )
    _REGISTRY[key] = cls
    return cls


def message_for(command_schema: str, event: str) -> "type[ProtocolMessage] | None":
    """Return the message class for ``(command_schema, event)``, or ``None``.

    :param command_schema: Envelope ``command_schema`` value.
    :param event: Envelope ``command.event`` value.
    :returns: The registered :class:`ProtocolMessage` subclass, or ``None`` when
        the pair is unknown (caller should ignore the message safely).
    """
    return _REGISTRY.get((command_schema, event))


def registered_messages() -> dict[tuple[str, str], type["ProtocolMessage"]]:
    """Return a copy of the full ``(schema, event) -> class`` registry.

    Used by the documentation generator to enumerate every protocol message.
    """
    return dict(_REGISTRY)


def doc_field(
    *,
    default: Any = MISSING,
    default_factory: Any = MISSING,
    doc: str = "",
):
    """Declare a dataclass field carrying a documentation string in metadata.

    The documentation generator reads ``field.metadata["doc"]`` for each field.

    :param default: Default value (mutually exclusive with *default_factory*).
    :param default_factory: Zero-arg callable producing the default.
    :param doc: Human-readable description of the field.
    """
    if default_factory is not MISSING:
        return field(default_factory=default_factory, metadata={"doc": doc})
    if default is not MISSING:
        return field(default=default, metadata={"doc": doc})
    return field(metadata={"doc": doc})


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ProtocolMessage:
    """Base class for all transport-layer protocol messages.

    Subclasses are ``@dataclass``-decorated and ``@register``-ed.  They set the
    class-level :attr:`SCHEMA` and :attr:`EVENT` constants and implement
    :meth:`to_payload` / :meth:`from_payload` explicitly.

    :cvar SCHEMA: The envelope ``command_schema`` for this message.
    :cvar EVENT: The envelope ``command.event`` for this message.
    :cvar ENVELOPE_SCHEMA: Optional top-level ``schema`` key written on the
        envelope (only :class:`IAmMaster` uses this for legacy compatibility).
    """

    SCHEMA: ClassVar[str] = ""
    EVENT: ClassVar[str] = ""
    ENVELOPE_SCHEMA: ClassVar["str | None"] = None

    def to_payload(self) -> dict[str, Any]:
        """Return the ``command.payload`` dict for this message."""
        raise NotImplementedError

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "ProtocolMessage":
        """Reconstruct a message instance from a received ``command.payload``."""
        raise NotImplementedError

    @classmethod
    def doc_fields(cls) -> list[tuple[str, str, str]]:
        """Return ``(name, type, description)`` triples for documentation.

        Default implementation reads the dataclass fields, skipping the
        ``extras`` catch-all used by tolerant messages.

        :returns: List of ``(field_name, type_name, doc)`` tuples.
        """
        out: list[tuple[str, str, str]] = []
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name == "extras":
                continue
            type_name = getattr(f.type, "__name__", str(f.type))
            out.append((f.name, type_name, f.metadata.get("doc", "")))
        return out


# ---------------------------------------------------------------------------
# Session family — LiveSession.1
# ---------------------------------------------------------------------------


@register
@dataclass
class WhoIsMaster(ProtocolMessage):
    """Master-discovery broadcast asking any existing master to identify itself."""

    SCHEMA = "LiveSession.1"
    EVENT = "WHO_IS_MASTER"

    requester_guid: str = doc_field(doc="GUID of the peer asking who the master is.")

    def to_payload(self) -> dict[str, Any]:
        return {"requester_guid": self.requester_guid}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "WhoIsMaster":
        return cls(requester_guid=data.get("requester_guid"))


@register
@dataclass
class IAmMaster(ProtocolMessage):
    """Master's response to discovery, announcing itself as session master."""

    SCHEMA = "LiveSession.1"
    EVENT = "I_AM_MASTER"
    #: Legacy top-level envelope schema preserved for older peers.
    ENVELOPE_SCHEMA = "SYNC_REVIEW_1.0"

    master_guid: str = doc_field(doc="GUID of the peer that is the session master.")

    def to_payload(self) -> dict[str, Any]:
        return {"master_guid": self.master_guid}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "IAmMaster":
        return cls(master_guid=data.get("master_guid"))


@register
@dataclass
class StateRequest(ProtocolMessage):
    """Joiner's request to the master for a full state snapshot."""

    SCHEMA = "LiveSession.1"
    EVENT = "STATE_REQUEST"

    target_guid: str = doc_field(doc="GUID of the master the request is aimed at.")
    requester_guid: str = doc_field(doc="GUID of the joining peer.")

    def to_payload(self) -> dict[str, Any]:
        return {"target_guid": self.target_guid, "requester_guid": self.requester_guid}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "StateRequest":
        return cls(
            target_guid=data.get("target_guid"),
            requester_guid=data.get("requester_guid"),
        )


@register
@dataclass
class StateSnapshot(ProtocolMessage):
    """Master's full session snapshot sent in response to a state request."""

    SCHEMA = "LiveSession.1"
    EVENT = "STATE_SNAPSHOT"

    target_guid: str = doc_field(doc="GUID of the joining peer this snapshot is for.")
    timelines: dict = doc_field(
        default_factory=dict,
        doc="Map of timeline GUID to OTIO timeline (objects on send, wire dicts on receive).",
    )
    active_timeline_guid: "str | None" = doc_field(
        default=None, doc="GUID of the active timeline at snapshot time."
    )
    snapshot_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the snapshot was taken."
    )
    playback_state: "dict | None" = doc_field(
        default=None, doc="Optional current playback state to seed the joiner."
    )
    display_state: "dict | None" = doc_field(
        default=None, doc="Optional current display state to seed the joiner."
    )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_guid": self.target_guid,
            "timelines": {g: _to_wire(tl) for g, tl in self.timelines.items()},
            "active_timeline_guid": self.active_timeline_guid,
            "snapshot_timestamp": self.snapshot_timestamp,
        }
        if self.playback_state is not None:
            payload["playback_state"] = self.playback_state
        if self.display_state is not None:
            payload["display_state"] = self.display_state
        return payload

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "StateSnapshot":
        return cls(
            target_guid=data.get("target_guid"),
            timelines=data.get("timelines", {}),
            active_timeline_guid=data.get("active_timeline_guid"),
            snapshot_timestamp=data.get("snapshot_timestamp"),
            playback_state=data.get("playback_state"),
            display_state=data.get("display_state"),
        )

    def as_otio(self) -> dict[str, Any]:
        """Return ``{guid: OTIO timeline}``, deserializing any wire-form entries."""
        return {g: _from_wire(tl) for g, tl in self.timelines.items()}


@register
@dataclass
class NewPresenter(ProtocolMessage):
    """Announces that a peer has become the session presenter."""

    SCHEMA = "LiveSession.1"
    EVENT = "NEW_PRESENTER"

    presenter_hash: str = doc_field(doc="Hash identifying the new presenter.")

    def to_payload(self) -> dict[str, Any]:
        return {"presenter_hash": self.presenter_hash}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "NewPresenter":
        return cls(presenter_hash=data.get("presenter_hash"))


@register
@dataclass
class NewParticipant(ProtocolMessage):
    """Announces that a new participant has joined the sync review."""

    SCHEMA = "LiveSession.1"
    EVENT = "NEW_PARTICIPANT"

    def to_payload(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "NewParticipant":
        return cls()


@register
@dataclass
class SharedKeyRequest(ProtocolMessage):
    """Requests the session's shared key from a peer."""

    SCHEMA = "LiveSession.1"
    EVENT = "SHARED_KEY_REQUEST"

    key: str = doc_field(doc="The shared key being requested.")

    def to_payload(self) -> dict[str, Any]:
        return {"key": self.key}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "SharedKeyRequest":
        return cls(key=data.get("key"))


@register
@dataclass
class SharedKeyResponse(ProtocolMessage):
    """Responds to a shared-key request with the session's shared key."""

    SCHEMA = "LiveSession.1"
    EVENT = "SHARED_KEY_RESPONSE"

    key: str = doc_field(doc="The shared key being returned.")

    def to_payload(self) -> dict[str, Any]:
        return {"key": self.key}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "SharedKeyResponse":
        return cls(key=data.get("key"))


# ---------------------------------------------------------------------------
# Timeline family — TIMELINE_1.0
# ---------------------------------------------------------------------------


@register
@dataclass
class AddTimeline(ProtocolMessage):
    """Registers a new timeline (sequence or single-clip) with all peers."""

    SCHEMA = "TIMELINE_1.0"
    EVENT = "ADD_TIMELINE"

    timeline_guid: str = doc_field(doc="GUID of the timeline being added.")
    timeline: Any = doc_field(
        doc="OTIO timeline (object on send, wire dict on receive)."
    )
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "timeline_guid": self.timeline_guid,
            "timeline": _to_wire(self.timeline),
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "AddTimeline":
        return cls(
            timeline_guid=data.get("timeline_guid"),
            timeline=data.get("timeline"),
            sync_timestamp=data.get("sync_timestamp"),
        )

    def as_otio(self) -> Any:
        """Return the OTIO timeline, deserializing if still in wire form."""
        return _from_wire(self.timeline)


@register
@dataclass
class RenameTimeline(ProtocolMessage):
    """Renames an existing timeline on all peers."""

    SCHEMA = "TIMELINE_1.0"
    EVENT = "RENAME_TIMELINE"

    timeline_guid: str = doc_field(doc="GUID of the timeline to rename.")
    name: str = doc_field(doc="New display name for the timeline.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "timeline_guid": self.timeline_guid,
            "name": self.name,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "RenameTimeline":
        return cls(
            timeline_guid=data.get("timeline_guid"),
            name=data.get("name", ""),
            sync_timestamp=data.get("sync_timestamp"),
        )


@register
@dataclass
class RemoveTimeline(ProtocolMessage):
    """Removes an existing timeline from all peers.

    Carries only the GUID — peers already hold the timeline, so no OTIO payload
    is needed.  Receivers that do not hold the GUID treat it as a no-op.
    """

    SCHEMA = "TIMELINE_1.0"
    EVENT = "REMOVE_TIMELINE"

    timeline_guid: str = doc_field(doc="GUID of the timeline to remove.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "timeline_guid": self.timeline_guid,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "RemoveTimeline":
        return cls(
            timeline_guid=data.get("timeline_guid"),
            sync_timestamp=data.get("sync_timestamp"),
        )


# ---------------------------------------------------------------------------
# Settings family — declare known fields, tolerate extras (hot paths)
# ---------------------------------------------------------------------------


@register
@dataclass
class PlaybackSettingsSet(ProtocolMessage):
    """Playback state broadcast.

    Hot path: fires on frame change during playback/scrubbing.  Known fields are
    declared for documentation; any additional producer fields are preserved in
    ``extras`` and round-tripped unchanged.
    """

    SCHEMA = "PLAYBACK_SETTINGS_1.0"
    EVENT = "SET"

    playing: "bool | None" = doc_field(default=None, doc="Whether playback is running.")
    current_time: "dict | None" = doc_field(
        default=None, doc="Current position as a serialized RationalTime."
    )
    looping: "bool | None" = doc_field(default=None, doc="Whether playback loops.")
    timeline_guid: "str | None" = doc_field(
        default=None, doc="GUID of the timeline being played."
    )
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )
    extras: dict = field(default_factory=dict)

    #: Field names modelled explicitly (everything else falls into ``extras``).
    _KNOWN: ClassVar[tuple[str, ...]] = (
        "playing",
        "current_time",
        "looping",
        "timeline_guid",
        "sync_timestamp",
    )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.playing is not None:
            payload["playing"] = self.playing
        if self.current_time is not None:
            payload["current_time"] = self.current_time
        if self.looping is not None:
            payload["looping"] = self.looping
        if self.timeline_guid is not None:
            payload["timeline_guid"] = self.timeline_guid
        if self.sync_timestamp is not None:
            payload["sync_timestamp"] = self.sync_timestamp
        payload.update(self.extras)
        return payload

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "PlaybackSettingsSet":
        extras = {k: v for k, v in data.items() if k not in cls._KNOWN}
        return cls(
            playing=data.get("playing"),
            current_time=data.get("current_time"),
            looping=data.get("looping"),
            timeline_guid=data.get("timeline_guid"),
            sync_timestamp=data.get("sync_timestamp"),
            extras=extras,
        )


@register
@dataclass
class DisplaySettingsSet(ProtocolMessage):
    """Display state broadcast (pan/zoom/exposure/channel).

    Known fields are declared for documentation; additional producer fields are
    preserved in ``extras``.
    """

    SCHEMA = "DISPLAY_SETTINGS_1.0"
    EVENT = "SET"

    pan: "list | None" = doc_field(default=None, doc="Normalised [x, y] pan offset.")
    zoom: "float | None" = doc_field(default=None, doc="Zoom multiplier (1.0 = none).")
    exposure: "float | None" = doc_field(
        default=None, doc="Exposure adjustment in stops (0.0 = none)."
    )
    channel: "str | None" = doc_field(
        default=None, doc='Active channel: "RGBA", "R", "G", "B", or "A".'
    )
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )
    extras: dict = field(default_factory=dict)

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "pan",
        "zoom",
        "exposure",
        "channel",
        "sync_timestamp",
    )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.pan is not None:
            payload["pan"] = self.pan
        if self.zoom is not None:
            payload["zoom"] = self.zoom
        if self.exposure is not None:
            payload["exposure"] = self.exposure
        if self.channel is not None:
            payload["channel"] = self.channel
        if self.sync_timestamp is not None:
            payload["sync_timestamp"] = self.sync_timestamp
        payload.update(self.extras)
        return payload

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "DisplaySettingsSet":
        extras = {k: v for k, v in data.items() if k not in cls._KNOWN}
        return cls(
            pan=data.get("pan"),
            zoom=data.get("zoom"),
            exposure=data.get("exposure"),
            channel=data.get("channel"),
            sync_timestamp=data.get("sync_timestamp"),
            extras=extras,
        )


# ---------------------------------------------------------------------------
# Selection family — SELECTION_1.0
# ---------------------------------------------------------------------------


@register
@dataclass
class SelectionSet(ProtocolMessage):
    """Broadcasts the clip the master has selected and the active view mode."""

    SCHEMA = "SELECTION_1.0"
    EVENT = "SET"

    clip_guid: str = doc_field(doc="Sync GUID of the selected clip ('' to clear).")
    view_mode: str = doc_field(default="source", doc='View mode: "source" or "sequence".')
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the message was sent."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "clip_guid": self.clip_guid,
            "view_mode": self.view_mode,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "SelectionSet":
        return cls(
            clip_guid=data.get("clip_guid"),
            view_mode=data.get("view_mode", "source"),
            sync_timestamp=data.get("sync_timestamp"),
        )


# ---------------------------------------------------------------------------
# Annotation family — Annotation.1 (hot path)
# ---------------------------------------------------------------------------


@register
@dataclass
class PartialAnnotation(ProtocolMessage):
    """Mid-stroke partial annotation (visual preview, not persisted).

    Hot path: fires repeatedly while a stroke is being drawn (before pen-up).
    Peers render the transient stroke visually, but do not write it to the local
    OTIO timeline. On pen-up/completion, a full stroke is committed via
    :class:`InsertChild` instead. No validation or reflective serialization is
    performed.
    """

    SCHEMA = "Annotation.1"
    EVENT = "PARTIAL"

    clip_guid: str = doc_field(doc="Sync GUID of the clip being annotated.")
    frame: float = doc_field(doc="0-indexed clip-local frame number.")
    fps: float = doc_field(doc="Frame rate used to interpret 'frame'.")
    # Deliberately kept as serialized SyncEvent dicts (NOT typed OTIO objects):
    # this is the hottest path and the host codec already produces dicts, so a
    # typed field would force wasteful deserialize-then-reserialize churn.
    events: list = doc_field(
        default_factory=list, doc="Serialized SyncEvent dicts for the in-progress stroke."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "clip_guid": self.clip_guid,
            "frame": self.frame,
            "fps": self.fps,
            "events": self.events,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "PartialAnnotation":
        return cls(
            clip_guid=data.get("clip_guid"),
            frame=data.get("frame"),
            fps=data.get("fps"),
            events=data.get("events", []),
        )


# ---------------------------------------------------------------------------
# OTIO session family — OTIO_SESSION_1.0
# Single definition: built and consumed by patcher.py.  Payloads carry the
# wire form (already-serialized child_data / commands), so to_payload is a cheap
# field copy.
# ---------------------------------------------------------------------------


@register
@dataclass
class SetProperty(ProtocolMessage):
    """Sets a property or metadata path on an object."""

    SCHEMA = "OTIO_SESSION_1.0"
    EVENT = "SET_PROPERTY"

    target_uuid: str = doc_field(doc="GUID of the target object.")
    path: str = doc_field(doc="Property name or 'metadata/...' sub-path.")
    value: Any = doc_field(doc="New primitive value.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the mutation occurred."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "target_uuid": self.target_uuid,
            "path": self.path,
            "value": self.value,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "SetProperty":
        return cls(
            target_uuid=data.get("target_uuid"),
            path=data.get("path"),
            value=data.get("value"),
            sync_timestamp=data.get("sync_timestamp"),
        )


@register
@dataclass
class InsertChild(ProtocolMessage):
    """Inserts a child object into a parent container."""

    SCHEMA = "OTIO_SESSION_1.0"
    EVENT = "INSERT_CHILD"

    parent_uuid: str = doc_field(doc="GUID of the parent container.")
    child_data: Any = doc_field(
        doc="OTIO child object (object on send, wire dict on receive)."
    )
    index: int = doc_field(default=-1, doc="Insert position; -1 appends.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the mutation occurred."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "parent_uuid": self.parent_uuid,
            "index": self.index,
            "child_data": _to_wire(self.child_data),
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "InsertChild":
        return cls(
            parent_uuid=data.get("parent_uuid"),
            child_data=data.get("child_data"),
            index=data.get("index", -1),
            sync_timestamp=data.get("sync_timestamp"),
        )

    def as_otio(self) -> Any:
        """Return the OTIO child object, deserializing if still in wire form."""
        return _from_wire(self.child_data)


@register
@dataclass
class MoveChild(ProtocolMessage):
    """Moves a child to a new index within its parent container."""

    SCHEMA = "OTIO_SESSION_1.0"
    EVENT = "MOVE_CHILD"

    parent_uuid: str = doc_field(doc="GUID of the parent container.")
    child_uuid: str = doc_field(doc="GUID of the child to move.")
    to_index: int = doc_field(default=0, doc="Target position in the parent.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the mutation occurred."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "parent_uuid": self.parent_uuid,
            "child_uuid": self.child_uuid,
            "to_index": self.to_index,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "MoveChild":
        return cls(
            parent_uuid=data.get("parent_uuid"),
            child_uuid=data.get("child_uuid"),
            to_index=data.get("to_index", 0),
            sync_timestamp=data.get("sync_timestamp"),
        )


@register
@dataclass
class RemoveChild(ProtocolMessage):
    """Removes a child from its parent container."""

    SCHEMA = "OTIO_SESSION_1.0"
    EVENT = "REMOVE_CHILD"

    parent_uuid: str = doc_field(doc="GUID of the parent container.")
    child_uuid: str = doc_field(doc="GUID of the child to remove.")
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the mutation occurred."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "parent_uuid": self.parent_uuid,
            "child_uuid": self.child_uuid,
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "RemoveChild":
        return cls(
            parent_uuid=data.get("parent_uuid"),
            child_uuid=data.get("child_uuid"),
            sync_timestamp=data.get("sync_timestamp"),
        )


@register
@dataclass
class ReplaceAnnotationCommands(ProtocolMessage):
    """Replaces the full annotation-command list on an annotation clip.

    Used when modifying/updating existing committed annotations in-place
    (e.g., editing text/captions or dragging/moving them), rather than
    appending a delta or committing a new stroke.
    """

    SCHEMA = "OTIO_SESSION_1.0"
    EVENT = "REPLACE_ANNOTATION_COMMANDS"

    annotation_clip_guid: str = doc_field(doc="GUID of the annotation clip to update.")
    commands: list = doc_field(
        default_factory=list,
        doc="Full replacement list of OTIO SyncEvents (objects on send, wire dicts on receive).",
    )
    sync_timestamp: "float | None" = doc_field(
        default=None, doc="Epoch seconds when the mutation occurred."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "annotation_clip_guid": self.annotation_clip_guid,
            "commands": [_to_wire(c) for c in self.commands],
            "sync_timestamp": self.sync_timestamp,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "ReplaceAnnotationCommands":
        return cls(
            annotation_clip_guid=data.get("annotation_clip_guid"),
            commands=data.get("commands", []),
            sync_timestamp=data.get("sync_timestamp"),
        )

    def as_otio(self) -> list:
        """Return the OTIO SyncEvent list, deserializing any wire-form entries."""
        return [_from_wire(c) for c in self.commands]
