# SPDX-License-Identifier: Apache-2.0
"""Read-only projection of :class:`~otio_sync_core.manager.SyncManager` state.

This is the single source of truth behind every Session State surface — the
OpenRV Qt panel, any future xStudio-native panel, and anything else that wants
to show who is in a session and who is driving it.  The two host applications
cannot share a Qt model (OpenRV's plugin runs PySide6 in-process, xStudio's
runs against xStudio's own QML engine over ``python_callback``), so what they
share is *this dict* rather than a widget: the semantics that could drift live
here once, and each host only writes layout.

Strictly read-only by design.  An observability surface reads the core and
never extends it — the moment a panel needs a field the manager does not have,
the answer is to derive it here or to pass it in from the host, never to grow
:class:`SyncManager` for the benefit of a debug view.
"""

from typing import Any

from . import authority

__all__ = ["session_state_snapshot", "peer_role", "display_name"]


def peer_role(peer: "dict[str, Any]", default_role: "str | None" = None) -> str:
    """Return the session role for one peer-table entry.

    The *session* role — ``driver`` / ``reviewer`` / ``viewer`` — which is what
    this participant is permitted to emit at all.  Master and host are separate
    axes and stay the separate ``is_master`` / ``is_host`` flags they already
    are: a session may have several drivers and exactly one host, and a change
    of master changes nobody's role.

    Derived here rather than in either panel, for the same reason
    :func:`display_name` is: two hosts formatting the same half-known state
    independently is how they drifted before.

    An entry carrying no role resolves to the session default, never to the most
    restrictive role.

    :param peer: Peer entry, i.e. ``{"app": str, "capabilities": [...], "role": str}``.
    :param default_role: The session's declared default role.
    :rtype: str
    """
    return authority.peer_role(peer, default_role)


def display_name(peer: "dict[str, Any]") -> str:
    """Derive the display name for a peer.
    
    Rule: "First Last" -> user -> app
    """
    ident = peer.get("identity")
    if ident:
        first = ident.get("first_name", "")
        last = ident.get("last_name", "")
        full = f"{first} {last}".strip()
        if full:
            return full
            
        user = ident.get("user", "")
        if user:
            return user
            
    return peer.get("app") or "Unknown"


def session_state_snapshot(manager) -> "dict[str, Any]":
    """Return a plain-dict view of the manager's session state.

    Contains no Qt types and no live references to manager internals, so it is
    safe to hand to a Qt model, serialise over ``python_callback``, or assert
    against in a test.

    :param manager: The :class:`SyncManager` to read.
    :returns: Session status, elected roles, and one entry per known peer.
        Peers are ordered with this peer first, then by GUID, so a list view
        built from successive snapshots does not reshuffle.
    :rtype: dict
    """
    self_guid = manager.self_guid
    master_guid = manager.master_guid
    host_guid = manager.host_guid
    default_role = getattr(manager, "default_role", None)

    # Resolve lease owners once rather than per peer — three dict reads instead
    # of three per row, and every row then agrees on the same instant.
    lease_owners = {
        channel: getattr(manager._leases.get(channel), "owner_guid", None)
        for channel in authority.LEASE_CHANNELS
    }

    peers = []
    for guid, peer in sorted(
        manager._peers.items(), key=lambda kv: (kv[0] != self_guid, kv[0])
    ):
        ident = peer.get("identity") or {}
        peers.append(
            {
                "guid": guid,
                "app": peer.get("app") or "Unknown",
                "role": peer_role(peer, default_role),
                "is_self": guid == self_guid,
                "is_master": guid == master_guid,
                "is_host": guid == host_guid,
                "holds_position_lease": lease_owners[authority.CHANNEL_POSITION] == guid,
                "holds_display_lease": lease_owners[authority.CHANNEL_DISPLAY] == guid,
                "holds_structure_lease": lease_owners[authority.CHANNEL_STRUCTURE] == guid,
                "display_name": display_name(peer),
                "user": ident.get("user", ""),
                "host": ident.get("host", ""),
                "source": ident.get("source", ""),
            }
        )

    return {
        "status": manager.status,
        "self_guid": self_guid,
        "master_guid": master_guid,
        "host_guid": host_guid,
        "master_app": (manager._peers.get(master_guid) or {}).get("app") or "",
        "is_master": manager.is_master,
        "is_host": manager.is_host,
        "self_role": getattr(manager, "self_role", authority.DEFAULT_ROLE),
        "default_role": default_role or authority.DEFAULT_ROLE,
        # Reported, not merely inferable from a menu item being disabled: a
        # session whose view nobody may change has to say so, and the panel is
        # where it says it.  Derived once here so both hosts report the same
        # condition rather than each deciding what "driverless" means.
        "driverless": not authority.has_eligible_driver(manager._peers, default_role),
        "active_timeline_guid": manager.active_timeline_guid,
        "selected_clip_guid": manager.selected_clip_guid,
        "peers": peers,
    }
