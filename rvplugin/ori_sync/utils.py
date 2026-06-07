import os
import sys
import logging as _logging
import traceback

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from PySide2 import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        QtCore = None


def _make_otio_logger():
    logger = _logging.getLogger("otio_sync")
    if logger.handlers:
        return logger
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    ts_fmt = _logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
    if os.environ.get("DEBUG_OTIO_SYNC"):
        sh = _logging.StreamHandler()
        sh.setFormatter(_logging.Formatter("[OTIOSync] %(message)s"))
        logger.addHandler(sh)
    log_file = os.environ.get("RV_OTIO_SYNC_LOG_FILE")
    if log_file:
        fh = _logging.FileHandler(log_file, mode='w')
        fh.setFormatter(ts_fmt)
        logger.addHandler(fh)
        # Mirror to stderr so the log is visible in the terminal alongside the file.
        eh = _logging.StreamHandler(sys.stderr)
        eh.setFormatter(ts_fmt)
        logger.addHandler(eh)
    return logger


_otio_logger = _make_otio_logger()


def _log(msg):
    if _otio_logger.handlers:
        _otio_logger.debug(msg)


def _log_exc(msg):
    if _otio_logger.handlers:
        _otio_logger.exception(msg)


def _install_excepthook():
    _prev = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        _otio_logger.error(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        _prev(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


if _otio_logger.handlers:
    _install_excepthook()


def _show_warning(msg):
    """Display a warning popup in RV (thread-safe, fire-and-forget)."""
    try:
        if QtCore:
            QtCore.QTimer.singleShot(0, lambda: _show_warning_main(msg))
        else:
            _log(f"WARNING: {msg}")
    except Exception:
        _log(f"WARNING: {msg}")


def _show_warning_main(msg):
    """Show the warning on the main thread."""
    try:
        from PySide2.QtWidgets import QMessageBox
    except ImportError:
        try:
            from PySide6.QtWidgets import QMessageBox
        except ImportError:
            _log(f"WARNING: {msg}")
            return
    try:
        mb = QMessageBox()
        mb.setWindowTitle("OTIOSync")
        mb.setText(msg)
        mb.setIcon(QMessageBox.Warning)
        mb.exec_()
    except Exception as e:
        _log(f"_show_warning_main failed: {e}")


def _parse_ori_session(env_val):
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


def _media_path(url: str) -> str:
    """Normalise a ``file://`` URL (any variant) or local path to a canonical absolute POSIX path.

    Delegates to :func:`opentimelineio.url_utils.filepath_from_url` for
    correct handling of percent-encoding and Windows UNC paths, then
    applies an extra pass to collapse the ``//path`` double-slash that
    OTIO returns for the ``file://localhost//path`` form emitted by
    xStudio's flat-playlist exporter.

    Non-``file://`` strings (plain absolute paths, relative paths) are
    resolved to absolute paths based on the current working directory.

    :param url: A media URL or path string.
    :returns: A normalised absolute path suitable for use as a dict key
        or as an argument to ``rv.commands.addSource``.
    :rtype: str
    """
    if not url:
        return url
    if not url.startswith('file://'):
        return os.path.abspath(os.path.normpath(url))
    try:
        import opentimelineio.url_utils as _url_utils
        path = _url_utils.filepath_from_url(url)
    except Exception:
        # Fallback: manual parse (handles the common macOS cases).
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(url).path)
    # OTIO returns '//path' for file://localhost//path — collapse to '/path'.
    while path.startswith('//'):
        path = path[1:]
    path = os.path.normpath(path)
    return os.path.abspath(path)


def _is_media_track(track) -> bool:
    """Return True if *track* carries source clips (not annotations).

    Matches both the ``"Media"`` name used by RV-originated timelines and
    the ``"Video Track"`` name used by xStudio-originated timelines.
    Audio tracks (``kind != Video``) and the ``"Annotations"`` overlay
    track are explicitly excluded.
    """
    if otio is None:
        return False
    # Compare kind as string to handle both enum and plain-string representations
    # across different OTIO versions (OpenRV's bundled OTIO stores kind as 'Video').
    if track.kind not in (otio.schema.TrackKind.Video, "Video"):
        return False
    name = track.name or ""
    if name.startswith("Annotations"):
        return False
    return not any(
        isinstance(c, otio.schema.Clip) and "annotation_commands" in c.metadata
        for c in track
    )
