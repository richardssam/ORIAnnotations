"""Tests the OTIO Sync menu's sync-core-unavailable state (rvplugin/ori_sync).

This is a path that only executes when something is *already* broken, so it is
the kind of code that rots unnoticed -- which is exactly what happened in the
vendored-pika incident, where a swallowed ImportError left RV presenting a
fully populated but completely inert menu. Exercising it here means the manual
in-RV check only has to confirm rendering, not logic.

Run standalone: it imports plugin.py with otio_sync_core deliberately poisoned,
which is not a state other test modules in this directory want to share.
"""

import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "rvplugin/ori_sync"))

sys.modules.setdefault("PySide2", MagicMock())
sys.modules.setdefault("PySide2.QtCore", MagicMock())

_mock_rv = MagicMock()
sys.modules["rv"] = _mock_rv
sys.modules["rv.commands"] = _mock_rv.commands
sys.modules["rv.extra_commands"] = _mock_rv.extra_commands
sys.modules["rv.rvtypes"] = _mock_rv.rvtypes
_mock_rv.rvtypes.MinorMode = type("MinorMode", (object,), {"__init__": lambda self: None})

# Sentinel objects so the two menu states are distinguishable by identity.
_mock_rv.commands.DisabledMenuState = "DISABLED"
_mock_rv.commands.NeutralMenuState = "NEUTRAL"


def _load_plugin(sync_core_available):
    """(Re)import plugin.py with otio_sync_core either present or poisoned.

    Setting sys.modules[name] = None makes `import name` raise ImportError,
    which is precisely the failure plugin.py's guard is written against.
    """
    for mod in ("plugin", "utils", "sequence_sync", "playback_sync",
                "display_sync", "annotation_sync", "color_sync"):
        sys.modules.pop(mod, None)

    saved = {k: v for k, v in sys.modules.items() if k.startswith("otio_sync_core")}
    try:
        if sync_core_available:
            for k, v in saved.items():
                sys.modules[k] = v
        else:
            for k in list(sys.modules):
                if k.startswith("otio_sync_core"):
                    del sys.modules[k]
            sys.modules["otio_sync_core"] = None
        return importlib.import_module("plugin")
    finally:
        sys.modules.pop("otio_sync_core", None)
        for k, v in saved.items():
            sys.modules[k] = v


def _menu_items(menu):
    """Unwrap [(title, [items])] -> items."""
    assert len(menu) == 1, menu
    return menu[0][1]


class TestSyncUnavailableMenu(unittest.TestCase):
    def test_import_failure_sets_the_sentinel(self):
        plugin = _load_plugin(sync_core_available=False)
        self.assertTrue(plugin._SYNC_IMPORT_ERROR)
        self.assertIsNone(plugin.SyncManager)
        self.assertIsNone(plugin.RabbitMQNetwork)

    def test_unavailable_menu_offers_one_disabled_item(self):
        plugin = _load_plugin(sync_core_available=False)
        mode = plugin.OpenRVSyncPlugin.__new__(plugin.OpenRVSyncPlugin)
        mode.sync_manager = None

        items = _menu_items(mode._build_menu())
        self.assertEqual(len(items), 1, items)
        label, action, _key, state = items[0]
        self.assertIn("Unavailable", label)
        self.assertIsNone(action)
        self.assertEqual(state(), "DISABLED")

    def test_unavailable_menu_hides_the_inert_session_items(self):
        """The whole point: no Create/Join/Add Clip that silently no-op."""
        plugin = _load_plugin(sync_core_available=False)
        mode = plugin.OpenRVSyncPlugin.__new__(plugin.OpenRVSyncPlugin)
        mode.sync_manager = None

        labels = " ".join(i[0] for i in _menu_items(mode._build_menu()))
        for gone in ("Create Session", "Join Session", "Add Clip"):
            self.assertNotIn(gone, labels)

    def test_available_menu_is_unchanged(self):
        plugin = _load_plugin(sync_core_available=True)
        self.assertIsNone(plugin._SYNC_IMPORT_ERROR)

        mode = plugin.OpenRVSyncPlugin.__new__(plugin.OpenRVSyncPlugin)
        mode.sync_manager = None
        labels = [i[0] for i in _menu_items(mode._build_menu())]
        self.assertIn("Create Session...", labels)
        self.assertIn("Join Session...", labels)
        self.assertNotIn("Sync Unavailable (otio_sync_core import failed)", labels)


if __name__ == "__main__":
    unittest.main()
