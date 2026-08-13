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

``position`` and ``structure`` are additionally gated by a write **lease**
(``broadcast-ownership``): any peer may broadcast them, but only whichever
peer currently holds that category's lease.  This subsumes the ``is_master``
gate structure used to sit behind.  ``display_state`` sits under ``position``
for *authority* purposes — reviewers legitimately toggle channels and
exposure locally, so it is per-peer (host-owned-visibility §7.1) — but takes
its own lease *channel*, so adjusting exposure never blocks another peer's
scrub (design.md D8).  See :data:`LEASE_CHANNELS` below.

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


def elect_host_guid(
    peers: Mapping[str, Mapping[str, Any]], default_role: "str | None" = None
) -> "str | None":
    """Return the GUID of the peer that should hold visibility authority.

    A pure function of the peer table, so two peers evaluating the same set of
    peers always reach the same host — the property that makes simultaneous
    election safe without a claim protocol.  Ordering is: preferred application
    first, then GUID ascending as a deterministic tie-break (the same rule
    ``session-roles`` D2 uses for claims).

    Candidates are filtered by **role as well as capability** (``session-roles``
    D4): a peer whose role forbids emitting visibility would hold the authority
    while unable to exercise it.  A peer carrying no role resolves to
    *default_role*, so a session that never opted into a role policy elects
    exactly as it did before roles existed.

    :param peers: ``{guid: {"app": str, "capabilities": [...], "role": str}}``,
        including this peer's own entry.
    :param default_role: The session's declared default role, applied to any
        entry carrying none.  ``None`` means :data:`DEFAULT_ROLE`.  Passed in
        rather than read from manager state so this stays a pure function.
    :returns: Elected host GUID, or ``None`` when no peer is eligible.
    :rtype: str or None
    """
    candidates = host_candidates(peers, default_role)
    if not candidates:
        return None
    return min(candidates)[1]


# ---------------------------------------------------------------------------
# Broadcast ownership (write leases) — position, display, structure
# ---------------------------------------------------------------------------

#: Playhead position, play/stop, playback mode — a field group of
#: ``PLAYBACK_SETTINGS_1.0``, gated alongside (but independently of) visibility.
CHANNEL_POSITION = "position"
#: Pan/zoom/exposure/channel — its own message (``DISPLAY_SETTINGS_1.0``) and its
#: own lease channel, deliberately not shared with ``CHANNEL_POSITION`` (D8):
#: adjusting exposure must never block someone else's scrub, or vice versa.
CHANNEL_DISPLAY = "display"
#: Timeline add/remove/replace/rename and structural child mutations.
CHANNEL_STRUCTURE = "structure"

#: Every channel that takes a write lease. ``visibility`` and ``annotation``
#: are deliberately absent: visibility is a static single writer (no
#: contention to resolve) and annotation stays multi-writer by design.
LEASE_CHANNELS: tuple[str, ...] = (CHANNEL_POSITION, CHANNEL_DISPLAY, CHANNEL_STRUCTURE)

#: Seconds of broadcast silence after which an unrefreshed lease expires.
#: Working defaults within the agreed 500ms-2s envelope (design.md D3/D8):
#: display releases fastest (a stopped gesture has no clip-boundary gap to
#: ride out), structure slowest (a remote rebuild can gap for hundreds of ms).
LEASE_DURATIONS: dict[str, float] = {
    CHANNEL_DISPLAY: 0.5,
    CHANNEL_POSITION: 1.0,
    CHANNEL_STRUCTURE: 2.0,
}

#: Fields of ``PLAYBACK_SETTINGS_1.0`` that assert **position** — the
#: counterpart to :data:`VISIBILITY_FIELDS`, gated by the position lease
#: rather than by host status.
POSITION_FIELDS: tuple[str, ...] = ("current_time", "playing", "playback_mode")


def strip_position_fields(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return *state* with every position field removed.

    Mirrors :func:`strip_visibility_fields`: the position field group is
    removed together so a message cannot lose ``playing`` while still
    asserting ``current_time``.

    :param state: Outgoing playback/view state dict.
    :returns: A new dict carrying only the non-position fields.
    :rtype: dict
    """
    return {k: v for k, v in state.items() if k not in POSITION_FIELDS}


def asserts_position(state: Mapping[str, Any]) -> bool:
    """Return whether *state* carries any position field.

    :param state: A playback/view state dict (outgoing or as received).
    :rtype: bool
    """
    return any(state.get(f) is not None for f in POSITION_FIELDS)


def resolve_claim(
    current: "tuple[float, str] | None", incoming: "tuple[float, str]"
) -> "tuple[float, str]":
    """Return the winning ``(claim_ts, peer_guid)`` pair between two claims.

    Pure function of its two arguments, so every peer evaluating the same pair
    of claims reaches the same winner without synchronized clocks (design.md
    D2): tuple comparison is lexicographic, so the earlier ``claim_ts`` wins
    and an exact tie breaks to the lower ``peer_guid`` — the same
    ``min()``-over-a-deterministic-key shape as :func:`elect_host_guid`.

    :param current: The best claim known so far, or ``None`` when there isn't one.
    :param incoming: The claim being weighed against it.
    :returns: Whichever of *current* / *incoming* sorts first; *incoming* when
        *current* is ``None``.
    :rtype: tuple
    """
    if current is None:
        return incoming
    return min(current, incoming)


#: Environment variable that disables ownership-lease enforcement at runtime.
OWNERSHIP_ENFORCEMENT_ENV = "ORI_BROADCAST_OWNERSHIP"


def ownership_enforcement_enabled() -> bool:
    """Return whether position/structure/display broadcasts are lease-gated.

    **Enabled by default** as of migration step 1b (design.md): step 1a
    shipped the mechanism dark (default disabled) behind this same switch;
    now that both host plugins wire ``claim_category()`` into their
    input-driven paths, the default flips to on. Setting
    ``ORI_BROADCAST_OWNERSHIP=0`` reverts to the pre-lease behaviour —
    unconditional broadcast for position/display/structure — so the whole
    mechanism can still be backed out of a live session without a rebuild if
    the 1b soak finds a problem. Read per call rather than cached at import,
    exactly like :func:`enforcement_enabled` — a disabled switch must revert
    enforcement completely, not leave a claim/expiry state machine running
    against a policy no longer in force.

    :rtype: bool
    """
    return os.environ.get(OWNERSHIP_ENFORCEMENT_ENV, "1").strip().lower() not in _FALSEY


# ---------------------------------------------------------------------------
# Session roles (session-roles) — what a *participant* may ever emit
# ---------------------------------------------------------------------------
#
# Role is the fourth authority axis, and it is not any of the other three:
#
#   master      who holds the canonical snapshot          (liveness/discovery)
#   host        who chooses what everyone looks at        (elect_host_guid)
#   lease owner who is emitting this category right now   (LEASE_CHANNELS)
#   role        what this participant may emit at all     (here)
#
# Role is a **ceiling**, category authority is a **gate**, and a broadcast must
# pass both.  A driver has permission to emit visibility; only the host actually
# does.  The role check runs *first* — not only because it is the cheaper of the
# two, but because a lease is confirmed as a side effect of a broadcast going
# out (``_refresh_lease_confirmed``): evaluating the gate first would let a
# role-blocked peer harden a lease it is not permitted to use.

#: Full control: every field group, subject to category authority.  Only a
#: driver is eligible for host.
DRIVER = "driver"
#: May annotate and move within what is shown; may not change *what* is shown,
#: modify session content, or clear other participants' annotations.
REVIEWER = "reviewer"
#: Passive observer: receives everything, emits nothing session-visible.
VIEWER = "viewer"

#: Every role, most-permitted first.
ROLES: tuple[str, ...] = (DRIVER, REVIEWER, VIEWER)

#: The role a session assigns to a participant it does not recognise, when it
#: declares no policy of its own.  Deliberately the *permissive* one: it
#: reproduces pre-roles behaviour exactly, so the mechanism is inert until a
#: session opts in — and it is also the rollback, needing no rebuild.  A defect
#: in role evaluation therefore fails towards "behaves like today" rather than
#: towards "nobody may drive and nothing says why".
DEFAULT_ROLE = DRIVER

#: Display state as a *role* group.  Deliberately distinct from the ``position``
#: category it sits under for lease purposes: every role may emit display state
#: (it is per-peer presentation, not a session event — host-owned-visibility
#: §7.1), while ``position`` stops at reviewer.  Same string as
#: :data:`CHANNEL_DISPLAY` because it is the same channel; a separate name
#: because the two tables answer different questions.
DISPLAY = CHANNEL_DISPLAY

#: Role → the field groups that role may emit.
#:
#: Keyed on the :data:`BROADCAST_CATEGORIES` vocabulary plus :data:`DISPLAY`.
#: The rows are field *groups*, not message types: the visibility boundary runs
#: inside ``PLAYBACK_SETTINGS_1.0``, so a table keyed on message type could not
#: express "a reviewer may scrub but not change the shot" — which is the whole
#: of the reviewer tier.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    DRIVER: frozenset({VISIBILITY, POSITION, DISPLAY, ANNOTATION, STRUCTURE}),
    REVIEWER: frozenset({POSITION, DISPLAY, ANNOTATION}),
    VIEWER: frozenset({DISPLAY}),
}

#: ``SyncManager`` broadcast method name → role permission group.
#:
#: Identical to :data:`BROADCAST_CATEGORIES` but for display state, which is
#: categorised ``position`` for lease purposes and is its own group here (see
#: :data:`DISPLAY`).  Kept as a derived table rather than a second hand-written
#: one so a new broadcast method cannot be gated by one and not the other.
ROLE_GROUPS: dict[str, str] = {
    **BROADCAST_CATEGORIES,
    "broadcast_display_state": DISPLAY,
}


def role_group_for(method_name: str) -> "str | None":
    """Return the role permission group of a ``broadcast_*`` method.

    :param method_name: Name of the :class:`~otio_sync_core.manager.SyncManager`
        broadcast method (e.g. ``"broadcast_display_state"``).
    :returns: A key of :data:`ROLE_PERMISSIONS`' value sets, or ``None`` for an
        ungated broadcast (session plumbing carries no user intent).
    :rtype: str or None
    """
    return ROLE_GROUPS.get(method_name)


def normalise_role(role: "str | None") -> str:
    """Return a recognised role, resolving anything unknown permissively.

    An unrecognised or absent role resolves to :data:`DEFAULT_ROLE`, **never**
    to the most restrictive role.  A peer running code that predates roles, an
    entry adopted from an older peer's roster, or a typo in a policy must not
    silently lock someone out — and a filter that read absence as "not a driver"
    would report a session with drivers in it as driverless.  Unknown is never
    the restrictive value (the ``xs_flat_playlist`` ``media_exists`` default got
    this wrong once already).

    :param role: A role name, or ``None``.
    :rtype: str
    """
    name = (role or "").strip().lower()
    return name if name in ROLE_PERMISSIONS else DEFAULT_ROLE


def role_permits(role: "str | None", group: "str | None", *, destructive: bool = False) -> bool:
    """Return whether *role* may emit the field group *group*.

    The single predicate behind both enforcement points — the broadcast guard
    and the lease claim gate — so the two cannot disagree about what a role
    means.

    :param role: The emitting peer's session role; unknown resolves to
        :data:`DEFAULT_ROLE` (see :func:`normalise_role`).
    :param group: A role permission group (:func:`role_group_for`), or ``None``
        for an ungated broadcast, which every role may emit.
    :param destructive: Whether the call destroys other participants' work
        rather than adding to it — a clear-all-paint rather than a stroke.  Only
        meaningful for :data:`ANNOTATION`, where it resolves driver-only: this
        is the one row where role is finer-grained than the category, and it
        cannot be expressed as a category because the category table is keyed on
        *method name* and the clear path shares a method with ordinary edits.
        The caller declares its own **intent**, which is not the same as testing
        its own **authority** (the latter belongs to core alone).
    :rtype: bool
    """
    if group is None:
        return True
    resolved = normalise_role(role)
    if destructive and group == ANNOTATION:
        return resolved == DRIVER
    return group in ROLE_PERMISSIONS[resolved]


def strip_role_fields(state: Mapping[str, Any], role: "str | None") -> dict[str, Any]:
    """Return *state* with every field group *role* may not emit removed.

    The role-layer counterpart to :func:`strip_visibility_fields` and
    :func:`strip_position_fields`, and it strips through them rather than
    reimplementing the field lists — so a group gained by one is gained by all
    three.  Whole groups only: a message that dropped ``view_mode`` but kept
    ``clip_guid`` would still be asserting what the session looks at.

    :param state: Outgoing playback/view state dict.
    :param role: The emitting peer's session role.
    :returns: A new dict carrying only the field groups *role* may emit.
    :rtype: dict
    """
    out = dict(state)
    if not role_permits(role, VISIBILITY):
        out = strip_visibility_fields(out)
    if not role_permits(role, POSITION):
        out = strip_position_fields(out)
    return out


#: Environment variable that disables role enforcement at runtime.
ROLE_ENFORCEMENT_ENV = "ORI_SESSION_ROLES"


def role_enforcement_enabled() -> bool:
    """Return whether session roles are enforced on outgoing broadcasts.

    Enabled by default, and the default *policy* (``default_role: driver``) is
    the real off switch: a session that has not opted in behaves exactly as it
    did before roles existed, with nothing stripped and no claim refused.  This
    variable exists for the narrower job of bisecting a suspected role bug in a
    session that *has* opted in, without re-declaring its policy.  Read per call
    rather than cached at import, exactly like :func:`enforcement_enabled` and
    :func:`ownership_enforcement_enabled`, so it can be flipped in a running
    interpreter.

    :rtype: bool
    """
    return os.environ.get(ROLE_ENFORCEMENT_ENV, "1").strip().lower() not in _FALSEY


#: Environment variables declaring a session's role policy at start-up.
#:
#: The policy has no UI in this change (editing it is ``session-role-
#: administration``), and it is resolved here rather than in either plugin for
#: the same reason identity is: one implementation, so the two applications
#: cannot declare different policies for the same session.
ROLE_DEFAULT_ENV = "ORI_SESSION_DEFAULT_ROLE"
ROLE_MEMORY_ENV = "ORI_SESSION_PEER_ROLES"


def role_policy_from_env() -> "tuple[str | None, dict[str, str]]":
    """Return ``(default_role, peer_roles)`` declared in the environment.

    ``ORI_SESSION_PEER_ROLES`` is ``user=role`` pairs separated by commas, e.g.
    ``"alice=driver,bob=reviewer"``.  An unparseable entry is skipped rather
    than raising: a malformed policy must not stop a session starting, and an
    unrecognised role resolves permissively anyway (:func:`normalise_role`).

    :returns: ``(default_role or None, {user: role})``.
    :rtype: tuple
    """
    default_role = (os.environ.get(ROLE_DEFAULT_ENV) or "").strip() or None
    peer_roles: dict[str, str] = {}
    for pair in (os.environ.get(ROLE_MEMORY_ENV) or "").split(","):
        user, _, role = pair.partition("=")
        user, role = user.strip(), role.strip()
        if user and role:
            peer_roles[user] = normalise_role(role)
    return default_role, peer_roles


def peer_role(peer: Mapping[str, Any], default_role: "str | None" = None) -> str:
    """Return a peer-table entry's session role, resolving absence to the default.

    :param peer: Peer entry, i.e. ``{"app", "capabilities", "role", ...}``.
    :param default_role: The session's declared default; ``None`` means
        :data:`DEFAULT_ROLE`.
    :rtype: str
    """
    role = peer.get("role")
    if not role:
        return normalise_role(default_role)
    return normalise_role(role)


def host_candidates(
    peers: Mapping[str, Mapping[str, Any]], default_role: "str | None" = None
) -> "list[tuple[int, str]]":
    """Return ``(rank, guid)`` for every peer eligible to hold visibility authority.

    Eligibility is capability **and** role: a peer that may not emit visibility
    must not be elected to the one seat that decides it, or the session's shot
    freezes with nothing reporting why.  Shared by :func:`elect_host_guid` and
    :func:`has_eligible_driver` so the election and the driverless indicator are
    answers to the same question and cannot disagree.

    :param peers: ``{guid: {"app", "capabilities", "role"}}``.
    :param default_role: Session default, applied to any entry carrying no role.
    :rtype: list
    """
    return [
        (host_rank(p.get("app")), guid)
        for guid, p in peers.items()
        if is_host_capable(p) and role_permits(peer_role(p, default_role), VISIBILITY)
    ]


def has_eligible_driver(
    peers: Mapping[str, Mapping[str, Any]], default_role: "str | None" = None
) -> bool:
    """Return whether any peer in the table could be elected host.

    The single predicate behind both hosts' "Become Controller" gating and the
    session panel's driverless indicator, so neither computes it locally and the
    action cannot be offered in a state the election disagrees about.  ``False``
    is exactly the condition that self-elevation exists to exit.

    :param peers: ``{guid: {"app", "capabilities", "role"}}``.
    :param default_role: Session default, applied to any entry carrying no role.
    :rtype: bool
    """
    return bool(host_candidates(peers, default_role))


def master_rank(peer: Mapping[str, Any], default_role: "str | None" = None) -> int:
    """Return a peer's master-election preference rank (lower is better).

    A *preference*, never a restriction: the master holds the session's
    canonical state and needs full broadcast capability to serve it, so a driver
    is the natural master — but a session with no driver still needs one, and a
    peer promoted on that basis is master for state synchronisation only and
    does not thereby acquire the ``driver`` role.

    :param peer: Peer entry, i.e. ``{"app", "capabilities", "role"}``.
    :param default_role: Session default, applied to an entry carrying no role.
    :returns: ``0`` for a driver, ``1`` otherwise.
    :rtype: int
    """
    return 0 if peer_role(peer, default_role) == DRIVER else 1
