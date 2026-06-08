#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Stateless helpers shared across all ori_sync modules."""

import contextlib
import logging
import os
import sys

# ── path setup ─────────────────────────────────────────────────────────────────
# Performed here so any module that imports utils gets the sys.path side-effects.

_here = os.path.dirname(os.path.realpath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_here))
_python_dir = os.path.join(_repo_root, "python")
_manifest_dir = os.path.join(_repo_root, "otio_event_plugin")
_manifest_file = os.path.join(_manifest_dir, "plugin_manifest.json")

for _p in (_python_dir, _manifest_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if os.path.exists(_manifest_file):
    _existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if _manifest_file not in _existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            _existing + os.pathsep + _manifest_file if _existing else _manifest_file
        )

# ── logging ────────────────────────────────────────────────────────────────────


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("ori_sync")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s", datefmt="%H:%M:%S")
    log_file = os.environ.get("ORI_SYNC_LOG_FILE")
    if log_file:
        print("ORI Sync Plugin: Logging output to: ", log_file)
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        # Mirror to stderr so logs appear in the terminal that launched xStudio.
        # Use sys.__stderr__ to bypass xStudio's internal capture of sys.stderr.
        raw_stderr = getattr(sys, "__stderr__", None) or sys.stderr
        if raw_stderr is not None:
            eh = logging.StreamHandler(raw_stderr)
            eh.setFormatter(fmt)
            logger.addHandler(eh)
    return logger


_logger = _make_logger()

# Also configure the core network logger so users can see raw payloads.
_core_logger = logging.getLogger("otio_sync")
_core_logger.setLevel(logging.DEBUG)
for _h in _logger.handlers:
    if _h not in _core_logger.handlers:
        _core_logger.addHandler(_h)


def _log(msg: str) -> None:
    _logger.debug(msg)


def _log_exc(msg: str) -> None:
    _logger.exception(msg)


# ── path helpers ───────────────────────────────────────────────────────────────


def _uri_to_posix_path(uri: str) -> str:
    """Convert a URI or xStudio internal URI string to a POSIX filesystem path.

    Handles the common forms returned by xStudio's ``MediaReference.uri()``:

    * ``file:///path`` → ``/path``
    * ``file://localhost/path`` → ``/path``
    * ``localhost//path`` (xStudio-specific, no ``file:`` scheme) → ``/path``
    * plain ``/path`` → ``/path`` (unchanged)
    """
    import urllib.parse
    if uri.startswith("file:"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        # file://localhost//path serialises with netloc='localhost' and
        # path='//absolute/path' — normalize the double leading slash.
        if path.startswith("//"):
            path = path[1:]
        return path
    if uri.startswith("localhost//"):
        # xStudio stores local URIs as "localhost//absolute/path"
        return uri[10:]  # strip "localhost/" leaving "/absolute/path"

    # If the path is relative, convert to absolute using current working directory.
    if not uri.startswith(("http://", "https://")) and not os.path.isabs(uri):
        return os.path.abspath(uri)

    return uri


# ── session string parsing ─────────────────────────────────────────────────────


def _parse_ori_session(env_val: str) -> tuple:
    """Parse ``[host:]session_name`` from an env-var string.

    :param env_val: Raw value of ``ORI_SESSION``.
    :returns: ``(host, session_name)`` tuple; host defaults to ``localhost``
        (or ``ORI_RMQ_HOST`` if set) when no colon is present.
    :rtype: tuple
    """
    default_host = os.environ.get("ORI_RMQ_HOST", "127.0.0.1")
    if ":" in env_val:
        host, name = env_val.split(":", 1)
        return (host or default_host, name)
    return (default_host, env_val)


# ── timeout guard ───────────────────────────────────────────────────────────────


def bounded(timeout_ms: int):
    """Decorator: run a controller method with a lowered connection timeout.

    The decorated method must be on an object exposing ``self.plugin.connection``
    (all sync controllers do).  Equivalent to wrapping the whole body in
    :func:`bounded_timeout`, but as a one-line decorator so no re-indentation is
    needed.  Use on poll-thread methods that read/write xStudio actors which
    could be stale or busy (bookmarks, playhead, viewport).

    :param timeout_ms: Temporary ``default_timeout_ms`` while the method runs.
    """
    import functools

    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(self, *args, **kwargs):
            with bounded_timeout(self.plugin.connection, timeout_ms):
                return fn(self, *args, **kwargs)
        return _wrapper

    return _decorator


@contextlib.contextmanager
def bounded_timeout(connection, timeout_ms: int):
    """Temporarily lower a connection's ``default_timeout_ms`` for blocking reads.

    xStudio's Python API performs synchronous ``request_receive`` round-trips
    bounded by ``connection.default_timeout_ms`` (100 s by default).  A call to
    a stale/destroyed actor therefore blocks the caller for the full 100 s.
    The blocking happens inside a C++ ``dequeue`` that holds the GIL, so a
    Python-thread timeout cannot interrupt it — the timeout *must* be enforced
    at the C++ level via ``default_timeout_ms``.

    Wrap only quick reads (playhead state, viewport state) in this context.
    Heavy calls such as ``load_otio`` / ``to_otio_string`` must keep the long
    default, so they are deliberately left outside.

    :param connection: The xStudio ``Connection`` whose timeout to adjust.
    :param timeout_ms: Temporary timeout in milliseconds.
    """
    prev = getattr(connection, "default_timeout_ms", None)
    try:
        connection.default_timeout_ms = timeout_ms
        yield
    finally:
        if prev is not None:
            connection.default_timeout_ms = prev


# ── QML constants ──────────────────────────────────────────────────────────────

QML_FOLDER = "qml/ORISyncPlugin.1"
SESSION_DIALOG_QML = "SessionDialog {}"
