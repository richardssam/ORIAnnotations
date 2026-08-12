import time
from unittest import mock
import pytest
from otio_sync_core.manager import SyncManager
from otio_sync_core.protocol_messages import PeerAnnounce

def test_manager_resolves_identity_exactly_once():
    """Test that SyncManager resolves identity exactly once at startup and not on per-message paths."""
    
    with mock.patch("otio_sync_core.identity.resolve_identity", return_value={"user": "testuser", "source": "local"}) as mock_resolve:
        
        # 1. Start the manager
        manager = SyncManager(session_id="test_session")
        
        # 2. It should have resolved identity once
        assert mock_resolve.call_count == 1
        assert manager.identity == {"user": "testuser", "source": "local"}
        assert manager._peers[manager.self_guid]["identity"] == {"user": "testuser", "source": "local"}
        
        # 3. Process multiple announce messages
        for i in range(5):
            announce = PeerAnnounce(
                peer_guid=f"peer-{i}",
                app="test_app",
                capabilities=["visibility"],
                # no identity field here to ensure it doesn't try to look it up as a fallback
            )
            manager._h_peer_announce(announce, {}, "some_source")
            
        # 4. Identity should not have been resolved again
        assert mock_resolve.call_count == 1
