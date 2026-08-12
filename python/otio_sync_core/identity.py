# SPDX-License-Identifier: Apache-2.0
"""Who is on the other end of a session: peer identity and where it comes from.

One module resolves the local peer's identity, and every consumer — the wire
format, the peer table, the shared state projection, both hosts' panels — is
independent of where it came from.  Replacing :func:`local_identity` with an
authenticated provider is therefore the whole of that change: no message
format moves, and no host learns anything new.

Identity here is **self-declared and unverified**, on the same terms as the
application name a peer already advertises.  It identifies cooperating
participants; it does not authenticate them.
"""

import getpass
import os
import socket
import logging

logger = logging.getLogger(__name__)

#: Every field an identity may carry on the wire.
ALLOWED_KEYS = ("user", "first_name", "last_name", "host", "source")

#: The subset that actually identifies someone.  ``source`` is deliberately
#: absent: it is provenance, and an identity carrying nothing but provenance
#: identifies no one (see :func:`normalise`).
IDENTIFYING_KEYS = ("user", "first_name", "last_name", "host")


def local_identity() -> dict:
    """
    Resolve the identity of the local user from the system.

    :returns: A dictionary with keys 'user', 'first_name', 'last_name', 'host', 'source'.
    """
    try:
        user = getpass.getuser()
    except Exception as e:
        logger.debug(f"Failed to get user: {e}")
        user = ""

    try:
        host = socket.gethostname()
    except Exception as e:
        logger.debug(f"Failed to get hostname: {e}")
        host = ""

    first_name = ""
    last_name = ""

    if user:
        try:
            # Imported here rather than at module scope: ``pwd`` is POSIX-only,
            # and this module is imported unconditionally by ``manager``, whose
            # own import is inside ``__init__``'s ``try/except ImportError``.
            # A module-scope import would therefore not fail loudly on a
            # platform without ``pwd`` — it would leave the whole sync plugin
            # connected and inert, the failure mode this codebase has shipped
            # once already.
            import pwd

            pwnam = pwd.getpwnam(user)
            gecos = pwnam.pw_gecos
            if gecos:
                # First comma-delimited segment
                name_part = gecos.split(',')[0].strip()
                if name_part:
                    parts = name_part.split(None, 1)
                    if len(parts) == 2:
                        first_name, last_name = parts
                    elif len(parts) == 1:
                        first_name = parts[0]
        except Exception as e:
            logger.debug(f"Failed to get gecos for user {user}: {e}")

    return {
        "user": user,
        "first_name": first_name,
        "last_name": last_name,
        "host": host,
        "source": "local"
    }

def identity_from_override(text: str) -> dict:
    """
    Create an identity from a user-supplied override string, maintaining the local
    user and host.

    :param text: The user-supplied identity string.
    :returns: A dictionary with the parsed identity, or an empty dict if text is blank.
    """
    text = text.strip()
    if not text:
        return {}

    parts = text.split(None, 1)
    if len(parts) == 2:
        first_name, last_name = parts
    elif len(parts) == 1:
        first_name = parts[0]
        last_name = ""
    else:
        first_name = ""
        last_name = ""

    # Get local identity just for user and host
    local = local_identity()

    return {
        "user": local.get("user", ""),
        "first_name": first_name,
        "last_name": last_name,
        "host": local.get("host", ""),
        "source": "override"
    }

def resolve_identity() -> dict:
    """
    Resolve the identity, applying environment overrides if present.

    Overrides:
    - ORI_SYNC_USER
    - ORI_SYNC_NAME

    :returns: A dictionary with the resolved identity.
    """
    override_user = os.environ.get("ORI_SYNC_USER")
    override_name = os.environ.get("ORI_SYNC_NAME")

    # Copied, not aliased: this function edits the fields in place, and
    # ``local_identity()`` is the kind of function a later provider might
    # reasonably cache or memoise. Mutating its return value would then edit
    # the cache, and every subsequent caller would inherit this call's
    # overrides.
    identity = dict(local_identity())

    if override_user is not None or override_name is not None:
        if override_user is not None:
            identity["user"] = override_user.strip()

        if override_name is not None:
            name = override_name.strip()
            parts = name.split(None, 1)
            if len(parts) == 2:
                identity["first_name"], identity["last_name"] = parts
            elif len(parts) == 1:
                identity["first_name"] = parts[0]
                identity["last_name"] = ""
            else:
                identity["first_name"] = ""
                identity["last_name"] = ""

        identity["source"] = "override"

    return identity

def normalise(identity: dict) -> dict | None:
    """
    Normalise an identity received over the wire.
    Drops unknown keys, coerces to string, and returns None if all fields are empty.

    :param identity: The raw identity dict from a message.
    :returns: A normalised dict, or None if the identity is effectively empty.
    """
    if not identity:
        return None

    normalised = {}
    for k in ALLOWED_KEYS:
        val = identity.get(k)
        normalised[k] = str(val).strip() if val is not None else ""

    # Emptiness is judged on the identifying fields only. ``source`` is
    # provenance, not content: ``local_identity()`` stamps it unconditionally,
    # so a peer whose user and host both failed to resolve would otherwise
    # normalise to a present-but-blank identity — which then overwrites a good
    # identity already known for that peer, since the caller's "keep what we
    # have" branch only fires on ``None``.
    if not any(normalised[k] for k in IDENTIFYING_KEYS):
        return None

    return normalised
