import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

import time
import pytest
from otio_sync_core.manager import SyncManager  # noqa: E402
from otio_sync_core.protocol_messages import PeerAnnounce  # noqa: E402

def test_peer_identity_propagation_announce():
    """A peer learned from an announcement carries the identity it announced."""
    manager = SyncManager(session_id="test")
    
    announce = PeerAnnounce(
        peer_guid="peer-1",
        app="test",
        capabilities=["visibility"],
        identity={"user": "alice", "first_name": "Alice", "source": "local"}
    )
    
    manager._h_peer_announce(announce, {}, "some_source")
    
    assert "peer-1" in manager._peers
    assert manager._peers["peer-1"]["identity"] == {"user": "alice", "first_name": "Alice", "last_name": "", "host": "", "source": "local"}

def test_peer_identity_propagation_roster():
    """A peer learned from a roster carries the same identity."""
    manager = SyncManager(session_id="test")
    
    roster = {
        "peer-2": {
            "app": "test",
            "capabilities": ["visibility"],
            "identity": {"user": "bob", "first_name": "Bob", "source": "local"}
        }
    }
    
    manager.adopt_peers(roster)
    
    assert "peer-2" in manager._peers
    assert manager._peers["peer-2"]["identity"] == {"user": "bob", "first_name": "Bob", "last_name": "", "host": "", "source": "local"}

def test_announce_no_identity_does_not_clear_known():
    """An announce carrying no identity does not clear a known one."""
    manager = SyncManager(session_id="test")
    
    # 1. Announce with identity
    announce1 = PeerAnnounce(
        peer_guid="peer-3",
        app="test",
        capabilities=["visibility"],
        identity={"user": "charlie", "source": "local"}
    )
    manager._h_peer_announce(announce1, {}, "some_source")
    assert manager._peers["peer-3"]["identity"] == {"user": "charlie", "first_name": "", "last_name": "", "host": "", "source": "local"}
    
    # 2. Announce without identity (simulating an older peer build or just an empty one)
    announce2 = PeerAnnounce(
        peer_guid="peer-3",
        app="test",
        capabilities=["visibility"],
        # no identity
    )
    manager._h_peer_announce(announce2, {}, "some_source")
    assert manager._peers["peer-3"]["identity"] == {"user": "charlie", "first_name": "", "last_name": "", "host": "", "source": "local"}

def test_roster_no_identity_does_not_clear_known():
    """A roster carrying no identity does not clear a known one."""
    manager = SyncManager(session_id="test")
    
    # 1. Add peer with identity
    manager._peers["peer-4"] = {
        "app": "test",
        "capabilities": ["visibility"],
        "identity": {"user": "dave", "source": "local"}
    }
    
    # 2. Roster update without identity
    roster = {
        "peer-4": {
            "app": "test",
            "capabilities": ["visibility"],
            # no identity
        }
    }
    
    manager.adopt_peers(roster)
    assert manager._peers["peer-4"]["identity"] == {"user": "dave", "source": "local"}

def test_adopting_a_roster_does_not_rewrite_a_known_peer():
    """A roster is second-hand: it fills a missing identity and nothing else.

    Rewriting the entry would blank ``capabilities`` from a partial roster —
    taking that peer out of host election — and would restamp ``last_seen``
    from another machine's view of who is present, which is the reason the
    roster carries no liveness stamp of its own.
    """
    manager = SyncManager(session_id="test")
    manager._peers["peer-6"] = {
        "app": "xstudio",
        "capabilities": ["visibility"],
        "last_seen": 0.0,
        "identity": {"user": "erin", "source": "local"},
    }

    manager.adopt_peers({"peer-6": {}})

    entry = manager._peers["peer-6"]
    assert entry["app"] == "xstudio"
    assert entry["capabilities"] == ["visibility"]
    assert entry["last_seen"] == 0.0
    assert entry["identity"] == {"user": "erin", "source": "local"}


def test_a_roster_fills_an_identity_a_known_peer_lacks():
    """The one field a joiner cannot get elsewhere until the peer announces."""
    manager = SyncManager(session_id="test")
    manager._peers["peer-7"] = {
        "app": "openrv",
        "capabilities": [],
        "last_seen": 0.0,
    }

    manager.adopt_peers({
        "peer-7": {"app": "openrv", "identity": {"user": "frank", "source": "local"}}
    })

    entry = manager._peers["peer-7"]
    assert entry["identity"]["user"] == "frank"
    assert entry["last_seen"] == 0.0


def test_peer_with_no_identity_is_still_added():
    """A peer with no identity is still added to the table."""
    manager = SyncManager(session_id="test")
    
    announce = PeerAnnounce(
        peer_guid="peer-5",
        app="test",
        capabilities=["visibility"],
    )
    manager._h_peer_announce(announce, {}, "some_source")
    
    assert "peer-5" in manager._peers
    assert "identity" not in manager._peers["peer-5"]
