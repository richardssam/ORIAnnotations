# SPDX-License-Identifier: Apache-2.0
"""Optional site-level connection defaults, dropped into a plugin's folder.

A site can pre-define its RabbitMQ server by placing a small JSON file
alongside the installed plugin, rather than requiring every user's
environment to carry ``ORI_RMQ_HOST`` set before launch — the same
practical-reachability gap ``session-role-config`` already closed for role
policy (session-config-file).

This module is deliberately best-effort: a missing or malformed file is not
an error, it's the common case (most installs won't have one), so
:func:`load_site_config` returns ``None`` rather than raising.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

#: Default filename looked for in a plugin's installed folder.
DEFAULT_FILENAME = "ori_site_config.json"


def load_site_config(plugin_dir: str, filename: str = DEFAULT_FILENAME) -> "dict | None":
    """Read a site config file from *plugin_dir*, if one is present.

    :param plugin_dir: Directory to look in — each host passes its own
        installed plugin folder (e.g. ``os.path.dirname(__file__)``), since
        this module has no reliable way to guess which installation is
        asking.
    :param filename: Config filename, overridable for tests.
    :returns: A dict with any of ``"host"`` / ``"port"`` present, or ``None``
        if the file is absent, unreadable, malformed, or carries neither key.
    """
    path = os.path.join(plugin_dir, filename)
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.debug(f"site_config: could not read {path}: {e}")
        return None

    if not isinstance(raw, dict):
        return None

    result: dict = {}
    host = raw.get("host") or raw.get("rabbitmq_host")
    if isinstance(host, str) and host.strip():
        result["host"] = host.strip()

    port = raw.get("port") or raw.get("rabbitmq_port")
    if isinstance(port, int) and not isinstance(port, bool):
        result["port"] = port
    elif isinstance(port, str) and port.strip().isdigit():
        result["port"] = int(port.strip())

    return result or None
