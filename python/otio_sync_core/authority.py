"""Broadcast authority: categories, enforcement status, and host election.

Sync traffic is split into categories with distinct authority, so that
*controlling what the session looks at* is a separate permission from *moving
within it*:

===============  ==================================================  ==========
Category         Contents                                            Authority
===============  ==================================================  ==========
``visibility``   which clip/sequence is shown, and in which mode      host only
``position``     playhead position, play/stop, playback mode,         any peer
                 display state (pan/zoom/exposure/channel)
``annotation``   strokes, captions, shapes                            any peer
``structure``    timeline add/remove/replace/rename, child edits      any peer
===============  ==================================================  ==========

``structure`` is listed for completeness and is **not** gated here — it stays
governed by ``is_master`` exactly as it is today; folding it in belongs to
``session-roles``.  ``display_state`` sits under ``position`` deliberately:
reviewers legitimately toggle channels and exposure locally, so it is per-peer
(host-owned-visibility §7.1).

Visibility and position travel as *field groups* inside one
``PLAYBACK_SETTINGS_1.0`` message, so enforcement applies to the fields rather
than to the message type.  A non-host peer may broadcast a playback message
carrying position; the visibility fields are stripped from it, in one place
(:func:`strip_visibility_fields`, called from ``SyncManager.broadcast_*``) so
that no call site can omit ``view_mode`` yet still leak a ``clip_guid``.

Host election is a pure function of the peer table (:func:`elect_host_guid`), so
every peer reaches the same host from the same inputs.  It prefers a peer whose
application is ranked as the preferred visibility authority, falls back to any
peer advertising the capability, and breaks ties by GUID.  The application name
is a *ranking* input, never a hard requirement — an OpenRV-only session still
elects a host.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

#: Which clip or sequence is on screen, and in which view mode. Host only.
VISIBILITY = "visibility"
#: Playhead position, play/stop, playback mode, display state. Any peer.
POSITION = "position"
#: Strokes, captions, shapes. Any peer, never gated.
ANNOTATION = "annotation"
#: Timeline and child mutations. Any peer here; still gated by ``is_master``.
STRUCTURE = "structure"

# ---------------------------------------------------------------------------
# Broadcast status
# ---------------------------------------------------------------------------

#: The broadcast went out as given.
SENT = "SENT"
#: The broadcast was withheld, or had fields stripped, by the authority check.
SUPPRESSED = "SUPPRESSED"

# ---------------------------------------------------------------------------
# The category table
# ---------------------------------------------------------------------------

#: ``SyncManager`` broadcast method name → category.
#:
#: ``broadcast_playback_state`` is the mixed one: the message is categorised
#: ``position`` (any peer may send it) but the ``view_mode``/``clip_guid`` field
#: group inside it is ``visibility`` and is stripped for a non-host sender.  The
#: session-plumbing broadcasts (``broadcast_master_discovery`` /
#: ``broadcast_master_response``) are deliberately absent: they carry no user
#: intent and are never gated.
BROADCAST_CATEGORIES: dict[str, str] = {
    # position
    "broadcast_playback_state": POSITION,
    "broadcast_display_state": POSITION,
    # annotation
    "broadcast_add_annotation": ANNOTATION,
    "broadcast_partial_annotation": ANNOTATION,
    "broadcast_replace_annotation_commands": ANNOTATION,
    # structure
    "broadcast_add_timeline": STRUCTURE,
    "broadcast_clip_timeline": STRUCTURE,
    "broadcast_timeline_rename": STRUCTURE,
    "broadcast_remove_timeline": STRUCTURE,
    "broadcast_replace_timeline": STRUCTURE,
    "broadcast_move_child": STRUCTURE,
    "broadcast_remove_child": STRUCTURE,
}

#: Fields of ``PLAYBACK_SETTINGS_1.0`` that assert **visibility**.
VISIBILITY_FIELDS: tuple[str, ...] = ("view_mode", "clip_guid")

#: Fields of ``PLAYBACK_SETTINGS_1.0`` that assert **position**.
POSITION_FIELDS: tuple[str, ...] = ("current_time", "playing", "playback_mode")


def category_for(method_name: str) -> "str | None":
    """Return the authority category of a ``broadcast_*`` method.

    :param method_name: Name of the :class:`~otio_sync_core.manager.SyncManager`
        broadcast method (e.g. ``"broadcast_playback_state"``).
    :returns: One of :data:`VISIBILITY` / :data:`POSITION` / :data:`ANNOTATION` /
        :data:`STRUCTURE`, or ``None`` for an ungated broadcast.
    :rtype: str or None
    """
    return BROADCAST_CATEGORIES.get(method_name)


def strip_visibility_fields(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return *state* with every visibility field removed.

    The single place visibility is stripped from an outgoing message.  Removing
    the whole :data:`VISIBILITY_FIELDS` group together is the point: a follower
    that dropped ``view_mode`` but kept ``clip_guid`` would still be asserting
    what the session should look at.

    :param state: Outgoing playback/view state dict.
    :returns: A new dict carrying only the non-visibility fields.
    :rtype: dict
    """
    return {k: v for k, v in state.items() if k not in VISIBILITY_FIELDS}


def asserts_visibility(state: Mapping[str, Any]) -> bool:
    """Return whether *state* carries any visibility field.

    Used by tests to assert on the wire rather than on intent.

    :param state: A playback/view state dict (outgoing or as received).
    :rtype: bool
    """
    return any(state.get(f) is not None for f in VISIBILITY_FIELDS)


# ---------------------------------------------------------------------------
# Enforcement kill switch
# ---------------------------------------------------------------------------

#: Environment variable that disables authority enforcement at runtime.
ENFORCEMENT_ENV = "ORI_VISIBILITY_AUTHORITY"

_FALSEY = {"0", "false", "no", "off"}


def enforcement_enabled() -> bool:
    """Return whether category authority is enforced on outgoing broadcasts.

    Enabled by default.  Setting ``ORI_VISIBILITY_AUTHORITY=0`` reverts to
    symmetric authority — every peer may broadcast visibility — so a wrong
    category split can be backed out of a live session without a rebuild
    (mirrors ``session-roles`` D5).  Read per call rather than cached at import,
    so the switch can be flipped in a running interpreter.

    :rtype: bool
    """
    return os.environ.get(ENFORCEMENT_ENV, "1").strip().lower() not in _FALSEY


# ---------------------------------------------------------------------------
# Host election
# ---------------------------------------------------------------------------

#: Capability a peer must advertise to be eligible for the host role.
CAPABILITY_VISIBILITY = "visibility"

#: Applications ranked as preferred visibility authorities, best first.
#:
#: This is a *preference*, never a requirement: an application not listed here
#: still hosts when it is the only capable peer, which is what keeps an
#: OpenRV-only session from being hostless.
HOST_PREFERENCE: tuple[str, ...] = ("xstudio", "openrv")

#: Rank given to a capable peer whose application is not in HOST_PREFERENCE.
UNRANKED = len(HOST_PREFERENCE)


def host_rank(app: "str | None") -> int:
    """Return the host-preference rank of an application name (lower is better).

    :param app: Application name as advertised by the peer, case-insensitive.
    :returns: Index into :data:`HOST_PREFERENCE`, or :data:`UNRANKED`.
    :rtype: int
    """
    name = (app or "").strip().lower()
    try:
        return HOST_PREFERENCE.index(name)
    except ValueError:
        return UNRANKED


def is_host_capable(peer: Mapping[str, Any]) -> bool:
    """Return whether a peer-table entry advertises visibility authority.

    :param peer: Peer entry, i.e. ``{"app": str, "capabilities": [...]}``.
    :rtype: bool
    """
    caps: Iterable[str] = peer.get("capabilities") or ()
    return CAPABILITY_VISIBILITY in caps


def elect_host_guid(peers: Mapping[str, Mapping[str, Any]]) -> "str | None":
    """Return the GUID of the peer that should hold visibility authority.

    A pure function of the peer table, so two peers evaluating the same set of
    peers always reach the same host — the property that makes simultaneous
    election safe without a claim protocol.  Ordering is: preferred application
    first, then GUID ascending as a deterministic tie-break (the same rule
    ``session-roles`` D2 uses for claims).

    :param peers: ``{guid: {"app": str, "capabilities": [...]}}``, including
        this peer's own entry.
    :returns: Elected host GUID, or ``None`` when no peer is capable.
    :rtype: str or None
    """
    candidates = [
        (host_rank(p.get("app")), guid)
        for guid, p in peers.items()
        if is_host_capable(p)
    ]
    if not candidates:
        return None
    return min(candidates)[1]
