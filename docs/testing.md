# Testing constraints

Read this before running or writing tests. The suite spans two Python
interpreters and several host-application stubs, and most of the confusing
failures come from one of the traps below rather than from the code under test.

---

## Two suites, two interpreters

| Command | Runs | Interpreter |
| --- | --- | --- |
| `./run_tests_core.sh` | `tests/otio_sync/` | standard `python` (pyenv) |
| `./run_tests_xstudio.sh` | `tests/xstudio_plugin/` | xStudio's bundled `python3` |

`tests/xstudio_plugin/` needs the `xstudio` module, which only exists inside
xStudio's bundled interpreter. `tests/xstudio_plugin/conftest.py` skips the
whole directory when that import fails, and `pytest.ini` defines the `xstudio`
marker it applies, so a plain `pytest` in the standard environment skips them
cleanly instead of erroring at collection.

Both scripts pass extra arguments straight through:
`./run_tests_core.sh -k playback -x`.

## Use the repo's `python`, not a system `python3.11`

The core suite must run on the pyenv interpreter (3.10, **OpenTimelineIO
0.18.1**). OTIO `0.19.0.dev1` raises `bad any cast` on nested metadata *and* on
every `deepcopy`, which breaks both the suite and RV builds. If tests fail with
`bad any cast`, check the interpreter before you debug anything else.

## Host modules are stubbed, and the stubs collide

`rvplugin/ori_sync/*` imports `rv.commands` at module scope, so a test that
imports a controller must install a stub in `sys.modules` first. Several test
modules do this, and **whichever one imports first owns `rv` for the whole
session** — `sys.modules.setdefault` makes every later installation a no-op.

That is what makes a test pass alone and fail in a full run, or vice versa.

The pattern that survives it (see
`tests/otio_sync/test_playback_view_dispatch.py` and
`test_playback_broadcast_guid.py`): install the stub for import, then **rebind
the controller module's `rv` attribute per test** in `setUp`, restoring it in
`tearDown`:

```python
import playback_sync as _ps

def setUp(self):
    self._saved_rv = _ps.rv
    _ps.rv = _fake_rv       # order-independent: does not depend on who won sys.modules

def tearDown(self):
    _ps.rv = self._saved_rv  # leaves the other modules' stubs untouched
```

Stub the RV *display* rather than hard-coding return values — back `viewNode()`
with a variable the tests move. Controllers decide what to do by reading what
RV is currently showing, so a stub whose display cannot diverge from its
history cannot test the behaviour that actually breaks.

## Known flaky test

`tests/otio_sync/test_sync_recorder.py::TestSyncRecorderPlayer::test_resume_preserves_event_spacing`
asserts on wall-clock event spacing and fails intermittently under load. It has
been observed failing once in a full run and passing on the next three, with no
code change in between.

**Re-run before concluding that a change broke it**, and check free memory and
swap while you are there — swap-induced latency mimics a timing race and has
produced false diagnoses in this repo more than once. The same caution applies
to `sync_test/` soak results.

## The rvpkg is what OpenRV actually loads

Tests import from the repo, but OpenRV loads the *installed* package. After
changing anything under `rvplugin/ori_sync/` or `python/otio_sync_core/`, run
`rvplugin/ori_sync/reinstall.csh` before testing in RV, or you will be testing
the previous build. New `otio_sync_core` modules must also be added by hand to
the zip list in `rvplugin/ori_sync/makepackage.csh` — a module missing there
makes the whole sync plugin silently inert, because `__init__.py` swallows the
`ImportError`.

## Reading a live soak: merge the peer logs

A soak produces one log per peer, and every sync question is a question about
two peers at the same instant. Grepping each file separately biases the answer
towards whatever you thought to grep for — two of this repo's longest
investigations were prolonged that way.

```bash
python debug/merge_sync_logs.py --view diverge rvplugin/ori_sync/xstudio_{host,client}.log
python debug/merge_sync_logs.py --view state --since 15:03:00 <logs...>
```

`--view diverge` reports only the intervals where peers disagreed about what is
on screen; `--view state` shows what each peer believed, side by side;
`--view wire` correlates each send with the peers that received it. Works on
xStudio and OpenRV logs, and on a mixed pair. See
[debug/README.md](../debug/README.md) for the full set.

**A missing log line is not evidence of a missing action.** Until 2026-08-13
xStudio logged every frame it *broadcast* and nothing about applying one, so a
peer that followed every frame and one that silently dropped them all produced
identical logs. Before concluding a peer did nothing, confirm the thing it
would have done is actually logged.
