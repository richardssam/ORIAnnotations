"""Sync Recorder package.

Provides SyncRecorder and SyncPlayer for recording and playing back OTIO sync session events.
"""

from .recorder import SyncRecorder
from .player import SyncPlayer

__all__ = ["SyncRecorder", "SyncPlayer"]
