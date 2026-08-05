#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for fix-discovery-thread-safety (xStudio side).

Property under test: the discovery-timeout thread never touches the
``SyncManager`` beyond the one status read that decides whether the timeout
still applies — it only enqueues — and the resulting ``self_elect`` command,
drained on the poll thread, elects only while the session is still discovering.

Unlike ``test_sequence_reconciliation_convergence.py`` this does **not** need
the xStudio-bundled interpreter: ``xstudio.*`` and ``pika`` are auto-stubbed
below, and the two functions under test are called with a fake ``self`` rather
than a constructed plugin.  Run it with the repo venv alongside the rest of the
suite::

    .venv/bin/python -m pytest tests/xstudio_plugin/test_discovery_self_elect.py

Lives under the repo-root ``tests/`` tree, not inside ``xstudio_plugin/`` —
xStudio's plugin loader enumerates every subdirectory there as a plugin
candidate and logs a load failure for non-plugin packages.
"""
import importlib.abc
import importlib.machinery
import os
import queue
import sys
import types

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))


class _AutoModule(types.ModuleType):
    """Stub module whose every attribute resolves to a throwaway class."""

    __path__ = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        value = type(name, (object,), {"__init__": lambda self, *a, **k: None})
        setattr(self, name, value)
        return value


class _AutoStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolves ``xstudio.*`` and ``pika`` to permissive stubs.

    The plugin's controller modules import xStudio names at module level, and
    ``rabbitmq_network`` imports pika.  Neither is installed in the repo venv,
    and neither is exercised by the code under test.
    """

    ROOTS = ("xstudio", "pika")

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return _AutoModule(spec.name)

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _AutoStubFinder())

# Register a lightweight 'ori_sync' package so the real __init__.py never runs.
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [os.path.join(_repo_root, "xstudio_plugin", "ori_sync")]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import ori_sync_plugin  # noqa: E402
from otio_sync_core.manager import (  # noqa: E402
    STATE_DISCOVERING,
    STATE_JOINING,
    STATE_SYNCED,
)

ORISyncPlugin = ori_sync_plugin.ORISyncPlugin


class RecordingManager:
    """Minimal SyncManager stand-in that records every attribute touched."""

    def __init__(self, status=STATE_DISCOVERING):
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "touched", [])
        object.__setattr__(self, "registered", [])
        object.__setattr__(self, "elected", [])

    def __getattribute__(self, name):
        if not name.startswith("_") and name not in ("touched",):
            object.__getattribute__(self, "touched").append(name)
        return object.__getattribute__(self, name)

    def register_timeline(self, tl):
        self.registered.append(tl)

    def elect_self_as_master(self, broadcast=True):
        self.elected.append(broadcast)
        object.__setattr__(self, "status", STATE_SYNCED)


class FakeBuilder:
    def __init__(self, timelines=("tl-a", "tl-b")):
        self.timelines = list(timelines)
        self.calls = 0

    def build_otio_timelines(self):
        self.calls += 1
        return list(self.timelines)


def _fake_plugin(manager, builder=None):
    """A duck-typed stand-in for ORISyncPlugin.

    The two methods under test only reach ``manager``, ``builder``,
    ``_cmd_queue``, and ``DISCOVERY_TIMEOUT``, so the real (xStudio-dependent)
    constructor is deliberately not run.
    """
    return types.SimpleNamespace(
        manager=manager,
        builder=builder or FakeBuilder(),
        _cmd_queue=queue.Queue(),
        DISCOVERY_TIMEOUT=0.0,
    )


# ---------------------------------------------------------------------------
# The timeout thread only enqueues
# ---------------------------------------------------------------------------


def test_discovery_timeout_enqueues_and_touches_only_status():
    mgr = RecordingManager(STATE_DISCOVERING)
    plugin = _fake_plugin(mgr)

    ORISyncPlugin._discovery_timeout_task(plugin)

    assert plugin._cmd_queue.get_nowait() == ("self_elect", {})
    # The whole point of the change: no registration, no election, no broadcast
    # from this thread.
    assert set(mgr.touched) == {"status"}
    assert mgr.registered == []
    assert mgr.elected == []
    assert plugin.builder.calls == 0


def test_discovery_timeout_enqueues_nothing_once_a_master_answered():
    mgr = RecordingManager(STATE_SYNCED)
    plugin = _fake_plugin(mgr)

    ORISyncPlugin._discovery_timeout_task(plugin)

    assert plugin._cmd_queue.empty()


def test_discovery_timeout_survives_disconnect():
    plugin = _fake_plugin(manager=None)

    ORISyncPlugin._discovery_timeout_task(plugin)

    assert plugin._cmd_queue.empty()


# ---------------------------------------------------------------------------
# The drained command does the work, guarded by a re-check
# ---------------------------------------------------------------------------


def test_self_elect_registers_then_elects_on_poll_thread():
    mgr = RecordingManager(STATE_DISCOVERING)
    builder = FakeBuilder(["tl-a", "tl-b"])
    plugin = _fake_plugin(mgr, builder)

    ORISyncPlugin._execute_command(plugin, "self_elect", {})

    assert builder.calls == 1
    assert mgr.registered == ["tl-a", "tl-b"]
    assert mgr.elected == [True]
    # Registration precedes election, so a joiner's snapshot is never empty.
    assert mgr.touched.index("register_timeline") < mgr.touched.index("elect_self_as_master")


def test_self_elect_is_a_noop_when_a_master_appeared_meanwhile():
    """The authoritative check: an I_AM_MASTER landed during queue latency."""
    for status in (STATE_SYNCED, STATE_JOINING):
        mgr = RecordingManager(status)
        plugin = _fake_plugin(mgr)

        ORISyncPlugin._execute_command(plugin, "self_elect", {})

        assert mgr.elected == [], f"must not elect from {status}"
        assert mgr.registered == []
        assert plugin.builder.calls == 0


def test_self_elect_is_a_noop_after_disconnect():
    plugin = _fake_plugin(manager=None)

    # leave_session drained ahead of us cleared the manager; must not raise.
    ORISyncPlugin._execute_command(plugin, "self_elect", {})

    assert plugin.builder.calls == 0
