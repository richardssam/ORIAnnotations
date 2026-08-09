"""Core synchronisation manager for the OTIO Sync protocol.

:class:`SyncManager` maintains a GUID-indexed map of every OTIO object in the shared
session and coordinates mutations across a pluggable network layer.  It implements the
master-election handshake, delta buffering during join, and all broadcast helpers
defined in the OTIO Sync Protocol v1 proposal.
"""

from __future__ import annotations

import json
import logging as _logging
import queue
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import opentimelineio as otio

from . import authority
from .network import SyncNetworkProtocol
from .proxy import OTIOSyncProxy
from .patcher import OTIOPatcher, _otio_to_dict, _dict_to_otio
from .protocol_messages import (
    ProtocolMessage,
    message_for,
    AddTimeline,
    ClaimOwnership,
    DisplaySettingsSet,
    IAmMaster,
    InsertChild,
    PartialAnnotation,
    PlaybackSettingsSet,
    ReleaseOwnership,
    RemoveTimeline,
    RenameTimeline,
    ReplaceAnnotationCommands,
    PeerAnnounce,
    PeerDepart,
    ReplaceTimeline,
    SetProperty,
    StateRequest,
    StateSnapshot,
    WhoIsMaster,
)

_logger = _logging.getLogger("otio_sync")


def _log(msg: str) -> None:
    if _logger.handlers:
        _logger.debug(msg)


def sync_event_schema(cmd: Any) -> str:
    """Return the OTIO schema name for a SyncEvent object or a serialised dict.

    Centralises the ``hasattr(cmd, "schema_name") / isinstance(cmd, dict)``
    pattern that appears throughout annotation-handling code.

    :param cmd: A deserialised SyncEvent object or a raw ``dict`` whose
        ``"OTIO_SCHEMA"`` key carries the schema name.
    :returns: Schema name string (e.g. ``"PaintStart.1"``), or ``""`` if
        *cmd* is neither.
    :rtype: str
    """
    if hasattr(cmd, "schema_name"):
        return cmd.schema_name()
    if isinstance(cmd, dict):
        return cmd.get("OTIO_SCHEMA", "")
    return ""


def _cmd_uuid(cmd: Any) -> "str | None":
    """Return the ``uuid`` field of a SyncEvent object or a serialised dict.

    Centralises the ``getattr(cmd, "uuid", None) or cmd.get("uuid")`` pattern
    used throughout annotation-handling code. Duck-types the dict-like
    fallback via ``.get`` rather than ``isinstance(cmd, dict)`` -- a dict
    written into OTIO ``metadata`` and read back is an
    ``opentimelineio.core.AnyDictionary``, which supports ``.get`` but is not
    an ``isinstance`` match for ``dict``.

    :param cmd: A deserialised SyncEvent object or a raw/OTIO-wrapped dict.
    :returns: The command's uuid string, or ``None`` if absent.
    :rtype: str or None
    """
    uid = getattr(cmd, "uuid", None)
    if uid is None:
        get = getattr(cmd, "get", None)
        if callable(get):
            uid = get("uuid")
    return uid


#: Session has not yet started.
STATE_NONE = "NONE"
#: Broadcasting ``WHO_IS_MASTER``; waiting for a response.
STATE_DISCOVERING = "DISCOVERING"
#: Master found; waiting for a full state snapshot.
STATE_JOINING = "JOINING"
#: Snapshot received and applied; fully participating in the session.
STATE_SYNCED = "SYNCED"

#: Seconds between a peer's own ``PEER_ANNOUNCE`` heartbeats.
#:
#: The heartbeat is what makes silence meaningful: without it a peer that
#: announced once and then legitimately went quiet is indistinguishable from one
#: that died.  It asks for no answer, so cost is O(peers) per interval, not the
#: O(peers²) answer cascade.
PEER_HEARTBEAT_INTERVAL = 5.0

#: Seconds of silence after which a peer is presumed gone and dropped.
#:
#: Three missed heartbeats.  The margin is deliberately generous: a peer whose
#: poll thread has stalled (a machine under memory pressure, a long structural
#: rebuild) is alive but quiet, and dropping it early causes a needless host
#: re-election.  A peer wrongly dropped restores itself on its next heartbeat.
PEER_LIVENESS_TIMEOUT = 15.0

#: Seconds after a remote apply returns during which a local change is still
#: attributed to the peer whose message caused it.
#:
#: A remote message rarely changes the display from inside :meth:`apply_patch`.
#: It changes what the host application *holds*, and the display follows through
#: that application's own event or poll machinery — in the 2026-08-06 soak, 3.3 s
#: after the ``ADD_TIMELINE`` that started it.  A window that closed when the
#: apply returned would therefore see none of the changes it exists to attribute.
#:
#: This value is a starting point taken from that single trace and is expected to
#: be re-sized from the traces the provenance logging itself produces.  Too short
#: misses the attribution; too long risks blaming a peer for a host action taken
#: moments later, which is why the window records *which* peer and is logged
#: rather than acted on silently.
REMOTE_APPLY_SETTLE_SECONDS = 5.0

#: ``(command_schema, event)`` pairs that open no provenance window, because
#: they cannot change what any peer displays.
#:
#: Found the hard way.  The first soak with provenance logging tagged all 12
#: host selection events "remote-induced?", every one of them blaming
#: ``LiveSession.1/PEER_ANNOUNCE`` — the 5-second liveness heartbeat, against a
#: 5-second settle window.  158 heartbeats arrived over that session, so the
#: window was open essentially all the time and the signal said "a peer did
#: this" about a user clicking in their own bin.  A provenance window that never
#: closes is worse than none: it would have the host reverting its own user's
#: actions.
#:
#: This is a denylist, not an allowlist, and deliberately so.  A message type
#: added later defaults to *being tracked*; the failure mode of that default is
#: an over-attribution that shows up in a log, while the failure mode of the
#: opposite default is the bypass this change exists to close, silently
#: reopened.  ``STATE_SNAPSHOT`` is absent on purpose — it carries structure.
NON_DISPLAY_EVENTS = frozenset({
    ("LiveSession.1", "WHO_IS_MASTER"),
    ("LiveSession.1", "I_AM_MASTER"),
    ("LiveSession.1", "PEER_ANNOUNCE"),
    ("LiveSession.1", "PEER_DEPART"),
    ("LiveSession.1", "STATE_REQUEST"),
    ("BROADCAST_OWNERSHIP_1.0", "CLAIM_OWNERSHIP"),
    ("BROADCAST_OWNERSHIP_1.0", "RELEASE_OWNERSHIP"),
})


@dataclass
class OwnershipLease:
    """Local state for one broadcast-ownership channel (position/display/structure).

    ``deadline`` is a *local* ``time.monotonic()`` reading, set on receipt of a
    claim or a category broadcast — never a value carried on the wire, so no
    cross-machine clock sync is needed (design.md D3).  ``claim_ts`` is the
    current owner's wall-clock claim time, kept so a losing claim can still be
    compared against it while the lease is unconfirmed (see ``confirmed``).

    ``confirmed`` distinguishes a lease that only exists because a claim was
    granted from one that has been backed by at least one real broadcast in
    its category. This is the mechanism that reconciles two requirements that
    would otherwise conflict: two peers claiming the same free channel in the
    same latency window must still converge on one winner (design.md D2,
    "every peer applies the same rule to the same message set"), *and* a peer
    that is actively driving must never be interrupted mid-operation
    (design.md "owner holds until idle"). While unconfirmed, an incoming claim
    is resolved against the current claimant like any other pending one — so
    the earliest of the two racing claims wins on every peer regardless of
    arrival order. Once a real broadcast confirms the lease, incoming claims
    can no longer preempt it; they only queue as ``pending_claimant``.
    """

    owner_guid: "str | None" = None
    claim_ts: "float | None" = None
    deadline: "float | None" = None
    confirmed: bool = False
    pending_claimant: "tuple[float, str] | None" = None


class SyncManager:
    """Coordinates OTIO object synchronisation across a network session.

    The manager maintains two complementary data structures:

    * ``_object_map`` — a flat ``{guid: otio_object}`` index for O(1) lookup by GUID.
    * ``_timelines`` — a ``{guid: Timeline}`` map of every registered top-level timeline.

    All mutations (inserts, removals, property changes) are applied locally **and**
    broadcast to peers via the injected *network* backend.  Incoming messages are
    applied through :meth:`apply_patch`, which also fires registered observer callbacks
    so that the host application (e.g. the RV plugin) can react to remote changes.

    **Session lifecycle**

    1. Call :meth:`start_session` — status transitions to ``STATE_DISCOVERING``.
    2. The caller polls :meth:`receive_and_apply_all` until a ``master_found`` action
       is returned, then calls :meth:`request_state`.
    3. Status transitions to ``STATE_JOINING``; incoming non-session messages are
       buffered in ``_delta_buffer``.
    4. When a ``state_snapshot_received`` action is returned, the caller invokes
       :meth:`apply_snapshot` which applies the full state and replays buffered deltas
       before transitioning to ``STATE_SYNCED``.

    If no master responds within the discovery timeout (implemented in the caller),
    the caller elects itself master via :meth:`elect_self_as_master`, which owns
    every state transition the election entails.  Callers MUST NOT elect by
    assigning :attr:`is_master`, :attr:`master_guid`, or :attr:`status` directly.

    **Broadcast authority**

    Separately from mastership, the manager holds the *host* role — the peer
    permitted to broadcast **visibility** (which clip/sequence is shown, and in
    which view mode).  Position and annotation remain multi-writer.  Host is
    elected by capability from the peer table (:meth:`elect_host`) and is
    deliberately distinct from master: master is the snapshot authority, elected
    on liveness grounds, and a master re-election does not move visibility
    authority.  Authority is enforced inside the ``broadcast_*`` methods, which
    return :data:`~otio_sync_core.authority.SENT` /
    :data:`~otio_sync_core.authority.SUPPRESSED`; plugins never test "am I host".

    :param session_id: Logical session identifier; scopes all network messages.
    :param self_guid: Stable GUID for this peer; auto-generated when not provided.
    :param network: Network backend satisfying :class:`~otio_sync_core.network.SyncNetworkProtocol`.
        May be set or replaced after construction.
    :param app_name: Application this peer runs in (e.g. ``"xstudio"``,
        ``"openrv"``).  Ranks the peer for host election; an unrecognised name
        is still eligible, so a session of unranked peers still elects a host.
    :param capabilities: Roles this peer can hold; defaults to
        ``["visibility"]``.  Pass ``[]`` for a peer that must never host (the
        sync viewer, a recorder).
    """

    def __init__(
        self,
        session_id: str = "default_session",
        self_guid: str | None = None,
        network: SyncNetworkProtocol | None = None,
        app_name: str = "",
        capabilities: "list[str] | None" = None,
    ) -> None:
        self.session_id = session_id
        self.self_guid: str = self_guid or str(uuid.uuid4())
        self.network: SyncNetworkProtocol | None = network

        self.patcher = OTIOPatcher()
        self._timelines: dict[str, otio.schema.Timeline] = {}
        #: Maps seq_clip_guid → clip_timeline_guid for all single-clip timelines.
        self._clip_timelines: dict[str, str] = {}
        self.active_timeline_guid: str | None = None

        self._status_callbacks: list[Callable[[str], None]] = []
        self._playback_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._display_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._synced_callbacks: list[Callable[[], None]] = []

        # Register internal callback to broadcast property changes
        @self.patcher.on_property_changed
        def _on_local_property_changed(target_uuid: str, path: str, value: Any) -> None:
            if not self._is_syncing and self.network:
                self._send_message(
                    SetProperty(
                        target_uuid=target_uuid,
                        path=path,
                        value=value,
                        sync_timestamp=time.time(),
                    )
                )

        self.status: str = STATE_NONE
        self.is_master: bool = False
        self.master_guid: str | None = None

        #: Application name and capabilities advertised by this peer.
        self.app_name: str = app_name
        self.capabilities: list[str] = (
            list(capabilities)
            if capabilities is not None
            else [authority.CAPABILITY_VISIBILITY]
        )
        #: Whether this peer holds visibility authority.  Written only by
        #: :meth:`elect_host`; never assign it directly.
        self.is_host: bool = False
        #: GUID of the elected host, or ``None`` before the first election.
        self.host_guid: str | None = None
        #: Peer table feeding host election:
        #: ``{guid: {"app", "capabilities", "last_seen"}}``.
        #: Seeded with this peer so a solo session still elects a host.
        #:
        #: ``last_seen`` is a *local* monotonic-ish stamp taken when this peer
        #: heard from that one; it never crosses the wire, so no clock sync is
        #: needed.  This peer's own entry is exempt from aging (see
        #: :meth:`_age_out_peers`).
        self._peers: dict[str, dict[str, Any]] = {
            self.self_guid: {
                "app": self.app_name,
                "capabilities": list(self.capabilities),
                "last_seen": time.time(),
            }
        }
        #: When this peer last broadcast its own announcement.  Drives the
        #: liveness heartbeat in :meth:`tick`.
        self._last_announce_time: float = 0.0
        #: Host-election requests enqueued from threads other than the poll
        #: thread.  Drained in :meth:`tick`; see :meth:`request_host_election`.
        self._host_election_queue: "queue.Queue[str]" = queue.Queue()
        self._host_callbacks: list[Callable[["str | None", bool], None]] = []

        #: Write-lease state for the position/display/structure channels
        #: (``broadcast-ownership``). Visibility and annotation have no entry
        #: here: visibility is a static single writer and annotation stays
        #: multi-writer, neither needs contention resolution.
        self._leases: dict[str, OwnershipLease] = {
            channel: OwnershipLease() for channel in authority.LEASE_CHANNELS
        }

        self._delta_buffer: list[dict[str, Any]] = []
        #: True only while :meth:`apply_snapshot` replays buffered deltas, so
        #: they are applied rather than buffered straight back onto the list
        #: being iterated.
        self._replaying: bool = False

        #: Clip timelines this peer has announced, so :meth:`broadcast_clip_timeline`
        #: sends each once without the callers having to decide.
        self._announced_clip_timelines: set[str] = set()

        #: Per-category ``(last_logged_key, repeats_since)`` for the field-strip
        #: logs (see :meth:`_log_field_strip`).
        self._strip_log_runs: dict[str, tuple[Any, int]] = {}

        #: Remote-apply provenance (see :meth:`remote_apply_context`).  A stack
        #: rather than a flag because a handler may itself apply a message: a
        #: single "source" field would be left holding the inner message's peer
        #: after the inner apply returned, and a depth counter alone loses which
        #: peer the surviving frame belongs to.
        self._remote_apply_stack: list[dict[str, Any]] = []
        #: The most recently completed apply, kept for the settle window.  None
        #: until the first remote message is applied.
        self._remote_apply_settled: dict[str, Any] | None = None
        #: Seconds after an apply returns during which a local change is still
        #: attributable to the peer that sent it.  A display change reaches the
        #: host application through its own event/poll machinery, so it lands
        #: *after* the apply returns, not inside it.
        self.remote_apply_settle_seconds: float = REMOTE_APPLY_SETTLE_SECONDS

        #: Sync GUIDs the session has actually seen — carried in a structural
        #: message this peer sent, or in one it received.  Deliberately *not*
        #: ``_object_map``: the defect this guards against had the parent
        #: firmly in the local map and simply never announced, because the
        #: track was rebuilt with a fresh GUID after ``ADD_TIMELINE`` had gone
        #: out carrying the old one.  Only a record of what crossed the wire
        #: can tell those apart.
        self._session_guids: set[str] = set()
        #: Broadcasts whose parent this peer never published, most recent last.
        self._unpublished_parents: list[str] = []
        self._unpublished_parent_count: int = 0
        self._last_snapshot_time: float = 0
        self._last_who_is_master_time: float | None = None
        self._state_request_time: float | None = None

        #: Last received playback state dict; empty until the first playback message.
        self.playback_state: dict[str, Any] = {}
        #: Last received display state dict; empty until the first display message.
        #: Keys: ``pan`` ([x, y] normalised), ``zoom`` (float), ``exposure`` (stops),
        #: ``channel`` (``"RGBA"``, ``"R"``, ``"G"``, ``"B"``, or ``"A"``).
        self.display_state: dict[str, Any] = {}
        #: GUID of the clip most recently selected by a remote peer via a
        #: ``SELECTION`` broadcast.  ``None`` when the selection is cleared.
        self.selected_clip_guid: str | None = None

        #: Receive-side dispatch table: ``(command_schema, event)`` -> handler.
        #: Each handler takes ``(msg, data, source)`` and returns an
        #: ``(action, data)`` tuple or ``None``.  All OTIO_SESSION events route
        #: to a single handler that delegates to the patcher.
        self._handlers: dict[
            tuple[str, str],
            Callable[[ProtocolMessage, dict[str, Any], str], "tuple[str, Any] | None"],
        ] = {
            ("LiveSession.1", "WHO_IS_MASTER"): self._h_who_is_master,
            ("LiveSession.1", "I_AM_MASTER"): self._h_i_am_master,
            ("LiveSession.1", "PEER_ANNOUNCE"): self._h_peer_announce,
            ("LiveSession.1", "PEER_DEPART"): self._h_peer_depart,
            ("LiveSession.1", "STATE_REQUEST"): self._h_state_request,
            ("LiveSession.1", "STATE_SNAPSHOT"): self._h_state_snapshot,
            ("BROADCAST_OWNERSHIP_1.0", "CLAIM_OWNERSHIP"): self._h_claim_ownership,
            ("BROADCAST_OWNERSHIP_1.0", "RELEASE_OWNERSHIP"): self._h_release_ownership,
            ("PLAYBACK_SETTINGS_1.0", "SET"): self._h_playback_set,
            ("DISPLAY_SETTINGS_1.0", "SET"): self._h_display_set,
            ("TIMELINE_1.0", "ADD_TIMELINE"): self._h_add_timeline,
            ("TIMELINE_1.0", "RENAME_TIMELINE"): self._h_rename_timeline,
            ("TIMELINE_1.0", "REMOVE_TIMELINE"): self._h_remove_timeline,
            ("TIMELINE_1.0", "REPLACE_TIMELINE"): self._h_replace_timeline,
            ("Annotation.1", "PARTIAL"): self._h_partial_annotation,
            ("OTIO_SESSION_1.0", "SET_PROPERTY"): self._h_otio_session,
            ("OTIO_SESSION_1.0", "INSERT_CHILD"): self._h_otio_session,
            ("OTIO_SESSION_1.0", "MOVE_CHILD"): self._h_otio_session,
            ("OTIO_SESSION_1.0", "REMOVE_CHILD"): self._h_otio_session,
            ("OTIO_SESSION_1.0", "REPLACE_ANNOTATION_COMMANDS"): self._h_otio_session,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _object_map(self) -> dict[str, otio.core.SerializableObject]:
        return self.patcher.object_map

    @_object_map.setter
    def _object_map(self, val: dict[str, otio.core.SerializableObject]) -> None:
        self.patcher.object_map = val

    @property
    def _is_syncing(self) -> bool:
        return self.patcher._is_syncing

    @_is_syncing.setter
    def _is_syncing(self, val: bool) -> None:
        self.patcher._is_syncing = val

    @property
    def unresolved_patches(self) -> list[str]:
        """Patches that could not be applied because their target was unknown.

        Empty in a healthy session — a peer should never be sent a patch naming
        an object it was not given. A non-empty list means some peer broadcast
        against an object its peers do not hold, which is a defect at the
        *sender*, and previously produced no signal anywhere at all.

        :rtype: list
        """
        return self.patcher.unresolved_patches

    @property
    def unresolved_patch_count(self) -> int:
        """Total unresolved patches seen, including those aged out of the list."""
        return self.patcher.unresolved_patch_count

    @property
    def unpublished_parents(self) -> list[str]:
        """Structural broadcasts whose parent this peer never published.

        The sender-side counterpart to :attr:`unresolved_patches`, and sharper
        than it — a sender knows what it announced, so this excludes "I have
        not caught up yet", which a receiver can never rule out.

        Sharper is not the same as conclusive, and this is **not** a verdict.
        Two benign patterns produce entries, both seen in a green suite:

        * **Deterministically derived parents.**  Clip-timeline GUIDs are
          computed from shared inputs, so a peer holds the parent without
          anyone having sent it.  Observed: xStudio broadcast an annotation
          into a track it never announced and OpenRV applied it with zero
          unresolved patches.
        * **Insert-then-announce.**  A peer inserts into a timeline it is still
          building, then announces the whole timeline — parent and child
          together — immediately afterwards.

        So do not refuse a broadcast on this signal alone; see tasks.md 5.2.
        """
        return list(self._unpublished_parents)

    @property
    def unpublished_parent_count(self) -> int:
        """Total unpublished-parent broadcasts, including those aged out."""
        return self._unpublished_parent_count

    @property
    def _property_callbacks(self) -> list[Callable[[str, str, Any], None]]:
        return self.patcher._property_callbacks

    @property
    def _hierarchy_callbacks(self) -> list[Callable[[str, str, str], None]]:
        return self.patcher._hierarchy_callbacks

    @property
    def is_syncing(self) -> bool:
        """``True`` while a snapshot or incoming delta is being applied locally.

        Callers can read this to suppress outgoing broadcasts that would echo
        changes back to their source.

        :rtype: bool
        """
        return self._is_syncing

    @property
    def root_timeline(self) -> otio.schema.Timeline | None:
        """The active timeline, or the first registered timeline when none is active.

        :returns: Active :class:`~opentimelineio.schema.Timeline`, or ``None`` if no
            timelines have been registered.
        """
        if self.active_timeline_guid:
            tl = self._timelines.get(self.active_timeline_guid)
            if tl is not None:
                return tl
        return next(iter(self._timelines.values()), None)

    @property
    def timelines(self) -> dict[str, otio.schema.Timeline]:
        """Read-only view of all registered timelines, keyed by sync GUID."""
        return self._timelines

    @property
    def object_map(self) -> dict[str, otio.core.SerializableObject]:
        """Read-only view of the flat GUID → OTIO object index."""
        return self._object_map

    @property
    def active_clip_guid(self) -> "str | None":
        """Sequence clip GUID if the active timeline is a single-clip timeline, else ``None``.

        :rtype: str or None
        """
        if not self.active_timeline_guid:
            return None
        for clip_guid, tl_guid in self._clip_timelines.items():
            if tl_guid == self.active_timeline_guid:
                return clip_guid
        return None

    @property
    def sequence_timeline_guid(self) -> "str | None":
        """GUID of the first registered timeline that is *not* a clip timeline.

        :rtype: str or None
        """
        clip_tl_guids = set(self._clip_timelines.values())
        for guid in self._timelines:
            if guid not in clip_tl_guids:
                return guid
        return None

    # ------------------------------------------------------------------
    # Timeline Registration
    # ------------------------------------------------------------------

    def register_timeline(self, timeline: otio.schema.Timeline) -> OTIOSyncProxy:
        """Register a timeline, assign GUIDs to all its objects, and index them.

        Sets :attr:`active_timeline_guid` to the new timeline's GUID if no active
        timeline exists yet.

        :param timeline: The :class:`~opentimelineio.schema.Timeline` to register.
        :returns: An :class:`~otio_sync_core.proxy.OTIOSyncProxy` wrapping *timeline*
            so that attribute writes are automatically broadcast.
        """
        self._ensure_guid_and_map(timeline)
        guid = timeline.metadata["sync"]["guid"]
        self._timelines[guid] = timeline
        self._traverse_and_map(timeline)
        if self.active_timeline_guid is None:
            self.active_timeline_guid = guid
        return OTIOSyncProxy(timeline, self.patcher)

    def get_or_create_clip_timeline(self, clip_guid: str) -> "str | None":
        """Return the GUID of the single-clip timeline for *clip_guid*, creating it lazily.

        All peers independently derive the **same** GUIDs via :meth:`_derive_guid`,
        so no coordination message is required before clips can be used across
        peers.  Callers should broadcast the timeline via
        :meth:`broadcast_clip_timeline` the first time it is created so that
        peers without local creation can register the annotation track in their
        ``_object_map`` (required for receiving annotation ``INSERT_CHILD``
        patches).

        The clip copy inside the clip timeline shares the same sync GUID as the
        sequence clip.  :meth:`_traverse_and_map_preserve` ensures the sequence
        clip remains canonical in ``_object_map`` so that
        ``range_in_parent()`` returns the sequence-level position.

        :param clip_guid: Sync GUID of the target sequence clip.
        :returns: GUID of the clip timeline, or ``None`` if *clip_guid* is not
            a known :class:`~opentimelineio.schema.Clip`.
        :rtype: str or None
        """
        if clip_guid in self._clip_timelines:
            return self._clip_timelines[clip_guid]

        seq_clip = self._object_map.get(clip_guid)
        if seq_clip is None or not isinstance(seq_clip, otio.schema.Clip):
            _log(f"get_or_create_clip_timeline: clip {clip_guid} not in object_map or not a Clip")
            return None

        clip_tl_guid = self._derive_guid(f"clip_timeline:{clip_guid}")
        video_track_guid = self._derive_guid(f"clip_timeline_video_track:{clip_guid}")
        ann_track_guid = self._derive_guid(f"clip_timeline_ann_track:{clip_guid}")

        # Deep-copy the clip preserving its sync GUID so annotations cross-reference.
        clip_copy = _dict_to_otio(_otio_to_dict(seq_clip))
        clip_copy.metadata.setdefault("sync", {})["guid"] = clip_guid

        tl = otio.schema.Timeline(name=getattr(seq_clip, "name", None) or "clip")
        tl.metadata["sync"] = {"guid": clip_tl_guid}
        tl.metadata["clip_timeline_for"] = clip_guid

        video_track = otio.schema.Track(
            name="V1", kind=otio.schema.TrackKind.Video
        )
        video_track.metadata["sync"] = {"guid": video_track_guid}
        video_track.append(clip_copy)

        ann_track = otio.schema.Track(name="Annotations")
        ann_track.metadata["sync"] = {"guid": ann_track_guid}

        tl.tracks.append(video_track)
        tl.tracks.append(ann_track)

        self._timelines[clip_tl_guid] = tl
        # Use preserve so the sequence clip stays canonical in _object_map.
        self._traverse_and_map_preserve(tl)
        self._clip_timelines[clip_guid] = clip_tl_guid

        _log(
            f"get_or_create_clip_timeline: created clip_tl={clip_tl_guid[:8]} "
            f"for clip={clip_guid[:8]}"
        )
        return clip_tl_guid

    def broadcast_add_timeline(self, tl_guid: str, lease_gated: bool = True) -> str:
        """Broadcast a timeline to all peers so they can register it.

        Works for both sequence timelines (new playlist / new sequence) and
        single-clip annotation timelines.  Call once immediately after
        :meth:`register_timeline` to propagate a locally-created timeline to
        all connected peers.  Peers that already hold the same GUID silently
        ignore the message.

        Every suppression is logged.  It used to return ``SUPPRESSED`` from
        three branches without a word, and that is how a follower's clip
        timelines went unannounced across two full soaks with nobody able to see
        why: the message simply was not there.

        :param tl_guid: GUID of the timeline to broadcast.
        :param lease_gated: Whether the structure write-lease is required.  See
            :meth:`broadcast_clip_timeline` for the one case that passes False.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if not self.network or self.status != STATE_SYNCED:
            _log(
                f"broadcast_add_timeline: suppressed {tl_guid[:8]}"
                f" — not synced (status={self.status})"
            )
            return authority.SUPPRESSED
        if lease_gated and not self._owns_channel(authority.CHANNEL_STRUCTURE):
            owner = self._leases[authority.CHANNEL_STRUCTURE].owner_guid
            _log(
                f"broadcast_add_timeline: suppressed {tl_guid[:8]}"
                f" — no structure lease (owner={(owner or 'free')[:8]})"
            )
            return authority.SUPPRESSED
        tl = self._timelines.get(tl_guid)
        if tl is None:
            _log(f"broadcast_add_timeline: suppressed {tl_guid[:8]} — no such timeline")
            return authority.SUPPRESSED
        if lease_gated:
            self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
        self._send_message(
            AddTimeline(
                timeline_guid=tl_guid,
                timeline=tl,
                sync_timestamp=time.time(),
            )
        )
        # Everything in this timeline is now common knowledge, so later patches
        # may legitimately address any of it.
        self._note_session_guids(tl)
        return authority.SENT

    def broadcast_clip_timeline(self, tl_guid: str) -> str:
        """Broadcast a clip timeline to all peers so they can register its annotation track.

        Should be called once per clip timeline, immediately after
        :meth:`get_or_create_clip_timeline` returns a new GUID.  Peers that
        already have the timeline (same deterministic GUID) will skip the
        ``ADD_TIMELINE`` message.

        **Not gated on the structure write-lease**, unlike every other
        structural broadcast.  The lease exists to stop two peers making
        conflicting structural *mutations*; this is not one.  A clip timeline's
        GUID comes from :meth:`_derive_guid`, so every peer computes the same
        one from the same clip, and :meth:`_h_add_timeline` ignores a GUID it
        already holds.  Two peers announcing the same clip timeline therefore
        cannot conflict — the second message is a no-op by construction.

        Gating it did real damage.  A peer that did not hold the structure
        lease had these dropped silently, including from the annotation paths
        that need the peer to register the Annotations track before an
        ``INSERT_CHILD`` can bind to it.  Claiming the lease here instead would
        not fix it: :meth:`_apply_claim` queues a claim behind a *confirmed*
        owner rather than granting it, so the announcement would still be
        dropped whenever another peer was actively doing structural work — the
        case where a race is most likely, and the failure would be silent again.

        Announced at most once per clip timeline, tracked here rather than by
        the callers.  Every call site used to gate on
        ``clip_guid not in _clip_timelines`` — "did *I* create this?" — when the
        question is "have my peers been told?".  A clip timeline this peer built
        while *applying a remote instruction* answers no to the first and yes to
        the second, so it was never announced to anyone; on 2026-08-09 08:58 an
        isolation went unannounced for exactly that reason, and the annotation
        paths carry the same gate, where the cost is an INSERT_CHILD the peer
        cannot bind.

        :param tl_guid: GUID of the clip timeline to broadcast.
        :returns: ``SENT`` / ``SUPPRESSED``, as :meth:`broadcast_add_timeline`.
        :rtype: str
        """
        if tl_guid in self._announced_clip_timelines:
            return authority.SUPPRESSED
        status = self.broadcast_add_timeline(tl_guid, lease_gated=False)
        if status == authority.SENT:
            self._announced_clip_timelines.add(tl_guid)
        return status

    def broadcast_timeline_rename(self, tl_guid: str, new_name: str) -> str:
        """Rename a timeline locally and broadcast the change to all peers.

        Updates the timeline's ``name`` attribute in ``_timelines`` immediately,
        then sends a ``RENAME_TIMELINE`` message so all connected peers apply the
        same rename.

        :param tl_guid: GUID of the timeline to rename.
        :param new_name: New display name for the timeline.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if self._is_syncing or not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_STRUCTURE):
            return authority.SUPPRESSED
        tl = self._timelines.get(tl_guid)
        if tl is None:
            _log(f"broadcast_timeline_rename: unknown timeline {tl_guid}")
            return authority.SUPPRESSED
        self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
        tl.name = new_name
        self._send_message(
            RenameTimeline(
                timeline_guid=tl_guid,
                name=new_name,
                sync_timestamp=time.time(),
            )
        )
        return authority.SENT

    def broadcast_remove_timeline(self, tl_guid: str) -> str:
        """Remove a timeline locally and broadcast the removal to all peers.

        Symmetric to :meth:`broadcast_add_timeline`.  Performs the same
        reference-aware teardown as the receive handler (single-timeline
        object-map cleanup plus clip-annotation cascade), then sends a
        ``REMOVE_TIMELINE`` message so all peers drop the timeline too.

        Per the host ordering contract, callers should move the on-screen
        source to a surviving timeline *before* calling this, so the removed
        timeline is not the active one except when it is the last remaining.

        :param tl_guid: GUID of the timeline to remove.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_STRUCTURE):
            return authority.SUPPRESSED
        if self._remove_timeline_local(tl_guid) is None:
            return authority.SUPPRESSED
        self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
        self._send_message(
            RemoveTimeline(
                timeline_guid=tl_guid,
                sync_timestamp=time.time(),
            )
        )
        return authority.SENT

    def broadcast_replace_timeline(self, tl_guid: str) -> str:
        """Push a wholesale replacement of a timeline's structure to all peers.

        Used for topology changes on OTIO-origin timelines (clip insert/remove,
        large re-edit), where rebuilding from the full OTIO via the native reader
        is cheaper and higher-fidelity than a stream of per-child patches.  The
        caller is expected to have already re-registered the updated timeline
        locally (e.g. re-exported via RV's ``create_timeline_from_node``), so
        this re-maps the local object map and sends the current timeline.

        Unlike :meth:`broadcast_add_timeline`, peers that already hold the GUID
        do **not** ignore this message — they replace their copy.

        :param tl_guid: GUID of the timeline to push.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_STRUCTURE):
            return authority.SUPPRESSED
        tl = self._timelines.get(tl_guid)
        if tl is None:
            return authority.SUPPRESSED
        self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
        self._replace_timeline_local(tl_guid, tl)
        self._send_message(
            ReplaceTimeline(
                timeline_guid=tl_guid,
                timeline=tl,
                sync_timestamp=time.time(),
            )
        )
        return authority.SENT

    def reset_timelines(self) -> None:
        """Clear all registered timelines, the object map, and the active GUID.

        Used during master re-initialisation when the timeline data must be
        rebuilt from scratch (e.g. after the RV node graph settles).
        """
        self._timelines.clear()
        self._object_map.clear()
        self._clip_timelines.clear()
        self.active_timeline_guid = None

    @staticmethod
    def _subtree_guids(item: otio.core.SerializableObject) -> set[str]:
        """Return the sync GUIDs of *item* and every OTIO object beneath it.

        Walks the same structure as :meth:`Patcher.traverse_and_map` so the
        set matches exactly what was inserted into ``_object_map`` on register.

        :param item: Root OTIO object to traverse.
        :returns: Set of sync GUID strings found in the subtree.
        """
        def _walk(node: otio.core.SerializableObject):
            yield node
            if hasattr(node, "tracks"):
                stack = node.tracks
                yield stack
                for child in stack:
                    yield from _walk(child)
            elif hasattr(node, "__iter__") and not isinstance(node, str):
                for child in node:
                    yield from _walk(child)

        guids: set[str] = set()
        for obj in _walk(item):
            if not isinstance(obj, otio.core.SerializableObject):
                continue
            guid = obj.metadata.get("sync", {}).get("guid")
            if guid:
                guids.add(guid)
        return guids

    #: Detail entries retained for :attr:`unpublished_parents`.
    _UNPUBLISHED_HISTORY = 10

    def _note_session_guids(self, item: otio.core.SerializableObject) -> None:
        """Record that *item*'s subtree has crossed the wire.

        Called on both sides — after this peer broadcasts structure, and after
        it accepts structure from a peer — because either one makes the GUIDs
        common knowledge in the session.
        """
        self._session_guids |= self._subtree_guids(item)

    def _check_parent_published(self, kind: str, parent_uuid: str) -> bool:
        """Report a structural broadcast addressing an unpublished parent.

        Report-only by design (tasks.md 5.1): the guard is new and refusing a
        legitimate patch would be a worse failure than the one it detects, so
        it records and lets the message go.  Escalating to refusal is 5.2, and
        is gated on the suite staying green with this in place.

        :param kind: Message kind, for the record.
        :param parent_uuid: GUID the outgoing patch addresses as its parent.
        :returns: ``True`` when the parent is known to the session.
        """
        if not parent_uuid or parent_uuid in self._session_guids:
            return True
        self._unpublished_parent_count += 1
        detail = (
            f"{kind}: parent {str(parent_uuid)[:8]} never published by this peer "
            f"({len(self._session_guids)} guids announced)"
        )
        self._unpublished_parents.append(detail)
        del self._unpublished_parents[:-self._UNPUBLISHED_HISTORY]
        _log(f"UNPUBLISHED PARENT {detail}")
        return False

    def _purge_timeline_state(self, tl_guid: str) -> None:
        """Remove one timeline's own state without touching other timelines.

        Removes only the GUIDs in this timeline's subtree from the shared
        ``_object_map`` (never a clear-all), drops the ``_timelines`` entry, and
        removes any ``_clip_timelines`` reverse entry pointing at it.

        :param tl_guid: GUID of the timeline whose state to purge.
        """
        tl = self._timelines.get(tl_guid)
        if tl is not None:
            for guid in self._subtree_guids(tl):
                self._object_map.pop(guid, None)
        self._timelines.pop(tl_guid, None)
        for clip_guid, ct_guid in list(self._clip_timelines.items()):
            if ct_guid == tl_guid:
                del self._clip_timelines[clip_guid]

    def _remove_timeline_local(
        self, tl_guid: str
    ) -> "otio.schema.Timeline | None":
        """Reference-aware teardown of a single timeline and its clip timelines.

        Cascade-deletes the clip-annotation timelines owned by clips in this
        timeline's subtree (per-clip-instance GUIDs, so no cross-sequence
        sharing), purges each from ``_object_map``/``_timelines``/
        ``_clip_timelines``, and clears ``active_timeline_guid`` to ``None`` if
        it pointed at the removed timeline — never naming a successor, since the
        active timeline is re-asserted by the next ``PlaybackSettingsSet``.

        :param tl_guid: GUID of the timeline to remove.
        :returns: The removed timeline, or ``None`` if the GUID was unknown.
        """
        tl = self._timelines.get(tl_guid)
        if tl is None:
            return None
        subtree = self._subtree_guids(tl)
        cascade = [
            ct_guid
            for clip_guid, ct_guid in self._clip_timelines.items()
            if clip_guid in subtree
        ]
        for ct_guid in cascade:
            self._purge_timeline_state(ct_guid)
        self._purge_timeline_state(tl_guid)
        if self.active_timeline_guid == tl_guid:
            self.active_timeline_guid = None
        _log(
            f"remove_timeline: {tl_guid[:8]} "
            f"(+{len(cascade)} clip timeline(s) cascaded)"
        )
        return tl

    def _replace_timeline_local(
        self, tl_guid: str, tl: "otio.schema.Timeline"
    ) -> "otio.schema.Timeline":
        """Wholesale-replace a timeline's structure, preserving object GUIDs.

        Re-maps the new timeline's subtree into ``_object_map`` and purges any
        GUIDs that were present in the old structure but not the new one (e.g.
        clips removed by the edit), so attribute patches cannot resolve to a
        stale object.  GUIDs that persist across the replace (carried in each
        object's ``metadata.sync.guid``) keep their bindings — annotations keyed
        by clip GUID survive.  Unlike :meth:`_remove_timeline_local`, this does
        **not** cascade-delete clip-annotation timelines for persisting clips.
        An unknown GUID is simply created.

        :param tl_guid: GUID of the timeline being replaced (or created).
        :param tl: The new OTIO timeline.
        :returns: The new timeline.
        """
        old = self._timelines.get(tl_guid)
        if old is not None and old is not tl:
            stale = self._subtree_guids(old) - self._subtree_guids(tl)
            for g in stale:
                self._object_map.pop(g, None)
        self._timelines[tl_guid] = tl
        self._traverse_and_map(tl)
        if self.active_timeline_guid is None:
            self.active_timeline_guid = tl_guid
        return tl

    @staticmethod
    def _derive_guid(key: str) -> str:
        """Return a stable, deterministic UUID derived from *key*.

        Uses :func:`uuid.uuid5` so that all peers independently compute the
        same GUID for the same logical object (e.g. the clip timeline for a
        given sequence clip) without any coordination message.

        :param key: Namespace string (e.g. ``"clip_timeline:<seq_clip_guid>"``).
        :returns: UUID string.
        :rtype: str
        """
        return str(uuid.uuid5(uuid.NAMESPACE_OID, key))

    def _traverse_and_map(self, item: otio.core.SerializableObject) -> None:
        self.patcher.traverse_and_map(item)

    def _traverse_and_map_preserve(self, item: otio.core.SerializableObject) -> None:
        self.patcher.traverse_and_map_preserve(item)

    def _ensure_guid_and_map(self, obj: Any) -> None:
        self.patcher.ensure_guid_and_map(obj)

    # ------------------------------------------------------------------
    # Observer Registry
    # ------------------------------------------------------------------

    def on_property_changed(
        self, callback: Callable[[str, str, Any], None]
    ) -> Callable[[str, str, Any], None]:
        """Register a callback for property change events.

        Fires for both locally-initiated and remotely-applied property changes.
        May be used as a decorator.

        :param callback: Callable receiving ``(target_uuid, path, new_value)``.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self.patcher.on_property_changed(callback)
        return callback

    def on_hierarchy_changed(
        self, callback: Callable[[str, str, str], None]
    ) -> Callable[[str, str, str], None]:
        """Register a callback for hierarchy change events.

        Fires for both locally-initiated and remotely-applied structural changes.
        May be used as a decorator.

        :param callback: Callable receiving ``(parent_uuid, action, child_uuid)``
            where *action* is one of ``"insert_child"`` or ``"remove_child"``.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self.patcher.on_hierarchy_changed(callback)
        return callback

    def on_status_changed(
        self, callback: Callable[[str], None]
    ) -> Callable[[str], None]:
        """Register a callback fired whenever :attr:`status` transitions.

        :param callback: Callable receiving the new status string.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._status_callbacks.append(callback)
        return callback

    def on_playback_changed(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register a callback fired whenever a playback-state message arrives.

        The callback receives the raw playback state dict (same structure as
        :attr:`playback_state`).  Also usable as a decorator.

        :param callback: Callable receiving the playback state dict.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._playback_callbacks.append(callback)
        return callback

    def on_display_changed(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register a callback fired whenever a display-state message arrives.

        The callback receives the raw display state dict (same structure as
        :attr:`display_state`).  Also usable as a decorator.

        :param callback: Callable receiving the display state dict.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._display_callbacks.append(callback)
        return callback

    def on_synced(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback fired once when the session reaches ``STATE_SYNCED``.

        Fires both when this peer self-elects as master and when it finishes
        joining an existing master.  Also usable as a decorator.

        :param callback: Zero-argument callable.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._synced_callbacks.append(callback)
        return callback

    def _set_status(self, new_status: str) -> None:
        """Update :attr:`status` and fire registered status-change callbacks."""
        if new_status == self.status:
            return
        self.status = new_status
        for cb in self._status_callbacks:
            try:
                cb(new_status)
            except Exception as e:
                _log(f"on_status_changed callback error: {e}")
        if new_status == STATE_SYNCED:
            for cb in self._synced_callbacks:
                try:
                    cb()
                except Exception as e:
                    _log(f"on_synced callback error: {e}")

    def _fire_property_changed(
        self, target_uuid: str, path: str, value: Any
    ) -> None:
        for cb in self._property_callbacks:
            try:
                cb(target_uuid, path, value)
            except Exception as e:
                _log(f"on_property_changed callback error: {e}")

    def _fire_hierarchy_changed(
        self, parent_uuid: str, action: str, child_uuid: str
    ) -> None:
        for cb in self._hierarchy_callbacks:
            try:
                cb(parent_uuid, action, child_uuid)
            except Exception as e:
                _log(f"on_hierarchy_changed callback error: {e}")

    # ------------------------------------------------------------------
    # Master Election & Session State
    # ------------------------------------------------------------------

    def start_session(self) -> None:
        """Begin the join process by broadcasting a master-discovery message.

        Transitions :attr:`status` to ``STATE_DISCOVERING``.  The caller is
        responsible for timing out and calling the appropriate method if no master
        responds (see class docstring for the full lifecycle).

        Also announces this peer so every other peer can include it in host
        election, and elects a host locally so a solo session is never hostless.
        """
        self._set_status(STATE_DISCOVERING)
        self.broadcast_master_discovery()
        self.announce_peer()
        self.elect_host()

    def broadcast_master_discovery(self) -> None:
        """Broadcast a ``WHO_IS_MASTER`` session message."""
        self._send_message(WhoIsMaster(requester_guid=self.self_guid))

    def broadcast_master_response(self) -> None:
        """Broadcast an ``I_AM_MASTER`` session message.

        Called after self-election (discovery timeout) or when an existing master
        receives a ``WHO_IS_MASTER`` it should answer.
        """
        self._send_message(IAmMaster(master_guid=self.self_guid))

    def elect_self_as_master(self, broadcast: bool = True) -> None:
        """Elect this peer as the session master.

        The single election operation: it owns every state transition that
        becoming master entails, so no caller has to assemble the sequence
        itself.  Used by all self-election paths — the hosts' discovery and
        state-request timeouts, and the master-failover check in :meth:`tick`.

        The order matters.  :attr:`is_master` and :attr:`master_guid` are set
        *before* the status transition, because ``_set_status(STATE_SYNCED)``
        fires the :meth:`on_synced` callbacks and those callbacks branch on
        :attr:`is_master` (a client loads the master's timelines; a master does
        not).  The broadcast precedes the status change to keep the wire order
        the pre-encapsulation call sites produced.

        :param broadcast: When ``False``, apply the local election state but do
            not announce it; the caller MUST call
            :meth:`broadcast_master_response` itself once it is ready to serve a
            ``STATE_REQUEST``.  OpenRV needs this: it must claim mastership
            synchronously (its 33 ms poll re-checks ``STATE_DISCOVERING`` and
            would otherwise re-enter its master init on the next tick) but can
            only announce after its deferred OTIO expansion has built the
            timelines a joiner's snapshot needs.
        """
        self.is_master = True
        self.master_guid = self.self_guid
        if broadcast:
            self.broadcast_master_response()
        self._set_status(STATE_SYNCED)

    # ------------------------------------------------------------------
    # Host Election (visibility authority)
    # ------------------------------------------------------------------

    def announce_peer(self) -> None:
        """Broadcast this peer's identity and capabilities.

        Feeds every other peer's election table, and doubles as this peer's
        liveness heartbeat.  Called from :meth:`start_session` and periodically
        from :meth:`_heartbeat`.

        Nobody answers an announcement.  A joiner learns the existing peer set
        from the snapshot it already requests (:meth:`adopt_peers`), and a peer
        that has gone quiet is re-learned from its next heartbeat — so the
        answer cascade that used to serve those purposes is gone, along with the
        only step whose message count grew with the size of the session.

        A peer on older code still sends a ``reply_requested`` flag and expects
        answers.  It gets none, and instead learns this peer from the next
        heartbeat — a few seconds later rather than immediately, which is the
        whole of the incompatibility.
        """
        # Any announcement counts as this peer's heartbeat, so joining or
        # answering resets the clock and the periodic one does not pile on.
        self._last_announce_time = time.time()
        self._send_message(PeerAnnounce(
            peer_guid=self.self_guid,
            app=self.app_name,
            capabilities=list(self.capabilities),
        ))

    def on_host_changed(
        self, callback: Callable[["str | None", bool], None]
    ) -> Callable[["str | None", bool], None]:
        """Register a callback fired when the elected host changes.

        The callback receives ``(host_guid, is_host)`` and observes a fully
        elected manager — both :attr:`host_guid` and :attr:`is_host` are set
        before it runs.  Also usable as a decorator.

        :param callback: Callable receiving ``(host_guid, is_host)``.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._host_callbacks.append(callback)
        return callback

    def request_host_election(self, reason: str = "") -> None:
        """Ask the poll thread to re-run host election.

        The **only** thread-safe entry point.  Host state belongs to a single
        writer — the thread that calls :meth:`tick` — so callers on other
        threads enqueue rather than mutate, exactly as ``fix-discovery-thread-
        safety`` did for master election.  Eligibility is re-evaluated when the
        request is *drained*, not when it is enqueued, so a peer discovered
        during queue latency is accounted for by the election it triggered.

        :param reason: Short label recorded in the log, e.g. ``"peer-announce"``.
        """
        self._host_election_queue.put(reason or "unspecified")

    def elect_host(self) -> "str | None":
        """Re-evaluate the host from the peer table and apply the result.

        The single host-election operation: it owns every field the transition
        touches, so no call site assembles the sequence itself.  Callers MUST
        NOT elect by assigning :attr:`host_guid` or :attr:`is_host` directly.

        The order matters, for the same reason it does in
        :meth:`elect_self_as_master`: :attr:`host_guid` and :attr:`is_host` are
        both set *before* the :meth:`on_host_changed` callbacks fire, so a
        callback never observes a half-elected manager.

        Election is a pure function of the peer table
        (:func:`~otio_sync_core.authority.elect_host_guid`), so two peers
        evaluating the same peers reach the same host with no claim protocol —
        and a peer that elects itself before hearing about a preferred peer
        simply re-elects when that peer's announcement arrives.  Both host
        applications reach this same code, so their post-election state is
        identical by construction rather than by hand-replication.

        Must run on the poll thread; other threads call
        :meth:`request_host_election`.

        :returns: The elected host GUID, or ``None`` when no peer is capable.
        :rtype: str or None
        """
        elected = authority.elect_host_guid(self._peers)
        if elected == self.host_guid:
            return elected
        previous = self.host_guid
        self.host_guid = elected
        self.is_host = elected == self.self_guid
        _log(
            f"elect_host: {(previous or 'none')[:8]} → {(elected or 'none')[:8]}"
            f" (self={'HOST' if self.is_host else 'follower'},"
            f" peers={len(self._peers)})"
        )
        for cb in self._host_callbacks:
            try:
                cb(self.host_guid, self.is_host)
            except Exception as e:
                _log(f"on_host_changed callback error: {e}")
        return elected

    def _peer_roster(self) -> dict[str, dict[str, Any]]:
        """Return the peer table in wire form, without liveness stamps.

        ``last_seen`` is deliberately excluded: it is the *receiver's* own clock
        reading of when it last heard from that peer.  Sending it would put one
        machine's clock on the wire and require skew handling to interpret, for
        no gain — the receiver stamps its own on adoption.

        :returns: ``{guid: {"app", "capabilities"}}``.
        """
        return {
            guid: {
                "app": peer.get("app", ""),
                "capabilities": list(peer.get("capabilities") or []),
            }
            for guid, peer in self._peers.items()
        }

    def adopt_peers(self, peers: "dict[str, dict[str, Any]] | None") -> None:
        """Merge a snapshot's peer roster into the local peer table.

        Lets a joiner learn the session's peers from the snapshot it already
        requested, instead of every peer answering its announcement.

        Merge rather than replace, and ignore an absent or empty roster: a peer
        predating this field sends no roster, and treating that as "no peers"
        would blank a table this peer has already populated from announcements.
        Liveness is stamped locally on adoption, for the reason in
        :meth:`_peer_roster`.

        :param peers: Roster from the snapshot, or ``None`` when absent.
        """
        if not peers:
            return
        now = time.time()
        learned = 0
        for guid, peer in peers.items():
            if guid == self.self_guid or guid in self._peers:
                continue
            self._peers[guid] = {
                "app": peer.get("app", ""),
                "capabilities": list(peer.get("capabilities") or []),
                "last_seen": now,
            }
            learned += 1
        if learned:
            _log(f"adopt_peers: learned {learned} peer(s) ({len(self._peers)} total)")

    def adopt_host(self, host_guid: "str | None") -> None:
        """Adopt a host learned from session state rather than from election.

        Used when applying a ``STATE_SNAPSHOT``: a joiner must not assume it is
        host and start fighting the real one before its own announcement has
        been answered.  Applies the same field order as :meth:`elect_host` and
        fires the same callbacks.

        A later :meth:`elect_host` may still move the role — a joining xStudio
        legitimately takes visibility from an OpenRV-only session's host — but
        it does so from a complete peer table rather than from an empty one.

        :param host_guid: Host GUID carried in the snapshot; ``None`` is ignored
            so a snapshot from a peer predating this field cannot clear a host
            already elected locally.
        """
        if not host_guid or host_guid == self.host_guid:
            return
        self.host_guid = host_guid
        self.is_host = host_guid == self.self_guid
        _log(f"adopt_host: {host_guid[:8]} (self={'HOST' if self.is_host else 'follower'})")
        for cb in self._host_callbacks:
            try:
                cb(self.host_guid, self.is_host)
            except Exception as e:
                _log(f"on_host_changed callback error: {e}")

    def owns_visibility(self) -> bool:
        """Return whether a local visibility transition may be read as user intent.

        The follower rule (D3): a peer that does not own a category must not
        infer, from a local state transition in that category, that a user
        caused it.  A follower's view changes because it applied someone else's
        message, and acting on that guess is what started playback on every peer
        and reset a playhead over a seek that had just landed.

        This is **not** the broadcast gate — that lives in ``broadcast_*`` and no
        plugin consults it.  It is the one shared predicate the two plugins use
        for their *local* intent branches (xStudio's auto-play on a Pinned Source
        Mode transition, its frame-0 reset on a new isolation), so the answer
        cannot drift between them and honours the same kill switch.  It replaces
        the per-plugin "was a peer driving in the last N ms?" time windows, which
        is the mechanism that kept failing.

        :rtype: bool
        """
        return self.is_host or not authority.enforcement_enabled()

    # ------------------------------------------------------------------
    # Broadcast Ownership (write leases: position, display, structure)
    # ------------------------------------------------------------------

    def _settle_lease_expiry(self, channel: str) -> None:
        """Apply expiry to *channel*'s lease if its deadline has passed.

        Lazy: evaluated on access (from :meth:`_owns_channel`, :meth:`_apply_claim`,
        and wire-section building) rather than on a timer, since nothing needs to
        react to an expiry except the next thing that consults ownership.  On
        expiry, a pending claimant is promoted (through the same resolution rule
        used everywhere else); otherwise the channel becomes free.

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        """
        lease = self._leases[channel]
        if lease.deadline is None or time.monotonic() < lease.deadline:
            return
        previous = lease.owner_guid
        if lease.pending_claimant is not None:
            claim_ts, guid = lease.pending_claimant
            lease.owner_guid = guid
            lease.claim_ts = claim_ts
            lease.deadline = time.monotonic() + authority.LEASE_DURATIONS[channel]
            lease.confirmed = False
            lease.pending_claimant = None
            _log(f"lease[{channel}]: expired ({(previous or '-')[:8]}), transferred to {guid[:8]}")
        else:
            lease.owner_guid = None
            lease.claim_ts = None
            lease.deadline = None
            lease.confirmed = False
            if previous:
                _log(f"lease[{channel}]: expired ({previous[:8]}), now free")

    def _owns_channel(self, channel: str) -> bool:
        """Return whether this peer currently holds *channel*'s write lease.

        The broadcast gate for position/display/structure, called from inside
        the relevant ``broadcast_*`` methods exactly as :meth:`owns_visibility`
        underpins :meth:`_enforce_visibility` — plugins never call this
        directly.  When ownership enforcement is disabled (the default for
        this commit; see :func:`authority.ownership_enforcement_enabled`),
        every peer is treated as owning every channel, so a disabled switch
        reverts broadcast behaviour completely rather than partially.

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        :rtype: bool
        """
        if not authority.ownership_enforcement_enabled():
            return True
        self._settle_lease_expiry(channel)
        return self._leases[channel].owner_guid == self.self_guid

    def _refresh_lease_confirmed(self, channel: str) -> None:
        """Refresh *channel*'s deadline and mark it confirmed by real traffic.

        Called after this peer is found to own *channel*, immediately before it
        broadcasts within it.  Marking ``confirmed`` is what makes
        :meth:`_apply_claim` stop treating the lease as merely provisional, so
        a peer that is genuinely driving is never interrupted mid-operation
        (see :class:`OwnershipLease`).

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        """
        if not authority.ownership_enforcement_enabled():
            return
        lease = self._leases[channel]
        lease.deadline = time.monotonic() + authority.LEASE_DURATIONS[channel]
        lease.confirmed = True

    def _apply_claim(self, channel: str, claim_ts: float, peer_guid: str) -> None:
        """Resolve a claim (local or received) against *channel*'s lease state.

        The single resolution path for both this peer's own claims and ones it
        receives, so both are decided by the identical rule (design.md D2's
        "a claim resolves identically on the claiming peer and every
        receiver").  See :class:`OwnershipLease` for why *confirmed* is the
        axis that decides whether an incoming claim can still win outright
        (unconfirmed — still within the initial contention window) or can only
        queue as ``pending_claimant`` (confirmed — actively held, not
        interrupted mid-operation).

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        :param claim_ts: Wall-clock time the claim was made.
        :param peer_guid: GUID of the claiming peer.
        """
        self._settle_lease_expiry(channel)
        lease = self._leases[channel]
        candidate = (claim_ts, peer_guid)

        if lease.owner_guid == peer_guid:
            # The current owner re-claiming (self-refresh, or an echo of its
            # own claim) just reaffirms what it already holds.
            lease.claim_ts = claim_ts
            self._refresh_lease(channel)
            return

        if lease.owner_guid is not None and lease.confirmed:
            # Actively held: never preempted, only queued (owner holds until idle).
            lease.pending_claimant = authority.resolve_claim(lease.pending_claimant, candidate)
            return

        # Free, or only provisionally claimed (not yet confirmed by real
        # traffic) — still within the initial contention window. Resolve the
        # incoming candidate against whatever is currently the best claim
        # (the provisional owner, if any, and any existing pending claimant),
        # so two peers racing to claim a free channel converge on the same
        # winner regardless of which claim each sees first.
        current = (lease.claim_ts, lease.owner_guid) if lease.owner_guid is not None else None
        best = authority.resolve_claim(current, candidate)
        if lease.pending_claimant is not None:
            best = authority.resolve_claim(best, lease.pending_claimant)
        lease.pending_claimant = None
        if current is not None and best == current:
            return
        lease.owner_guid = best[1]
        lease.claim_ts = best[0]
        self._refresh_lease(channel)

    def _refresh_lease(self, channel: str) -> None:
        """Push *channel*'s deadline forward without changing ``confirmed``.

        Used when granting or reaffirming a claim — distinct from
        :meth:`_refresh_lease_confirmed`, which additionally marks the lease
        as backed by real category traffic.

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        """
        self._leases[channel].deadline = time.monotonic() + authority.LEASE_DURATIONS[channel]

    def claim_category(self, channel: str) -> None:
        """Claim the write lease for *channel*, broadcasting ``CLAIM_OWNERSHIP``.

        Input-driven only: callers MUST call this only from a path caused by
        this peer's own local user input, never from the broadcast-suppression
        path or from applying a remote message.  Claiming from an echo would
        let ownership cycle back to a non-driving peer at the next expiry,
        reproducing the same feedback loop this mechanism exists to remove
        (design.md D4).

        Applies the claim to local state through the same resolution rule used
        for a received claim, then broadcasts it — so this peer's own view
        stays consistent with what every other peer will compute on receipt.

        A no-op while ownership enforcement is disabled (the kill switch):
        plugins are expected to call this unconditionally from every
        input-driven path (e.g. every scrub), and a disabled switch reverting
        enforcement "completely" (design.md D5) includes not flooding the
        session with claims nobody is going to honour.

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        :raises ValueError: If *channel* is not a recognised lease channel.
        """
        if channel not in authority.LEASE_CHANNELS:
            raise ValueError(f"unknown lease channel: {channel!r}")
        if not authority.ownership_enforcement_enabled():
            return
        claim_ts = time.time()
        self._apply_claim(channel, claim_ts, self.self_guid)
        self._send_message(
            ClaimOwnership(category=channel, peer_guid=self.self_guid, claim_ts=claim_ts)
        )

    def release_category(self, channel: str) -> None:
        """Release *channel*'s lease if this peer holds it, broadcasting the release.

        Frees the channel (or promotes a pending claimant) immediately rather
        than waiting for expiry, so a peer that is done driving hands off
        without a stall.  A no-op if this peer does not currently hold the
        lease, or while ownership enforcement is disabled (see
        :meth:`claim_category`).

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        :raises ValueError: If *channel* is not a recognised lease channel.
        """
        if channel not in authority.LEASE_CHANNELS:
            raise ValueError(f"unknown lease channel: {channel!r}")
        if not authority.ownership_enforcement_enabled():
            return
        if self._leases[channel].owner_guid != self.self_guid:
            return
        self._release_local(channel)
        self._send_message(ReleaseOwnership(category=channel, peer_guid=self.self_guid))

    def _release_local(self, channel: str) -> None:
        """Free *channel* locally (or promote its pending claimant), no broadcast.

        Shared by :meth:`release_category` (this peer releasing) and
        :meth:`_h_release_ownership` (another peer's release arriving).

        :param channel: One of :data:`authority.LEASE_CHANNELS`.
        """
        lease = self._leases[channel]
        if lease.pending_claimant is not None:
            claim_ts, guid = lease.pending_claimant
            lease.owner_guid = guid
            lease.claim_ts = claim_ts
            lease.deadline = time.monotonic() + authority.LEASE_DURATIONS[channel]
            lease.confirmed = False
            lease.pending_claimant = None
        else:
            lease.owner_guid = None
            lease.claim_ts = None
            lease.deadline = None
            lease.confirmed = False

    def _h_claim_ownership(
        self, msg: ClaimOwnership, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        if msg.category not in authority.LEASE_CHANNELS or not msg.peer_guid or msg.claim_ts is None:
            return None
        self._apply_claim(msg.category, msg.claim_ts, msg.peer_guid)
        return None

    def _h_release_ownership(
        self, msg: ReleaseOwnership, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        if msg.category not in authority.LEASE_CHANNELS:
            return None
        lease = self._leases[msg.category]
        if lease.owner_guid != msg.peer_guid:
            # Stale or foreign release (e.g. arriving after expiry already
            # moved the lease on); nothing to do.
            return None
        self._release_local(msg.category)
        return None

    def _lease_wire_section(self) -> dict[str, dict[str, Any]]:
        """Build the ``broadcast_ownership`` section for an outgoing ``STATE_SNAPSHOT``.

        A channel with no live owner is omitted entirely, mirroring the
        omit-when-unset convention already established for ``host_guid`` —
        see :meth:`adopt_ownership` for why that matters.

        :returns: ``{channel: {"owner_guid": str, "remaining_ms": float}}``,
            possibly empty.
        """
        now = time.monotonic()
        section: dict[str, dict[str, Any]] = {}
        for channel in authority.LEASE_CHANNELS:
            self._settle_lease_expiry(channel)
            lease = self._leases[channel]
            if lease.owner_guid is None or lease.deadline is None:
                continue
            section[channel] = {
                "owner_guid": lease.owner_guid,
                "remaining_ms": max(0.0, lease.deadline - now) * 1000.0,
            }
        return section

    @property
    def ownership_snapshot(self) -> dict[str, dict[str, Any]]:
        """Per-channel lease state, for test-harness visibility.

        Same shape and omit-when-unset convention as the wire section (see
        :meth:`_lease_wire_section`), exposed independently of building a full
        ``STATE_SNAPSHOT`` so a test hook can poll it cheaply — the pattern
        ``is_host``/``host_guid`` already established (host-owned-visibility
        §2.4).

        :returns: ``{channel: {"owner_guid": str, "remaining_ms": float}}``.
        """
        return self._lease_wire_section()

    def adopt_ownership(self, ownership: "dict[str, dict[str, Any]] | None") -> None:
        """Adopt write-lease state learned from a ``STATE_SNAPSHOT``.

        Mirrors :meth:`adopt_host`'s compatibility convention: an absent or
        empty payload is ignored entirely, and only channels actually present
        in *ownership* are touched — so a peer predating this field, or a
        snapshot taken while every channel happened to be free, cannot be
        mistaken for "every lease is free" and clear one this peer or another
        peer already holds.

        A channel this peer already holds live is left untouched: adoption is
        for a joiner *learning* the session's ownership view, not for a
        snapshot overriding state this peer is the current authority on.

        :param ownership: The snapshot's ``broadcast_ownership`` section, or
            ``None``/empty when absent.
        """
        if not ownership:
            return
        now = time.monotonic()
        for channel, info in ownership.items():
            if channel not in authority.LEASE_CHANNELS:
                continue
            owner_guid = info.get("owner_guid")
            remaining_ms = info.get("remaining_ms")
            if not owner_guid or remaining_ms is None:
                continue
            lease = self._leases[channel]
            if lease.owner_guid == self.self_guid and lease.deadline and lease.deadline > now:
                continue
            lease.owner_guid = owner_guid
            lease.claim_ts = None
            lease.deadline = now + max(0.0, remaining_ms / 1000.0)
            lease.confirmed = True
            lease.pending_claimant = None
            _log(f"adopt_ownership: {channel} -> {owner_guid[:8]} ({remaining_ms:.0f}ms remaining)")

    def drop_peer(self, peer_guid: str) -> None:
        """Forget a peer and re-elect the host if it held the role.

        The single removal transition, reached by both detection paths:
        :meth:`_h_peer_depart` when a peer announces its exit, and
        :meth:`_age_out_peers` when one stops announcing at all.  Neither
        removes the entry itself — a second election implementation is exactly
        what this operation exists to prevent.

        Re-electing is the point, not a side effect: because only the host may
        broadcast visibility, a departed host that stayed elected would leave
        the session's view frozen with no peer permitted to change it.

        :param peer_guid: GUID of the peer to forget.
        """
        if self._peers.pop(peer_guid, None) is None:
            return
        _log(f"drop_peer: {peer_guid[:8]} ({len(self._peers)} remaining)")
        self.elect_host()

    def request_state(self) -> None:
        """Send a ``STATE_REQUEST`` to the master and enter ``STATE_JOINING``.

        Non-session messages received while joining are buffered in
        ``_delta_buffer`` and replayed by :meth:`apply_snapshot`.
        """
        if self.master_guid:
            self._set_status(STATE_JOINING)
            self._state_request_time = time.time()
            self._send_message(StateRequest(
                target_guid=self.master_guid,
                requester_guid=self.self_guid,
            ))

    def send_state_snapshot(
        self,
        target_guid: str,
        playback_state: dict[str, Any] | None = None,
    ) -> None:
        """Serialise all registered timelines and send a full snapshot to a joiner.

        Only the master should call this method.  The snapshot is broadcast to the
        whole session (not unicast), but only the peer whose GUID matches *target_guid*
        will act on it.

        :param target_guid: GUID of the requesting peer.
        :param playback_state: Optional current playback state dict to include so the
            joiner can immediately seek to the right position.
        """
        if not self.is_master or not self._timelines:
            return
        self._send_message(StateSnapshot(
            target_guid=target_guid,
            timelines=dict(self._timelines),
            active_timeline_guid=self.active_timeline_guid,
            snapshot_timestamp=time.time(),
            playback_state=playback_state or None,
            display_state=self.display_state or None,
            host_guid=self.host_guid,
            peers=self._peer_roster(),
            broadcast_ownership=self._lease_wire_section() or None,
        ))
        # A snapshot publishes every timeline it carries, as surely as an
        # ADD_TIMELINE does for one.
        for tl in self._timelines.values():
            self._note_session_guids(tl)

    def export_state(self) -> dict[str, Any]:
        """Return this peer's current reduced state as a ``StateSnapshot`` payload.

        Produces the same dict shape the master sends in a ``STATE_SNAPSHOT``
        (``timelines``, ``active_timeline_guid``, ``playback_state``,
        ``display_state``) but **without touching the network**.  This is the
        "ask the client's own reducer" source used by the sync_test inspector to
        expose a peer's state for structural validation via
        :func:`otio_sync_core.project_state`.  Works on any peer, not only the
        master.

        Also carries ``is_master`` and ``is_host`` as extra top-level keys,
        purely for test harness visibility (e.g. waiting for a script-driven
        test's driver app to hold master before sending structural commands it
        would otherwise silently be unable to broadcast, or asserting which peer
        holds visibility authority — a distinction the harness cannot reason
        about unless it collects it). ``project_state``/``diff_states`` only read
        the named ``StateSnapshot`` fields, so these extra keys are inert for
        structural comparison.

        :returns: A ``StateSnapshot``-shaped payload dict (timelines in wire
            form), plus ``is_master`` and ``is_host``.
        """
        payload = StateSnapshot(
            target_guid="",
            timelines=dict(self._timelines),
            active_timeline_guid=self.active_timeline_guid,
            snapshot_timestamp=time.time(),
            playback_state=self.playback_state or None,
            display_state=self.display_state or None,
            host_guid=self.host_guid,
            peers=self._peer_roster(),
            broadcast_ownership=self._lease_wire_section() or None,
        ).to_payload()
        payload["is_master"] = self.is_master
        payload["is_host"] = self.is_host
        # Structural patches this peer could not apply. Carried for the same
        # reason as the flags above — harness visibility — and because a peer
        # that dropped patches is precisely one whose reported state should not
        # be trusted as "synced with the sender".
        payload["unresolved_patches"] = list(self.unresolved_patches)
        payload["unresolved_patch_count"] = self.unresolved_patch_count
        # The sender-side counterpart: sharper than the above, since it
        # excludes "not caught up yet", but still not a verdict — derived
        # parents and insert-then-announce both produce benign entries.
        payload["unpublished_parents"] = self.unpublished_parents
        payload["unpublished_parent_count"] = self.unpublished_parent_count
        return payload

    def _send_message(self, msg: ProtocolMessage) -> None:
        """Wrap a typed :class:`ProtocolMessage` in the envelope and send it.

        The envelope's ``command_schema``, ``command.event`` and
        ``command.payload`` are derived from the message class, so the message
        definition is the single source of truth for the wire format.

        :param msg: A registered :class:`ProtocolMessage` instance.
        """
        if not self.network:
            return
        envelope: dict[str, Any] = {
            "session": self.session_id,
            "source_guid": self.self_guid,
            "payload": {
                "command_schema": msg.SCHEMA,
                "command": {
                    "event": msg.EVENT,
                    "payload": msg.to_payload(),
                }
            }
        }
        if msg.ENVELOPE_SCHEMA is not None:
            envelope["schema"] = msg.ENVELOPE_SCHEMA
        self.network.send_payload(envelope)

    # ------------------------------------------------------------------
    # Data Mutations
    # ------------------------------------------------------------------

    def set_property(self, target_uuid: str, path: str, value: Any) -> None:
        """Set property *path* to *value* on object *target_uuid* and broadcast.

        Property paths are either plain attributes (e.g. ``"name"``) or metadata
        sub-paths starting with ``"metadata/"`` (e.g. ``"metadata/annotations"``).

        :param target_uuid: GUID of the target object.
        :param path: Target property or metadata sub-key path.
        :param value: New value; must be a primitive type.
        """
        self.patcher.set_property(target_uuid, path, value)

    def insert_child(
        self,
        parent_uuid: str,
        child_obj: otio.core.SerializableObject,
        index: int = -1,
    ) -> None:
        """Insert *child_obj* into the parent container and broadcast the change.

        A GUID is assigned to *child_obj* if it does not already have one.
        Use ``index=-1`` to append.

        :param parent_uuid: GUID of the parent container (Track or Stack).
        :param child_obj: OTIO object to insert.
        :param index: Position at which to insert; ``-1`` appends.
        """
        msg = self.patcher.insert_child(parent_uuid, child_obj, index)

        if not self._is_syncing and self.network and msg:
            _log(
                f"insert_child broadcasting: parent={parent_uuid} index={index} "
                f"child={getattr(child_obj, 'name', '?')}"
            )
            self._check_parent_published(InsertChild.EVENT, parent_uuid)
            self._send_message(msg)
            self._note_session_guids(child_obj)

    #: Emit a field-strip line every this many identical repeats, so a long
    #: unchanging run still shows its scale without waiting for it to end.
    STRIP_LOG_HEARTBEAT = 100

    def _log_field_strip(self, category: str, key: Any, message: str) -> None:
        """Log a field strip once per distinct *key*, not once per message.

        A follower broadcasts position on every rendered frame while the user
        scrubs, and each of those messages is stripped.  Logging each one buried
        the log: the 2026-08-09 12:27 session recorded 36 identical
        ``stripped visibility fields`` lines in three minutes, all naming the
        same clip.  What a reader needs is *that* it happened, for which view,
        and roughly how often — never 36 copies.

        A run that is still in progress is not silent: every
        :attr:`STRIP_LOG_HEARTBEAT` repeats emits a line, so a peer stuck
        stripping the same field group for minutes still says so.  The trailing
        count is folded into the line that ends the run.

        :param category: Field group being stripped; runs are tracked per group
            so visibility and position do not mask each other.
        :param key: What makes this strip distinct — repeats collapse when equal.
        :param message: The line to log when the run changes.
        """
        prev_key, repeats = self._strip_log_runs.get(category, (None, 0))
        if key == prev_key:
            repeats += 1
            self._strip_log_runs[category] = (key, repeats)
            if repeats % self.STRIP_LOG_HEARTBEAT == 0:
                _log(f"{message} (still, {repeats} identical so far)")
            return
        if repeats:
            message = f"{message} — previous run repeated {repeats}x"
        self._strip_log_runs[category] = (key, 0)
        _log(message)

    def _enforce_visibility(self, state: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Strip visibility fields when this peer is not the host.

        **The** enforcement point for the visibility category, deliberately in
        one place rather than at each call site: the failure this guards against
        is a follower that drops ``view_mode`` but still carries a ``clip_guid``,
        which still asserts what the session should look at.  Stripping the
        whole field group together makes that mistake unavailable.

        :param state: Outgoing playback/view state dict.
        :returns: ``(state_to_send, status)`` where *status* is
            :data:`~otio_sync_core.authority.SENT` when the message goes out
            intact, or :data:`~otio_sync_core.authority.SUPPRESSED` when
            visibility fields were removed from it.
        """
        if self.is_host or not authority.enforcement_enabled():
            return state, authority.SENT
        if not authority.asserts_visibility(state):
            return state, authority.SENT
        view_mode = state.get("view_mode")
        clip_guid = state.get("clip_guid")
        self._log_field_strip(
            authority.VISIBILITY,
            (view_mode, clip_guid, self.host_guid),
            "broadcast_playback_state: stripped visibility fields"
            f" (mode={view_mode!r} clip={(clip_guid or '-')[:8]})"
            f" — host is {(self.host_guid or 'unelected')[:8]}",
        )
        return authority.strip_visibility_fields(state), authority.SUPPRESSED

    def _enforce_position(self, state: dict[str, Any], status: str) -> tuple[dict[str, Any], str]:
        """Strip position fields when this peer does not hold the position lease.

        Called beside :meth:`_enforce_visibility` from :meth:`broadcast_playback_state`,
        so a single ``PLAYBACK_SETTINGS_1.0`` message can lose either field group,
        both, or neither, independently.  ``SUPPRESSED`` already means "sent, with
        some fields stripped", not "not sent" — a message that keeps its position
        fields but loses visibility (or vice versa) is the normal case, not an
        edge case (design.md D1).

        :param state: Outgoing state, already passed through :meth:`_enforce_visibility`.
        :param status: The status so far (SENT/SUPPRESSED from the visibility check).
        :returns: ``(state_to_send, status)``; *status* becomes SUPPRESSED if
            position fields are stripped here, even when the visibility check
            alone would have said SENT.
        """
        if self._owns_channel(authority.CHANNEL_POSITION):
            self._refresh_lease_confirmed(authority.CHANNEL_POSITION)
            return state, status
        if not authority.asserts_position(state):
            return state, status
        owner = self._leases[authority.CHANNEL_POSITION].owner_guid
        self._log_field_strip(
            authority.CHANNEL_POSITION,
            owner,
            "broadcast_playback_state: stripped position fields"
            f" — position owned by {(owner or 'free')[:8]}",
        )
        return authority.strip_position_fields(state), authority.SUPPRESSED

    def broadcast_playback_state(
        self,
        state_dict: dict[str, Any],
        timeline_guid: str | None = None,
    ) -> str:
        """Broadcast the current view/playback state to all peers.

        This is the single authoritative view-state message (SELECTION_1.0 is
        retired): besides ``playing``/``current_time``/``playback_mode`` it carries
        the view-state fields ``view_mode`` ("sequence"|"source") and ``clip_guid``
        when the caller includes them in *state_dict* (they round-trip through
        ``PlaybackSettingsSet.from_payload``).

        The message spans two authority categories — ``position`` (any peer) and
        ``visibility`` (host only) — so enforcement applies to the *fields*: a
        follower's message goes out carrying position, with the visibility
        fields removed by :meth:`_enforce_visibility`.

        :param state_dict: View/playback fields — ``playing``, ``current_time``,
            ``playback_mode``, and optionally ``view_mode`` and ``clip_guid``.
        :param timeline_guid: GUID of the timeline being viewed; falls back to
            :attr:`active_timeline_guid`.
        :returns: ``SENT`` when the state went out as given, ``SUPPRESSED`` when
            it was withheld or had visibility fields stripped.
        :rtype: str
        """
        if self._is_syncing or not self.network:
            return authority.SUPPRESSED
        inner = dict(state_dict)
        inner["sync_timestamp"] = time.time()
        inner["timeline_guid"] = timeline_guid or self.active_timeline_guid
        inner, status = self._enforce_visibility(inner)
        inner, status = self._enforce_position(inner, status)
        self._send_message(PlaybackSettingsSet.from_payload(inner))
        return status

    def clip_guid_at_frame(
        self, timeline_guid: "str | None", frame: int
    ) -> "str | None":
        """Return the sync GUID of the clip active at *frame* in a sequence.

        In sequence view-mode the active clip is a derived property of the
        playhead position (see the unify-view-state-sync change): we walk the
        timeline's non-Annotations video track, summing each clip's
        ``source_range`` duration, and return the GUID of the clip whose span
        contains *frame*.  This is identical on every peer regardless of
        per-peer clip GUIDs, which is why it is authoritative over a transmitted
        ``clip_guid`` in sequence mode.

        :param timeline_guid: GUID of the sequence timeline.
        :param frame: Sequence-relative frame (0-based).
        :returns: Clip sync GUID, or ``None`` if no clip covers the frame.
        """
        if not timeline_guid:
            return None
        tl = self._timelines.get(timeline_guid)
        if tl is None:
            return None
        for track in tl.tracks:
            if track.kind != otio.schema.TrackKind.Video or track.name == "Annotations":
                continue
            t = 0
            for child in track:
                sr = getattr(child, "source_range", None)
                dur = int(sr.duration.value) if sr is not None else 0
                if isinstance(child, otio.schema.Clip) and t <= frame < t + dur:
                    return child.metadata.get("sync", {}).get("guid")
                t += dur
        return None

    def broadcast_display_state(self, state_dict: dict[str, Any]) -> str:
        """Broadcast the current display state to all peers and persist it.

        Expected keys in *state_dict*:

        * ``pan``      — ``[x, y]`` normalised pan offset.
        * ``zoom``     — zoom multiplier (``1.0`` = no zoom).
        * ``exposure`` — exposure adjustment in stops (``0.0`` = no change).
        * ``channel``  — active channel string: ``"RGBA"``, ``"R"``, ``"G"``,
          ``"B"``, or ``"A"``.

        The state is also written into the active timeline's
        ``metadata["display_settings"]`` so it survives a full session teardown
        if the OTIO file is saved to disk.

        Categorised **position**, not visibility: reviewers legitimately toggle
        channels and exposure locally, so every peer may broadcast it (see the
        category table in :mod:`otio_sync_core.authority`).

        :param state_dict: Display state fields as listed above.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if self._is_syncing or not self.network:
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_DISPLAY):
            _log(
                "broadcast_display_state: suppressed — display lease held by "
                f"{(self._leases[authority.CHANNEL_DISPLAY].owner_guid or 'free')[:8]}"
            )
            return authority.SUPPRESSED
        self._refresh_lease_confirmed(authority.CHANNEL_DISPLAY)
        inner = dict(state_dict)
        inner["sync_timestamp"] = time.time()
        self.display_state = inner
        tl = self.root_timeline
        if tl is not None:
            tl.metadata["display_settings"] = {
                k: v for k, v in inner.items() if k != "sync_timestamp"
            }
        self._send_message(DisplaySettingsSet.from_payload(inner))
        return authority.SENT

    @staticmethod
    def _annotation_track_end(track: otio.schema.Track) -> int:
        """Return the total duration (in frames) of all children in *track*.

        This is the track position at which the next appended child would start,
        analogous to ``lastframe`` in ``ORIAnnotations._export_otio_media``.

        :param track: An OTIO :class:`~opentimelineio.schema.Track`.
        :returns: Sum of ``source_range.duration.value`` for all children.
        :rtype: int
        """
        total = 0
        for child in track:
            sr = getattr(child, "source_range", None)
            if sr is not None:
                total += int(sr.duration.value)
        return total

    @staticmethod
    def _find_annotation_clip_at(
        track: otio.schema.Track,
        clip_guid: str,
        frame: int,
    ) -> "otio.schema.Clip | None":
        """Find an existing annotation clip for *(clip_guid, frame)* in *track*.

        :param track: The Annotations :class:`~opentimelineio.schema.Track`.
        :param clip_guid: GUID of the media clip being annotated.
        :param frame: 0-indexed clip-local frame number.
        :returns: The matching :class:`~opentimelineio.schema.Clip`, or ``None``.
        """
        for child in track:
            if not isinstance(child, otio.schema.Clip):
                continue
            if child.metadata.get("clip_guid") != clip_guid:
                continue
            sr = getattr(child, "source_range", None)
            if sr is not None and int(sr.start_time.value) == frame:
                return child
        return None

    @staticmethod
    def _try_merge_annotation(
        parent: otio.schema.Track,
        child_obj: otio.core.SerializableObject,
    ) -> "otio.schema.Clip | None":
        """Check whether *child_obj* is an annotation-merge delta and apply it.

        If *parent* already contains a clip for the same ``(clip_guid, frame)``
        as *child_obj*, the incoming ``annotation_commands`` are appended to
        that existing clip and the existing clip is returned (so the caller can
        raise an ``annotation_commands_added`` event without inserting a
        structural duplicate).  Returns ``None`` when no merge applies.

        :param parent: The parent track that would receive *child_obj*.
        :param child_obj: The incoming OTIO object from an ``INSERT_CHILD`` message.
        :returns: The existing clip if a merge occurred, otherwise ``None``.
        """
        if not isinstance(parent, otio.schema.Track):
            return None
        if not hasattr(child_obj, "metadata"):
            return None
        incoming_cmds = child_obj.metadata.get("annotation_commands")
        incoming_cg = child_obj.metadata.get("clip_guid")
        incoming_sr = getattr(child_obj, "source_range", None)
        if not incoming_cmds or not incoming_cg or incoming_sr is None:
            return None
        incoming_frame = int(incoming_sr.start_time.value)
        existing = SyncManager._find_annotation_clip_at(
            parent, incoming_cg, incoming_frame
        )
        if existing is None:
            return None
        existing.metadata["annotation_commands"].extend(incoming_cmds)
        return existing

    @staticmethod
    def _make_annotation_clip(
        clip_guid: str,
        clip_local_time: otio.opentime.RationalTime,
        otio_events: list,
    ) -> otio.schema.Clip:
        """Build a 1-frame annotation clip for *clip_guid* at *clip_local_time*.

        :param clip_guid: GUID of the media clip being annotated.
        :param clip_local_time: 0-indexed time within the clip source range.
        :param otio_events: Deserialised SyncEvent objects to embed.
        :returns: A new :class:`~opentimelineio.schema.Clip`.
        """
        frame = int(clip_local_time.value)
        fps = clip_local_time.rate
        clip = otio.schema.Clip(name=f"Annotation_{frame}")
        clip.source_range = otio.opentime.TimeRange(
            clip_local_time,
            otio.opentime.RationalTime(1, fps),
        )
        clip.metadata["annotation_commands"] = otio_events
        clip.metadata["clip_guid"] = clip_guid
        return clip

    def annotation_track_guid_for_clip(
        self,
        clip_guid: str,
        preferred_timeline_guid: "str | None" = None,
    ) -> "str | None":
        """Return the GUID of the Annotations track in the same timeline as *clip_guid*.

        Searches every non-annotation track for *clip_guid*, then returns the
        first track whose name contains ``"annotation"`` (case-insensitive) from
        that same timeline.

        When *preferred_timeline_guid* is provided (e.g. the current
        :attr:`active_timeline_guid`), that timeline is searched first.  This
        ensures that annotations are written to the clip timeline's annotation
        track while in clip mode, rather than the sequence timeline's track.

        :param clip_guid: Sync GUID of the media clip.
        :param preferred_timeline_guid: GUID of the timeline to search first;
            falls back to all timelines if not found there.
        :returns: Annotation track GUID, or ``None`` if not found.
        :rtype: str or None
        """
        timelines = list(self._timelines.values())
        if preferred_timeline_guid:
            pref = self._timelines.get(preferred_timeline_guid)
            if pref is not None:
                timelines = [pref] + [t for t in timelines if t is not pref]

        for timeline in timelines:
            clip_found = False
            for track in timeline.tracks:
                if "annotation" in (track.name or "").lower():
                    continue
                for item in track:
                    if item.metadata.get("sync", {}).get("guid") == clip_guid:
                        clip_found = True
                        break
                if clip_found:
                    break
            if not clip_found:
                continue
            for track in timeline.tracks:
                if track.name and "annotation" in track.name.lower():
                    return track.metadata.get("sync", {}).get("guid")
        return None

    def annotation_clip_guid_at(self, clip_guid: str, frame: int) -> "str | None":
        """Return the sync GUID of the annotation clip at *(clip_guid, frame)*.

        Convenience wrapper around :meth:`annotation_track_guid_for_clip` and
        :meth:`_find_annotation_clip_at` that returns the clip's own GUID
        rather than the object itself.

        :param clip_guid: GUID of the media clip being annotated.
        :param frame: 0-indexed clip-local frame number.
        :returns: Annotation clip GUID, or ``None`` if not found.
        :rtype: str or None
        """
        ann_track_guid = self.annotation_track_guid_for_clip(clip_guid)
        if ann_track_guid is None:
            return None
        ann_track = self._object_map.get(ann_track_guid)
        if ann_track is None:
            return None
        clip = self._find_annotation_clip_at(ann_track, clip_guid, frame)
        if clip is None:
            return None
        return clip.metadata.get("sync", {}).get("guid")

    def count_annotation_commands(
        self, clip_guid: str, frame: int
    ) -> "tuple[int, int]":
        """Return ``(n_strokes, n_captions)`` already committed for *(clip_guid, frame)*.

        Counts ``PaintStart`` events (strokes) and ``TextAnnotation`` events
        (captions) in the annotation track.  Accumulates across all matching
        clips at the same frame so that old snapshots containing per-stroke
        clips are handled correctly.

        :param clip_guid: GUID of the media clip being annotated.
        :param frame: 0-indexed clip-local frame number.
        :returns: ``(n_strokes, n_captions)`` already in the annotation track.
        :rtype: tuple
        """
        ann_track_guid = self.annotation_track_guid_for_clip(clip_guid)
        if ann_track_guid is None:
            return 0, 0
        ann_track = self._object_map.get(ann_track_guid)
        if ann_track is None:
            return 0, 0
        n_strokes = 0
        n_captions = 0
        for item in ann_track:
            if not isinstance(item, otio.schema.Clip):
                continue
            if item.metadata.get("clip_guid") != clip_guid:
                continue
            sr = getattr(item, "source_range", None)
            if sr is None or int(sr.start_time.value) != frame:
                continue
            for cmd in item.metadata.get("annotation_commands", []):
                schema = sync_event_schema(cmd)
                if schema.startswith("PaintStart"):
                    n_strokes += 1
                elif schema.startswith("TextAnnotation"):
                    n_captions += 1
        return n_strokes, n_captions

    def annotation_clip_guid_for_stroke_uuid(self, stroke_uuid: str) -> "str | None":
        """Return the sync GUID of the annotation clip containing *stroke_uuid*.

        Scans every timeline's Annotations track(s) for a clip whose
        ``annotation_commands`` includes a command with this uuid. Used to
        resolve a host-reported deleted-stroke uuid (e.g. RV's ``clear-paint``
        / ``clear-all-paint`` internal events) back to the owning annotation
        clip without re-deriving the host's own frame/node context.

        :param stroke_uuid: The uuid of a ``PaintStart``, ``TextAnnotation``,
            or shape command to look up.
        :returns: The owning annotation clip's sync GUID, or ``None`` if not found.
        :rtype: str or None
        """
        for timeline in self._timelines.values():
            for track in timeline.tracks:
                if not track.name or "annotation" not in track.name.lower():
                    continue
                for item in track:
                    if not isinstance(item, otio.schema.Clip):
                        continue
                    for cmd in item.metadata.get("annotation_commands", []):
                        if _cmd_uuid(cmd) == stroke_uuid:
                            return item.metadata.get("sync", {}).get("guid")
        return None

    def surviving_annotation_commands(
        self, annotation_clip_guid: str, deleted_uuids: "set[str]"
    ) -> list:
        """Return *annotation_clip_guid*'s current commands minus *deleted_uuids*.

        Shared helper for both RV and xStudio delete-detection paths: given
        the set of stroke/text/shape uuids a host reported as locally
        deleted, compute the surviving command list to broadcast via
        :meth:`broadcast_replace_annotation_commands`.

        :param annotation_clip_guid: Sync GUID of the annotation clip to read.
        :param deleted_uuids: Set of command uuids that were deleted locally.
        :returns: The clip's remaining commands, in original order. Empty if
            every command was deleted, or if the clip is not found.
        :rtype: list
        """
        clip = self._object_map.get(annotation_clip_guid)
        if clip is None:
            return []
        return [
            cmd
            for cmd in clip.metadata.get("annotation_commands", [])
            if _cmd_uuid(cmd) not in deleted_uuids
        ]

    def broadcast_add_annotation(
        self,
        annotation_track_guid: str,
        clip_guid: str,
        clip_local_time: otio.opentime.RationalTime,
        events: list[dict[str, Any]],
    ) -> "str | None":
        """Build an annotation clip and insert it via the standard patch path.

        Called on pen-up/debounce to permanently commit the completed stroke
        or caption. Annotations are expressed as ``insert_child`` patches so
        that all peers apply them through the same code path as any other
        timeline mutation.

        The annotation track mirrors the structure produced by
        :meth:`ORIAnnotations.ReviewItem._export_otio_media`: each annotated
        frame is a 1-frame :class:`~opentimelineio.schema.Clip` and the gaps
        between annotated frames are :class:`~opentimelineio.schema.Gap` objects
        whose duration is ``frame − track_end`` frames.

        If an annotation already exists at this frame, the new commands are
        merged (appended) into the existing clip's metadata using a delta
        clip sent via :class:`InsertChild` rather than inserting a duplicate clip.

        Categorised **annotation** — multi-writer, never gated — which is why
        this is the one ``broadcast_*`` that keeps a domain return value instead
        of a ``SENT``/``SUPPRESSED`` status: callers need the annotation clip's
        GUID, and there is no authority outcome for them to observe.

        :param annotation_track_guid: GUID of the target Annotations track.
        :param clip_guid: GUID of the media clip being annotated.
        :param clip_local_time: 0-indexed time within the clip's source range.
        :param events: Serialised OTIO SyncEvent dicts (``PaintStart.1``,
            ``PaintPoints.1``) as produced by ``otio.adapters.write_to_string``.
        :returns: The sync GUID of the annotation clip that was created or
            merged into, or ``None`` if the operation could not be completed.
        :rtype: str or None
        """
        if not self.network or self.status != STATE_SYNCED:
            return
        if annotation_track_guid not in self._object_map:
            _log(f"broadcast_add_annotation: annotation track {annotation_track_guid} not found")
            return

        otio_events: list[otio.core.SerializableObject] = []
        for e in events:
            try:
                otio_events.append(_dict_to_otio(e) if isinstance(e, dict) else e)
            except Exception as exc:
                _log(f"broadcast_add_annotation: failed to deserialise event: {exc}")

        annotation_track = self._object_map[annotation_track_guid]
        frame = int(clip_local_time.value)
        fps = clip_local_time.rate

        existing = self._find_annotation_clip_at(annotation_track, clip_guid, frame)
        if existing is not None:
            # A clip already exists at this frame — merge the new commands in locally
            # and broadcast a delta clip so peers can apply the same merge.
            existing.metadata["annotation_commands"].extend(otio_events)
            delta_clip = self._make_annotation_clip(clip_guid, clip_local_time, otio_events)
            self._ensure_guid_and_map(delta_clip)
            self._send_message(InsertChild(
                parent_uuid=annotation_track_guid,
                index=-1,
                child_data=delta_clip,
                sync_timestamp=time.time(),
            ))
            return existing.metadata.get("sync", {}).get("guid")
        else:
            # New frame — insert a Gap to reach it (if needed) then the clip.
            track_end = self._annotation_track_end(annotation_track)
            if frame > track_end:
                gap = otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(track_end, fps),
                        duration=otio.opentime.RationalTime(frame - track_end, fps),
                    )
                )
                self.insert_child(annotation_track_guid, gap)
            ann_clip = self._make_annotation_clip(clip_guid, clip_local_time, otio_events)
            self.insert_child(annotation_track_guid, ann_clip)
            return ann_clip.metadata.get("sync", {}).get("guid")

    def broadcast_partial_annotation(
        self,
        clip_guid: str,
        frame: float,
        fps: float,
        events: list,
    ) -> str:
        """Broadcast a mid-stroke partial annotation to peers (visual only, no timeline persistence).

        Called periodically while the user is actively drawing a stroke, before pen-up.
        Peers render the transient stroke visually but do **not** write it to the OTIO
        timeline — that happens on pen-up via :meth:`broadcast_add_annotation`.

        :param clip_guid: Sync GUID of the media clip being annotated.
        :param frame: 0-indexed clip-local frame number.
        :param fps: Frame rate used to interpret *frame*.
        :param events: Serialised SyncEvent dicts (``PaintStart.1``, ``PaintPoints.1``).
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        self._send_message(PartialAnnotation(
            clip_guid=clip_guid,
            frame=frame,
            fps=fps,
            events=[_otio_to_dict(e) if not isinstance(e, dict) else e for e in events],
        ))
        return authority.SENT

    def broadcast_replace_annotation_commands(
        self,
        annotation_clip_guid: str,
        events: list,
    ) -> str:
        """Replace all annotation_commands on an existing clip and broadcast to peers.

        Used when the user edits or modifies an existing committed annotation in-place
        (e.g., editing text/captions or dragging/moving them) where the command
        list changes but the clip structure remains. Sends a
        ``REPLACE_ANNOTATION_COMMANDS`` message so peers replace the full
        command list rather than appending a delta.

        :param annotation_clip_guid: Sync GUID of the annotation clip to update.
        :param events: Full replacement list of SyncEvent objects (strokes +
            captions) representing the current annotation state.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        clip = self._object_map.get(annotation_clip_guid)
        if clip is None:
            _log(f"broadcast_replace_annotation_commands: clip {annotation_clip_guid} not found")
            return authority.SUPPRESSED

        otio_events: list[otio.core.SerializableObject] = []
        for e in events:
            try:
                otio_events.append(_dict_to_otio(e) if isinstance(e, dict) else e)
            except Exception as exc:
                _log(f"broadcast_replace_annotation_commands: failed to deserialise event: {exc}")

        clip.metadata["annotation_commands"] = otio_events

        self._send_message(ReplaceAnnotationCommands(
            annotation_clip_guid=annotation_clip_guid,
            commands=list(otio_events),
            sync_timestamp=time.time(),
        ))
        return authority.SENT

    def broadcast_move_child(
        self, parent_uuid: str, child_uuid: str, to_index: int
    ) -> str:
        """Move *child_uuid* to *to_index* within its parent and broadcast the change.

        Applies the reorder locally before broadcasting so the local OTIO model
        stays consistent regardless of network round-trip time.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to move.
        :param to_index: Target position in the parent's child list.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if self._is_syncing:
            _log("broadcast_move_child: skipped (_is_syncing)")
            return authority.SUPPRESSED
        if not self.network:
            _log("broadcast_move_child: skipped (no network)")
            return authority.SUPPRESSED
        if self.status != STATE_SYNCED:
            _log(f"broadcast_move_child: skipped (status={self.status})")
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_STRUCTURE):
            _log("broadcast_move_child: skipped (structure lease not held)")
            return authority.SUPPRESSED

        msg = self.patcher.move_child(parent_uuid, child_uuid, to_index)
        if msg:
            self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
            self._send_message(msg)
            return authority.SENT
        return authority.SUPPRESSED

    def broadcast_remove_child(self, parent_uuid: str, child_uuid: str) -> str:
        """Remove *child_uuid* from its parent and broadcast the change.

        The child is removed from both the parent container and ``_object_map``.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to remove.
        :returns: ``SENT``, or ``SUPPRESSED`` when there was nothing to send.
        :rtype: str
        """
        if self._is_syncing or not self.network or self.status != STATE_SYNCED:
            return authority.SUPPRESSED
        if not self._owns_channel(authority.CHANNEL_STRUCTURE):
            return authority.SUPPRESSED

        msg = self.patcher.remove_child(parent_uuid, child_uuid)
        if msg:
            _log(f"broadcast_remove_child: removed {child_uuid} from {parent_uuid}")
            self._refresh_lease_confirmed(authority.CHANNEL_STRUCTURE)
            self._send_message(msg)
            return authority.SENT
        return authority.SUPPRESSED

    # ------------------------------------------------------------------
    # Message Handling
    # ------------------------------------------------------------------

    def apply_patch(self, payload: dict[str, Any]) -> tuple[str, Any] | None:
        """Apply a single incoming message from the network.

        Dispatches on ``command_schema`` and ``event`` fields.  Returns an
        ``(action, data)`` tuple when the caller needs to act (e.g. to update RV
        state), or ``None`` when the message was fully handled internally.

        Messages from :attr:`self_guid` are silently discarded.  Messages arriving
        during ``STATE_JOINING`` are buffered (except session messages) and replayed
        by :meth:`apply_snapshot`.

        :param payload: Parsed message envelope received from the network.
        :returns: ``(action_name, action_data)`` or ``None``.
        """
        source = payload.get("source_guid", "unknown")

        if source == self.self_guid:
            return None

        inner_payload = payload.get("payload", {})
        command_schema = inner_payload.get("command_schema")
        command_block = inner_payload.get("command", {})

        event = command_block.get("event")
        data = command_block.get("payload", {})

        _log(f"apply_patch: command_schema={command_schema} event={event} source={source[:8]}")

        if (self.status == STATE_JOINING
                and command_schema != "LiveSession.1"
                and not self._replaying):
            self._delta_buffer.append(payload)
            return None

        handler = self._handlers.get((command_schema, event))
        msg_cls = message_for(command_schema, event)
        if handler is None or msg_cls is None:
            # Unknown (command_schema, event) — ignore safely and keep going.
            return None

        self._is_syncing = True
        # Session plumbing and lease bookkeeping cannot move anyone's view, so
        # they open no window — see NON_DISPLAY_EVENTS for what happens when
        # they do.
        track_provenance = (command_schema, event) not in NON_DISPLAY_EVENTS
        if track_provenance:
            self._remote_apply_stack.append({
                "source": source,
                "command_schema": command_schema,
                "event": event,
                "started_at": time.monotonic(),
            })
        try:
            msg = msg_cls.from_payload(data)
            return handler(msg, data, source)
        finally:
            if track_provenance:
                frame = self._remote_apply_stack.pop()
                frame["ended_at"] = time.monotonic()
                self._remote_apply_settled = frame
            self._is_syncing = False

    def remote_apply_context(self) -> "dict[str, Any] | None":
        """What remote message, if any, this moment is attributable to.

        Returns ``None`` when nothing a peer sent can account for what is
        happening now; otherwise a dict describing the apply::

            {"source": peer_guid, "command_schema": ..., "event": ...,
             "age": seconds_since_the_apply_started,
             "settling_for": seconds_since_it_returned or None,
             "in_apply": bool}

        Callers use this to tell *a peer caused this* from *the user did this*.
        Every other signal available at the point a display change is noticed —
        which container the media sits in, whether playback is running, whether
        the guid matches one recently applied — is a proxy for that question,
        and the bypass this exists to close is the case where every proxy
        answers "the user" and the true answer is "a peer" (see
        ``fix-visibility-authority-bypass`` design D2/D3).

        ``in_apply`` is False during the settle window, when the apply has
        returned and the host application's own machinery is still reacting to
        it.  That is where a display change is normally observed, so the window
        is the point of the method rather than a tolerance on it.

        Read from a different thread than the one applying (the host plugins
        poll).  The stack is only appended to and popped from, and the settled
        frame is replaced by whole-object assignment, so a reader sees either
        the old frame or the new one, never a half-written one.
        """
        now = time.monotonic()
        if self._remote_apply_stack:
            frame = self._remote_apply_stack[-1]
            return {
                "source": frame["source"],
                "command_schema": frame["command_schema"],
                "event": frame["event"],
                "age": now - frame["started_at"],
                "settling_for": None,
                "in_apply": True,
            }
        frame = self._remote_apply_settled
        if frame is None:
            return None
        settling_for = now - frame["ended_at"]
        if settling_for > self.remote_apply_settle_seconds:
            return None
        return {
            "source": frame["source"],
            "command_schema": frame["command_schema"],
            "event": frame["event"],
            "age": now - frame["started_at"],
            "settling_for": settling_for,
            "in_apply": False,
        }

    # ------------------------------------------------------------------
    # Receive-side handlers (registered in ``self._handlers``)
    # ------------------------------------------------------------------

    def _h_who_is_master(
        self, msg: WhoIsMaster, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        if self.is_master:
            self.broadcast_master_response()
        elif self.status == STATE_SYNCED:
            self._last_who_is_master_time = time.time()
        return None

    def _h_i_am_master(
        self, msg: IAmMaster, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        self.master_guid = msg.master_guid
        self._last_who_is_master_time = None
        if self.status == STATE_DISCOVERING:
            return ("master_found", self.master_guid)
        return None

    def _h_peer_announce(
        self, msg: PeerAnnounce, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        guid = msg.peer_guid or source
        known = self._peers.get(guid)
        entry = {"app": msg.app, "capabilities": list(msg.capabilities)}
        # Every announcement refreshes liveness, including a periodic heartbeat
        # that changes nothing — stamping only on change would let every peer
        # age out while announcing.  `last_seen` is deliberately excluded from
        # the change test below: a timestamp in the comparison would make each
        # heartbeat look like a change and flood the log.
        changed = known is None or any(known.get(k) != v for k, v in entry.items())
        self._peers[guid] = {**entry, "last_seen": time.time()}
        if changed:
            _log(
                f"PEER_ANNOUNCE: {guid[:8]} app={msg.app!r}"
                f" caps={entry['capabilities']} ({len(self._peers)} peers)"
            )
        # Deliberately no answer: see announce_peer.  Answering was how a
        # joiner used to learn quiet peers; the snapshot roster and the
        # heartbeat cover that now, without the per-join burst.
        # Already on the poll thread (apply_patch runs inside tick), so this is
        # a direct election rather than an enqueue.
        self.elect_host()
        return None

    def _h_peer_depart(
        self, msg: PeerDepart, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        # drop_peer already pops the entry and re-elects; doing either here
        # would be a second election implementation, which is what drop_peer
        # was written to prevent.  Same poll-thread position as
        # _h_peer_announce, so the direct call is right.
        self.drop_peer(msg.peer_guid or source)
        return None

    def _h_state_request(
        self, msg: StateRequest, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        if not self.is_master:
            return None
        requester = msg.requester_guid or source
        return ("state_request_received", requester)

    def _h_state_snapshot(
        self, msg: StateSnapshot, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        if msg.target_guid == self.self_guid:
            return ("state_snapshot_received", data)
        return None

    def _h_playback_set(
        self, msg: PlaybackSettingsSet, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        self.playback_state = data
        # Track the active clip from the unified view-state so passive peers
        # (e.g. the sync viewer) can highlight it even while paused.  This used
        # to live in the retired SELECTION_1.0 handler.
        self.selected_clip_guid = msg.clip_guid or None
        # Sync active_timeline_guid so passive peers (e.g. the sync viewer)
        # automatically follow the master when it switches between sequences.
        # Skip clip-level timelines: those are single-clip artefacts that live
        # alongside the sequence timeline and should not shadow the sequence
        # view on passive peers.
        tl_guid = msg.timeline_guid
        if (tl_guid
                and tl_guid in self._timelines
                and tl_guid not in self._clip_timelines.values()):
            self.active_timeline_guid = tl_guid
        for cb in self._playback_callbacks:
            try:
                cb(data)
            except Exception as e:
                _log(f"on_playback_changed callback error: {e}")
        return ("playback_settings", data)

    def _h_display_set(
        self, msg: DisplaySettingsSet, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        self.display_state = data
        tl = self.root_timeline
        if tl is not None:
            tl.metadata["display_settings"] = {
                k: v for k, v in data.items() if k != "sync_timestamp"
            }
        for cb in self._display_callbacks:
            try:
                cb(data)
            except Exception as e:
                _log(f"on_display_changed callback error: {e}")
        return ("display_settings", data)

    def _h_add_timeline(
        self, msg: AddTimeline, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        tl_guid = msg.timeline_guid
        # Check the GUID guard *before* deserializing: a timeline we already
        # hold must not pay the as_otio() cost.
        if tl_guid and msg.timeline and tl_guid not in self._timelines:
            tl = msg.as_otio()
            self._timelines[tl_guid] = tl
            # A peer announced this subtree, so it is session-visible here too
            # and this peer may address it without having published it itself.
            self._note_session_guids(tl)
            seq_clip_guid = tl.metadata.get("clip_timeline_for")
            if seq_clip_guid:
                # Single-clip annotation timeline — preserve canonical
                # sequence clip in object_map.
                self._traverse_and_map_preserve(tl)
                self._clip_timelines[seq_clip_guid] = tl_guid
                _log(
                    f"ADD_TIMELINE: registered clip_tl={tl_guid[:8]} "
                    f"for seq_clip={str(seq_clip_guid)[:8]}"
                )
            else:
                # Full sequence / playlist timeline — traverse normally and
                # notify the host application so it can create the corresponding
                # viewer containers.
                self._traverse_and_map(tl)
                _log(
                    f"ADD_TIMELINE: new sequence timeline={tl_guid[:8]}"
                    f" name={tl.name!r}"
                )
                return ("add_timeline", tl)
        return None

    def _h_rename_timeline(
        self, msg: RenameTimeline, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        tl_guid = msg.timeline_guid
        new_name = msg.name
        tl = self._timelines.get(tl_guid)
        if tl is not None and new_name:
            tl.name = new_name
            _log(f"RENAME_TIMELINE: {tl_guid[:8]} → {new_name!r}")
        return ("timeline_renamed", data)

    def _h_remove_timeline(
        self, msg: RemoveTimeline, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        tl = self._remove_timeline_local(msg.timeline_guid)
        if tl is None:
            # Unknown / already-removed GUID — idempotent no-op.
            return None
        return ("remove_timeline", tl)

    def _h_replace_timeline(
        self, msg: ReplaceTimeline, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        tl_guid = msg.timeline_guid
        if not tl_guid or not msg.timeline:
            return None
        tl = msg.as_otio()
        self._replace_timeline_local(tl_guid, tl)
        _log(
            f"REPLACE_TIMELINE: {tl_guid[:8]} name={tl.name!r} "
            f"(wholesale structure replace)"
        )
        return ("replace_timeline", tl)

    def _h_partial_annotation(
        self, msg: PartialAnnotation, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        return ("partial_annotation", data)

    def _h_otio_session(
        self, msg: ProtocolMessage, data: dict[str, Any], source: str
    ) -> "tuple[str, Any] | None":
        result = self.patcher.apply_patch(msg)
        if result and result[0] == "insert_child":
            # A peer published this child, so addressing it later is legitimate
            # even though this peer never announced it.
            if isinstance(result[1], otio.core.SerializableObject):
                self._note_session_guids(result[1])
        return result

    def tick(self) -> list[tuple[str, Any]]:
        """Poll the network and auto-advance the session handshake.

        This is the recommended entry point for new client integrations.
        It wraps :meth:`receive_and_apply_all` and handles the session
        state machine automatically:

        * ``master_found``          → calls :meth:`request_state` internally.
        * ``state_snapshot_received`` → calls :meth:`apply_snapshot` internally.
        * ``state_request_received`` → **returned to caller**; the master must
          respond by calling :meth:`send_state_snapshot`.

        Application-level events (``playback_settings``,
        ``annotation_*``, ``insert_child``, …) are returned so the caller can
        react to them.  Playback updates are also delivered through the
        :meth:`on_playback_changed` callback if one is registered.

        Compare with :meth:`receive_and_apply_all`, which returns every raw
        action tuple and leaves the handshake entirely to the caller.

        :returns: List of ``(action, data)`` tuples requiring application
            action (subset of what :meth:`receive_and_apply_all` would return).
        """
        self._drain_host_elections()
        app_events: list[tuple[str, Any]] = []
        for action, data in self.receive_and_apply_all():
            if action == "master_found":
                self.request_state()
            elif action == "state_snapshot_received":
                # Replay results (buffered deltas newer than the snapshot) are
                # forwarded so callers react to them just like live events.
                replay = self.apply_snapshot(data)
                if "playback_state" in data:
                    self.playback_state = data["playback_state"]
                    # Mirror _h_playback_set: some callers (e.g. the xStudio
                    # plugin) only ever apply playback state via the
                    # on_playback_changed callback list, not the returned
                    # action tuple — so this must fire both paths.
                    for cb in self._playback_callbacks:
                        try:
                            cb(self.playback_state)
                        except Exception as e:
                            _log(f"on_playback_changed callback error: {e}")
                    app_events.append(("playback_settings", self.playback_state))
                if "display_state" in data:
                    self.display_state = data["display_state"]
                    app_events.append(("display_settings", self.display_state))
                app_events.extend(replay)
            else:
                app_events.append((action, data))

        # Check for master failover
        if (not self.is_master 
                and self.status == STATE_SYNCED 
                and getattr(self, "_last_who_is_master_time", None) is not None):
            if time.time() - self._last_who_is_master_time > 2.0:
                _log("Master did not respond to WHO_IS_MASTER. Promoting self to master.")
                # Already STATE_SYNCED here, so the status transition inside
                # elect_self_as_master is a no-op and on_synced does not re-fire.
                self._last_who_is_master_time = None
                self.elect_self_as_master()

        # Liveness: announce our own presence, and forget peers that have gone
        # quiet.  Gated on having a session at all rather than on being SYNCED:
        # start_session announces once, and a peer retrying discovery after a
        # state-request timeout would otherwise stay silent for the whole retry
        # and be aged out by everyone else while alive and actively joining.
        if self.status != STATE_NONE:
            self._heartbeat()
            self._age_out_peers()

        # Check for state snapshot timeout
        if (self.status == STATE_JOINING
                and getattr(self, "_state_request_time", None) is not None):
            if time.time() - self._state_request_time > 5.0:
                _log("STATE_REQUEST timed out. Reverting to DISCOVERING.")
                self.master_guid = None
                self._state_request_time = None
                self._set_status(STATE_DISCOVERING)
                app_events.append(("state_request_timeout", None))

        return app_events

    def _heartbeat(self) -> None:
        """Re-announce this peer if the heartbeat interval has elapsed.

        Poll-thread only, called from :meth:`tick`.  The announcement asks for
        no answer: the storm the ``PeerAnnounce`` docstring warns about is the
        answer cascade, and a re-announcement nobody answers costs one message
        per peer per interval.

        This is what gives :meth:`_age_out_peers` something to measure.  A peer
        may legitimately sit idle for the whole session — a viewer watching a
        screening touches nothing — so its *other* traffic cannot stand in for
        liveness.
        """
        if time.time() - self._last_announce_time < PEER_HEARTBEAT_INTERVAL:
            return
        self.announce_peer()

    def _age_out_peers(self) -> None:
        """Drop peers not heard from within :data:`PEER_LIVENESS_TIMEOUT`.

        The backstop for departures nobody announced — a crash, a killed
        process, a lost network — where no ``PEER_DEPART`` will ever arrive.
        Poll-thread only, called from :meth:`tick`.

        Removal goes through :meth:`drop_peer`, which re-elects the host, so a
        departed host does not keep visibility authority.  This peer's own entry
        is never aged out: it is the one peer whose presence is not in question,
        and dropping it would leave a solo session with no host.
        """
        now = time.time()
        stale = [
            guid
            for guid, peer in self._peers.items()
            if guid != self.self_guid
            and now - peer.get("last_seen", now) > PEER_LIVENESS_TIMEOUT
        ]
        for guid in stale:
            _log(
                f"peer {guid[:8]} not heard from in"
                f" {PEER_LIVENESS_TIMEOUT:.0f}s — presumed gone"
            )
            self.drop_peer(guid)

    def _drain_host_elections(self) -> None:
        """Run any host election requested from another thread.

        Eligibility is evaluated here, at drain time: :meth:`elect_host` reads
        the peer table as it stands *now*, so a request enqueued before a
        preferred peer announced still produces the right host.  A batch of
        queued requests collapses into one election, since the result depends
        only on the table and not on how many times it was asked for.
        """
        requested = False
        reasons: list[str] = []
        while True:
            try:
                reasons.append(self._host_election_queue.get_nowait())
            except queue.Empty:
                break
            requested = True
        if requested:
            _log(f"host election requested ({', '.join(reasons)})")
            self.elect_host()

    # Latest-wins message schemas mapped to a per-schema coalesce threshold:
    # within one drained batch, superseded messages of that schema are dropped
    # only once the batch holds MORE than ``threshold`` of them.  This collapses
    # a genuine backlog (fast producer outrunning the consumer) without throwing
    # away the intermediate frames of a normal-rate stream — dropping those makes
    # scrubbing visibly choppy.  The unified view-state (PLAYBACK_SETTINGS_1.0)
    # keeps every position until a real backlog forms.
    _COALESCE_THRESHOLDS = {
        "PLAYBACK_SETTINGS_1.0": 12,
    }

    def _coalesce_payloads(
        self, payloads: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop superseded latest-wins messages once a backlog forms.

        For each coalescible schema whose count in this batch exceeds its
        threshold, keep only its last occurrence (in place); everything else —
        sub-threshold streams, structural mutations, annotations — is preserved
        in original order.
        """
        if len(payloads) <= 1:
            return payloads
        counts: dict[str, int] = {}
        last_idx: dict[str, int] = {}
        for i, p in enumerate(payloads):
            schema = p.get("payload", {}).get("command_schema")
            if schema in self._COALESCE_THRESHOLDS:
                counts[schema] = counts.get(schema, 0) + 1
                last_idx[schema] = i
        # Only collapse schemas that have actually backed up past their threshold.
        collapse = {
            s for s, n in counts.items() if n > self._COALESCE_THRESHOLDS[s]
        }
        if not collapse:
            return payloads
        out: list[dict[str, Any]] = []
        dropped = 0
        for i, p in enumerate(payloads):
            schema = p.get("payload", {}).get("command_schema")
            if schema in collapse and last_idx[schema] != i:
                dropped += 1
                continue
            out.append(p)
        if dropped:
            _log(f"coalesce: dropped {dropped} backlogged message(s) {sorted(collapse)}")
        return out

    def receive_and_apply_all(self) -> list[tuple[str, Any]]:
        """Drain the network and apply every pending message.

        :returns: List of ``(action, data)`` tuples for messages that require a
            response from the caller (e.g. to update RV state).  Empty when all
            messages were handled internally or no messages were waiting.
        """
        if not self.network:
            return []
        results: list[tuple[str, Any]] = []
        for p in self._coalesce_payloads(self.network.receive_payloads()):
            res = self.apply_patch(p)
            if res:
                results.append(res)
        return results

    @staticmethod
    def _payload_sync_timestamp(payload: dict[str, Any]) -> float:
        """Return the ``sync_timestamp`` an envelope carries, or ``0``.

        The field lives in ``payload.command.payload``, alongside the message's
        own fields — not in ``payload`` itself, where the replay comparison used
        to look for it. Reading it one level too high returned the default
        ``0`` for every message, so ``p_time > snapshot_timestamp`` was
        universally false and **every** buffered delta was discarded: a peer
        joining mid-edit silently lost every change made while it was joining.

        Latent rather than observed — the delta buffer only fills when messages
        arrive during the ``STATE_JOINING`` window — which is why this is fixed
        and tested on its own rather than folded into a fix for something else.

        :param payload: A received message envelope.
        :returns: Epoch seconds, or ``0`` when the message carries none.
        :rtype: float
        """
        command = payload.get("payload", {}).get("command", {})
        return command.get("payload", {}).get("sync_timestamp", 0) or 0

    def apply_snapshot(self, snapshot_data: dict[str, Any]) -> list[tuple[str, Any]]:
        """Replace local state with a full snapshot and replay buffered deltas.

        Clears ``_object_map`` and ``_timelines``, deserialises the timelines from
        *snapshot_data*, then replays any buffered messages whose ``sync_timestamp``
        is newer than the snapshot.  Transitions :attr:`status` to ``STATE_SYNCED``.

        :param snapshot_data: ``payload`` dict from a ``STATE_SNAPSHOT`` message.
        :returns: List of ``(action, data)`` tuples produced by replaying buffered
            deltas; to be handled by the caller in the same way as the return value
            of :meth:`receive_and_apply_all`.
        """
        timestamp: float = snapshot_data.get("snapshot_timestamp", 0)

        self._is_syncing = True
        try:
            self._timelines = {}
            self._object_map = {}
            self._clip_timelines = {}

            # Sort so sequence timelines are processed before clip timelines.
            # This guarantees the sequence clip is canonical in _object_map
            # before the clip-timeline copy is registered via setdefault.
            tl_items = sorted(
                snapshot_data.get("timelines", {}).items(),
                key=lambda kv: bool(kv[1].get("metadata", {}).get("clip_timeline_for")),
            )
            for guid, tl_dict in tl_items:
                tl = _dict_to_otio(tl_dict)
                self._timelines[guid] = tl
                self._note_session_guids(tl)
                is_clip_tl = bool(tl.metadata.get("clip_timeline_for"))
                if is_clip_tl:
                    self._traverse_and_map_preserve(tl)
                    seq_clip_guid = tl.metadata["clip_timeline_for"]
                    self._clip_timelines[seq_clip_guid] = guid
                else:
                    self._traverse_and_map(tl)
            self.active_timeline_guid = snapshot_data.get("active_timeline_guid")
            # Adopt the peer set first, then the host: election reads the
            # table, so adopting the host against a table that does not yet
            # contain it would be a needless disagreement.
            self.adopt_peers(snapshot_data.get("peers"))
            # Adopt the session's host before any local election runs, so a
            # joiner does not assume the role and fight the incumbent.
            self.adopt_host(snapshot_data.get("host_guid"))
            # Learn who holds each write lease so this joiner's first scrub
            # or structural edit doesn't fight the peer already driving.
            self.adopt_ownership(snapshot_data.get("broadcast_ownership"))
            if "playback_state" in snapshot_data:
                self.playback_state = snapshot_data["playback_state"]

            # Restore display_state: prefer the explicit snapshot field; fall back
            # to timeline custom_metadata written by a previous session to disk.
            if "display_state" in snapshot_data:
                self.display_state = snapshot_data["display_state"]
            else:
                for tl in self._timelines.values():
                    ds = tl.metadata.get("display_settings")
                    if ds:
                        self.display_state = dict(ds)
                        break

            # Replay with buffering disabled. The status is still
            # STATE_JOINING here — it transitions below, after the replay, so
            # that the on_synced callbacks observe the deltas already applied —
            # and apply_patch buffers every non-session message while joining.
            # Without this flag each replayed message is appended back onto the
            # very list being iterated, which never terminates. That was
            # unreachable while the timestamp comparison below always failed;
            # it becomes reachable the moment the comparison works, so the two
            # belong together.
            replay_results: list[tuple[str, Any]] = []
            self._replaying = True
            try:
                for payload in self._delta_buffer:
                    p_time: float = self._payload_sync_timestamp(payload)
                    if p_time > timestamp:
                        res = self.apply_patch(payload)
                        if res:
                            replay_results.append(res)
            finally:
                self._replaying = False

            self._delta_buffer = []
            self._state_request_time = None
            self._set_status(STATE_SYNCED)
            return replay_results
        finally:
            self._is_syncing = False

    def close(self) -> None:
        """Announce departure, stop the network backend, release resources.

        The departure is emitted here rather than from each host application's
        disconnect path.  Both already call this, so both get the behaviour, and
        a protocol message hand-replicated across two separately-written paths
        would drift the way discovery cadence and snapshot assembly already
        have — silently, with one host announcing its exit and the other not.

        Best-effort by design: the message is enqueued and the backend is given
        a bounded chance to flush it, but a lost departure simply means peers
        fall back to aging this peer out, which is the case a crash takes
        anyway.
        """
        if not self.network:
            return
        try:
            self._send_message(PeerDepart(peer_guid=self.self_guid))
        except Exception as e:
            # Never let a courtesy message block teardown.
            _log(f"PEER_DEPART send failed (peers will age us out): {e}")
        self.network.stop()
