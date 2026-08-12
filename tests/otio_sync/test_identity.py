import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from unittest import mock
import pytest
from otio_sync_core import identity  # noqa: E402

def test_local_identity_gecos_parsing():
    """Test various gecos formats parsing correctly."""
    with mock.patch("otio_sync_core.identity.getpass.getuser", return_value="alice"), \
         mock.patch("otio_sync_core.identity.socket.gethostname", return_value="host1"):

        # 1. Full name
        mock_pwnam = mock.Mock()
        mock_pwnam.pw_gecos = "Alice Smith"
        with mock.patch("pwd.getpwnam", return_value=mock_pwnam):
            ident = identity.local_identity()
            assert ident["first_name"] == "Alice"
            assert ident["last_name"] == "Smith"

        # 2. Empty
        mock_pwnam.pw_gecos = ""
        with mock.patch("pwd.getpwnam", return_value=mock_pwnam):
            ident = identity.local_identity()
            assert ident["first_name"] == ""
            assert ident["last_name"] == ""

        # 3. Comma stanza
        mock_pwnam.pw_gecos = "Alice Smith,Room 1,Ext 2,Home 3"
        with mock.patch("pwd.getpwnam", return_value=mock_pwnam):
            ident = identity.local_identity()
            assert ident["first_name"] == "Alice"
            assert ident["last_name"] == "Smith"

        # 4. Comma stanza with only first name
        mock_pwnam.pw_gecos = "Bob,Room 1,Ext 2,Home 3"
        with mock.patch("pwd.getpwnam", return_value=mock_pwnam):
            ident = identity.local_identity()
            assert ident["first_name"] == "Bob"
            assert ident["last_name"] == ""

        # 5. Missing pwd info
        with mock.patch("pwd.getpwnam", side_effect=KeyError("user not found")):
            ident = identity.local_identity()
            assert ident["first_name"] == ""
            assert ident["last_name"] == ""


def test_identity_from_override():
    """Test identity from override."""
    with mock.patch("otio_sync_core.identity.local_identity", return_value={"user": "alice", "host": "host1"}):

        # Two parts
        ident = identity.identity_from_override("Bob Jones")
        assert ident["first_name"] == "Bob"
        assert ident["last_name"] == "Jones"
        assert ident["user"] == "alice"
        assert ident["host"] == "host1"
        assert ident["source"] == "override"

        # One part
        ident = identity.identity_from_override("Bob")
        assert ident["first_name"] == "Bob"
        assert ident["last_name"] == ""
        assert ident["user"] == "alice"
        assert ident["host"] == "host1"
        assert ident["source"] == "override"

        # Blank
        assert identity.identity_from_override("   ") == {}


def test_resolve_identity_env_overrides():
    """Test that env overrides take precedence."""
    with mock.patch("otio_sync_core.identity.local_identity", return_value={
        "user": "alice", "first_name": "Alice", "last_name": "Smith", "host": "host1", "source": "local"
    }):

        # No overrides
        with mock.patch.dict(os.environ, clear=True):
            ident = identity.resolve_identity()
            assert ident["user"] == "alice"
            assert ident["first_name"] == "Alice"
            assert ident["source"] == "local"

        # Override user only
        with mock.patch.dict(os.environ, {"ORI_SYNC_USER": "bob"}):
            ident = identity.resolve_identity()
            assert ident["user"] == "bob"
            assert ident["first_name"] == "Alice"
            assert ident["source"] == "override"

        # Override name only
        with mock.patch.dict(os.environ, {"ORI_SYNC_NAME": "Bob Jones"}):
            ident = identity.resolve_identity()
            assert ident["user"] == "alice"
            assert ident["first_name"] == "Bob"
            assert ident["last_name"] == "Jones"
            assert ident["source"] == "override"

        # Override both
        with mock.patch.dict(os.environ, {"ORI_SYNC_USER": "bob", "ORI_SYNC_NAME": "Bob"}):
            ident = identity.resolve_identity()
            assert ident["user"] == "bob"
            assert ident["first_name"] == "Bob"
            assert ident["last_name"] == ""
            assert ident["source"] == "override"


def test_normalise():
    """Test normalising an identity dict."""

    # Valid dict
    raw = {"user": "alice", "first_name": "Alice", "unknown": "field"}
    norm = identity.normalise(raw)
    assert norm["user"] == "alice"
    assert norm["first_name"] == "Alice"
    assert norm["last_name"] == ""
    assert norm["host"] == ""
    assert "unknown" not in norm

    # Int coercing to string
    raw = {"user": 123}
    norm = identity.normalise(raw)
    assert norm["user"] == "123"

    # All empty or spaces
    raw = {"user": "  ", "first_name": ""}
    assert identity.normalise(raw) is None

    # Empty dict
    assert identity.normalise({}) is None
    assert identity.normalise(None) is None

    # Provenance alone identifies nobody. local_identity() always stamps
    # `source`, so a peer whose user and host both failed to resolve sends
    # exactly this — and it must not count as an identity, or it overwrites a
    # good one already known for that peer.
    assert identity.normalise({
        "user": "", "first_name": "", "last_name": "", "host": "", "source": "local"
    }) is None
