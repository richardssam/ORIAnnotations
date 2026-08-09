import time
import urllib.request
import json
import logging
import re
import sys
import os
import socket
import bisect
import uuid
import subprocess
import contextlib
import threading
from collections import Counter
from datetime import datetime, timezone

from .spawner import AppSpawner
from .config import SyncTestConfig


def _find_free_ports(count, start=19000):
    """Find `count` consecutive-ish free TCP ports starting near `start`."""
    ports = []
    candidate = start
    while len(ports) < count:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                ports.append(candidate)
            except OSError:
                pass
        candidate += 1
        if candidate > start + 200:
            raise RuntimeError(f"Could not find {count} free ports near {start}")
    return ports

# Try to import SyncPlayer from sync_recorder
python_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sync_test_dir = os.path.abspath(os.path.join(python_dir, ".."))
repo_dir = os.path.abspath(os.path.join(sync_test_dir, ".."))

if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)
if python_dir not in sys.path:
    sys.path.insert(0, python_dir)

try:
    from sync_recorder.player import SyncPlayer
except ImportError:
    SyncPlayer = None

from otio_sync_core import project_state, diff_states, normalize_clip_name
from . import annotation_assertions


def _normalize_clip_name(name):
    # Delegate to the shared projection helper so record-side and replay-side
    # agree on normalization (kept as a thin alias for existing call sites).
    return normalize_clip_name(name)


def _media_identity(state):
    """Normalised on-screen media path, or None if the app did not report one.

    This is the only per-clip identity the harness gets. ``state["clip"]`` is
    the active *timeline* name — identical for both apps whichever clip they
    are actually showing — so without this, a frame comparison cannot tell
    "both apps at frame 61 of the same shot" from "each at frame 61 of a
    different shot". The two are not the same thing and only one is sync.

    Both hooks store the raw value, which may be a ``file://`` URI (xStudio's
    media_reference) or a plain path (OpenRV's sourceMedia), so the strings do
    not compare directly — the reason ``media_path`` sits in compare_states'
    ignore_keys. Normalise here rather than leave the field unusable.
    """
    if not isinstance(state, dict):
        return None
    path = state.get("media_path")
    if not path:
        return None
    if path.startswith("file://localhost"):
        path = path[16:]
    elif path.startswith("file://"):
        path = path[7:]
    elif path.startswith("file:/"):
        path = path[5:]
    path = os.path.normpath(path)
    # normpath deliberately PRESERVES exactly two leading slashes (POSIX leaves
    # "//" implementation-defined), so a "file:////Users/x" URI normalises to
    # "//Users/x" and compares unequal to "/Users/x" — the same file, reported
    # as a clip mismatch. Collapse any leading run of slashes to one.
    return re.sub(r"^/+", "/", path)


def _media_disagreement(states, app_names):
    """Names of apps showing different media, or None when they agree.

    Apps that report no media at all are excluded rather than treated as a
    mismatch — "unknown" is not "different" (the same reasoning that makes
    media_exists default True).
    """
    known = [
        (name, _media_identity(st))
        for st, name in zip(states, app_names)
        if _media_identity(st) is not None
    ]
    if len(known) < 2:
        return None
    first = known[0][1]
    if all(ident == first for _, ident in known):
        return None
    # Full paths, not basenames: two apps can report the same filename from
    # different directories (or one resolving a symlink the other does not),
    # and a message showing only "car.mov vs car.mov" is unreadable.
    return ", ".join(f"{name}={ident}" for name, ident in known)


def _view_mode_disagreement(states, app_names):
    """Report a sequence-vs-isolated-clip split, or None when the apps agree.

    A frame index means a different thing in each mode — an offset into the
    whole sequence versus an offset into one isolated clip — so comparing
    across a split compares unlike quantities. Apps not reporting a view mode
    are excluded: unknown is not disagreement.
    """
    known = [
        (name, st.get("view_mode"))
        for st, name in zip(states, app_names)
        if isinstance(st, dict) and st.get("view_mode") is not None
    ]
    if len(known) < 2:
        return None
    first = known[0][1]
    if all(mode == first for _, mode in known):
        return None
    return ", ".join(f"{name}={mode}" for name, mode in known)


def _playhead_is_playing(state):
    """True if *state* reports an actively playing playhead.

    Tolerates both shapes the inspectors emit: a flat ``playing`` key and the
    nested ``playback_state.playing`` of a StateSnapshot. Returns False when
    the field is absent — an app that does not report playback status is
    treated as parked, since assuming otherwise would silently disable frame
    assertions for that app entirely.
    """
    if not isinstance(state, dict):
        return False
    playing = state.get("playing")
    if playing is None:
        playback = state.get("playback_state")
        if isinstance(playback, dict):
            playing = playback.get("playing")
    return bool(playing)


def _any_playing(states):
    """Names-free check: is any app's playhead currently running?"""
    return any(_playhead_is_playing(st) for st in states)


def _format_observed(states, app_names):
    """Render what every app actually reports, for logging beside an expectation.

    Both the pass and fail paths use this. A pass that says only "frame ~29"
    cannot be distinguished from a pass where every app sat at a coincidentally
    tolerable frame on the wrong clip, and a frame-mismatch message that omits
    the clip sends you digging through the recording to find out whether the
    playhead or the selection was the thing that went wrong. Print the observed
    values and the question answers itself.
    """
    parts = []
    for state, name in zip(states, app_names):
        if not isinstance(state, dict):
            parts.append(f"{name}=<no state>")
        elif "error" in state:
            parts.append(f"{name}=<error: {state['error']}>")
        else:
            # Labelled "timeline", not "clip": the state key is named `clip`
            # but both hooks populate it with the active *timeline* name
            # (see openrv_hook "first non-empty sequence" and xstudio_hook's
            # note that it is the timeline name when a timeline is on screen),
            # and validate_checkpoint compares it against the checkpoint's
            # `timeline_name`. Printing it as "clip" makes a `set_selection
            # car_ACES_sRGB.mov` look like it silently failed when the field
            # was never tracking clip selection in the first place.
            desc = f"{name}: frame={state.get('frame')} timeline={state.get('clip')!r}"
            # A seek assertion against a *playing* playhead can never converge:
            # both peers keep advancing and sample at different instants. When
            # the endpoint reports it, say so — it distinguishes "the seek did
            # not propagate" from "the seek propagated and then playback ran
            # away with it", which look identical as bare frame numbers.
            playing = state.get("playing")
            if playing is None:
                playback = state.get("playback_state")
                if isinstance(playback, dict):
                    playing = playback.get("playing")
            if playing is not None:
                desc += f" playing={playing}"
            # The clip actually on screen. Without it a matching frame across
            # two different shots is indistinguishable from real sync.
            media = _media_identity(state)
            desc += f" media={os.path.basename(media) if media else None!r}"
            # Sequence vs isolated-clip view. Frame numbers mean different
            # things in each, so a comparison across a view-mode split is not
            # comparing like with like.
            if state.get("view_mode") is not None:
                desc += f" view={state['view_mode']}"
            # Which peer owns visibility. A media/view split between two peers
            # means something different depending on who was allowed to set it:
            # the host's view is the session's, a follower's is local drift.
            if state.get("is_host") is not None:
                desc += " host" if state["is_host"] else " follower"
            if state.get("view_mirror_error"):
                desc += f" MIRROR-FAILED({state['view_mirror_error']})"
            # A *declined* instruction is reported but does not fail: declining
            # can be correct (a sequence-mode clip change tracks the playhead,
            # not a selection). What must never happen again is it being
            # invisible — "received the host's view and did nothing" read
            # exactly like "complied" for the six seconds a session stayed
            # diverged. Adopted/already-displayed are the normal case and stay
            # out of the line.
            _vo = state.get("view_outcome") or {}
            if _vo.get("outcome") == "declined":
                desc += f" VIEW-DECLINED({_vo.get('reason')})"
            if state.get("unresolved_patches"):
                desc += f" UNRESOLVED-PATCHES({len(state['unresolved_patches'])})"
            if state.get("unpublished_parents"):
                desc += f" UNPUBLISHED-PARENTS({len(state['unpublished_parents'])})"
            parts.append(desc)
    return " | ".join(parts) if parts else "<no apps>"


class FailKind:
    """Closed set of reasons a test can fail. Attached at the point a failure
    is raised (not inferred from message text later) so retry eligibility and
    run-history classification are always accurate — see the
    sync-tests-tracking change design doc, "fail_kind as a small closed enum".
    """
    STATE_MISMATCH = "state_mismatch"
    CHECKPOINT_TIMEOUT = "checkpoint_timeout"
    MISSING_MEDIA = "missing_media"
    LOG_ERROR_SIGNATURE = "log_error_signature"
    ANNOTATION_MISSING = "annotation_missing"
    STRUCTURAL_CONSENSUS = "structural_consensus"
    OTIO_EXPORT = "otio_export"
    #: A follower could not show the view the host reported. Distinct from
    #: state_mismatch: the peers may still *report* matching state, because the
    #: follower kept whatever was on screen. Waiting cannot fix it.
    VIEW_MIRROR_FAILED = "view_mirror_failed"
    #: Reserved. Unresolved patches are reported but do not fail a run — see
    #: the note in compare_states for why a *receiver* cannot distinguish a
    #: sender's bug from its own lag. Kept so run-history rows recorded while
    #: this briefly did fail still classify.
    UNRESOLVED_PATCH = "unresolved_patch"


#: fail_kinds for which waiting longer can plausibly change the outcome —
#: eligible for the bounded one-retry-at-2x described in design.md. The rest
#: (missing media, a known-bad log signature, an annotation that never
#: arrived even after the test's own settle time, an OTIO export mismatch)
#: cannot be fixed by waiting, so they fail immediately.
TIMING_ELIGIBLE_FAIL_KINDS = frozenset({
    FailKind.STATE_MISMATCH,
    FailKind.CHECKPOINT_TIMEOUT,
    FailKind.STRUCTURAL_CONSENSUS,
})


class TestResult:
    """Structured outcome of ``TestRunner.run_test`` — replaces the old bare
    ``bool`` return so failure kind, retry outcome, and convergence timing
    survive past the function instead of being logged and discarded.
    """
    def __init__(self, test_name, passed, fail_kind=None, message="",
                 converged_late=False, time_to_converge=0.0, recording=None,
                 duration=0.0):
        self.test_name = test_name
        self.passed = passed
        self.fail_kind = fail_kind
        self.message = message
        self.converged_late = converged_late
        self.time_to_converge = time_to_converge
        #: Wall-clock seconds for the entire run_test() call — app launch,
        #: teardown, and everything in between — not to be confused with
        #: time_to_converge (which measures a single check against its own
        #: deadline, not the whole test's runtime).
        self.duration = duration
        #: The `recording:` path from the test's yaml entry, or None for a
        #: script-driven test with no recording at all (pure `commands:` /
        #: fixture-only). Script-driven tests *with* a recording still derive
        #: their commands from it (see derive_commands_from_recording), so
        #: this is populated for those too.
        self.recording = recording


@contextlib.contextmanager
def _freeze_playback(player):
    """Freeze recording playback for the duration of a checkpoint validation.

    A checkpoint's expected value is only true for as long as the recording
    holds that point in its timeline; freezing stops the player thread's
    event dispatch so validation (which can itself take time — RPCs, a
    convergence poll) cannot race the recording forward past that window.
    See the freeze-recording-during-validation change proposal for the two
    traced failures this fixes.

    A context manager rather than paired pause()/resume() calls so resume is
    guaranteed on every exit path — normal, early ``break``, or exception —
    since a leaked freeze would hang the whole test run. No-op when *player*
    is ``None`` (script-driven tests have no recording playing during their
    assertions, so there is nothing to freeze).
    """
    if player is None:
        yield
        return
    freeze_start = time.time()
    player.pause()
    try:
        yield
    finally:
        player.resume()
        logging.info(
            f"⏸  Playback frozen {time.time() - freeze_start:.1f}s for checkpoint validation"
        )


def _poll_until(check_fn, deadline_seconds, poll_interval=0.5, label=None):
    """Poll ``check_fn() -> (ok, msg)`` until it passes or *deadline_seconds*
    elapses. Returns ``(ok, msg, elapsed_seconds)``.

    Pass *label* to have the waiting reported. Without it this polls in
    silence, so a check that quietly retried for seconds is indistinguishable
    in the log from one that passed or failed outright — the elapsed time only
    shows up afterwards, in a trailing ``[waited N.Ns]``. Frame checkpoints
    announce their retries; anything using this should too.
    """
    start = time.time()
    ok, msg = check_fn()
    if ok or deadline_seconds <= 0:
        return ok, msg, time.time() - start

    if label:
        logging.warning(
            f"{label} did not match on first check — polling up to "
            f"{deadline_seconds:.1f}s for convergence before failing."
        )
    while not ok and (time.time() - start) < deadline_seconds:
        time.sleep(poll_interval)
        ok, msg = check_fn()
    elapsed = time.time() - start
    if label:
        logging.info(
            f"{label} {'converged' if ok else 'did NOT converge'} "
            f"after {elapsed:.1f}s of polling."
        )
    return ok, msg, elapsed


class TestRunner:
    def __init__(self, config_path="sync_tests.yaml"):
        self.config_path = config_path
        self.config = SyncTestConfig.from_file(config_path)

    def _resolve_git_sha(self):
        """Current repo HEAD, or None if unavailable — tolerates a dirty or

        detached worktree (``git rev-parse HEAD`` works in both) and a
        missing git binary alike, since a run-history entry is still worth
        recording without it.
        """
        try:
            repo_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..")
            )
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo_root,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _history_path(self):
        return os.path.join(
            os.path.dirname(os.path.abspath(self.config_path)), "run_history.jsonl"
        )

    def load_history(self):
        """Read ``run_history.jsonl`` (if it exists) into ``{test_name: [entries...]}``,

        each test's entries ordered oldest-to-newest. Used to show prior
        results in the run summary — call this *before* running new tests
        so it reflects only prior runs, not ones about to be appended.
        """
        by_test = {}
        path = self._history_path()
        if not os.path.exists(path):
            return by_test
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    by_test.setdefault(entry.get("test"), []).append(entry)
        except OSError:
            pass
        return by_test

    def _write_history_entry(self, result):
        """Append one entry to ``sync_test/run_history.jsonl`` for every
        completed run, pass or fail — see the sync-tests-tracking change
        design doc, "Run-history format: git-tracked JSON Lines, one file".
        """
        history_path = self._history_path()
        entry = {
            "test": result.test_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_sha": self._resolve_git_sha(),
            "result": "pass" if result.passed else "fail",
            "fail_kind": result.fail_kind,
            "converged_late": result.converged_late,
            "time_to_converge": round(result.time_to_converge, 3),
            "recording": result.recording,
            "duration": round(result.duration, 3),
        }
        try:
            with open(history_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logging.warning(f"Could not write run history entry: {e}")

    def fetch_state(self, port):
        url = f"http://127.0.0.1:{port}/state"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as response:
                data = response.read()
                return json.loads(data.decode('utf-8'))
        except Exception as e:
            return {"error": str(e)}

    def fetch_full_state(self, port):
        """Fetch a client's StateSnapshot-shaped full state from /full_state.

        Returns an ``{"error": ...}`` dict on transport failure or if the
        inspector does not support full state (HTTP 501).
        """
        url = f"http://127.0.0.1:{port}/full_state"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5.0) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            return {"error": str(e)}

    def send_command(self, port, payload):
        url = f"http://127.0.0.1:{port}/command"
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode('utf-8'), method='POST'
            )
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=35.0) as response:
                data = response.read()
                body = json.loads(data.decode('utf-8'))
                # The inspector wraps results in {"status": "ok", "result": ...}.
                # Surface any error the command handler returned so the runner
                # can detect it the same way it detects HTTP-level failures.
                inner = body.get("result", {}) if isinstance(body, dict) else {}
                if isinstance(inner, dict) and inner.get("status") == "error":
                    return {"error": inner.get("error", "unknown command error")}
                return body
        except Exception as e:
            return {"error": str(e)}

    def compare_states(self, states, app_names):
        """Returns ``(passed, message, fail_kind)``. ``fail_kind`` is only

        meaningful when ``passed`` is False: ``FailKind.MISSING_MEDIA`` for a
        confirmed-absent media file (never resolved by waiting longer), or
        ``FailKind.STATE_MISMATCH`` for a transient app error or a live
        structural/clip mismatch (both retry-eligible — an app error can be a
        momentary RPC hiccup, and a live mismatch may still converge).
        """
        if len(states) < 2:
            return True, "", None

        base_state = states[0]
        if "error" in base_state:
            return False, f"{app_names[0]} returned error: {base_state['error']}", FailKind.STATE_MISMATCH

        if "media_exists" in base_state and not base_state["media_exists"]:
            return False, f"{app_names[0]} reports missing media: {base_state.get('media_path')}", FailKind.MISSING_MEDIA

        # A follower that could not mirror the host's view is a failure even
        # when every reported field matches: it kept showing what it already
        # had, so "same timeline name, different clip" reads as agreement. The
        # apps report the failure rather than substituting a local best guess,
        # so the harness only has to notice it.
        for name, st in zip(app_names, states):
            mirror_error = st.get("view_mirror_error")
            if mirror_error:
                return (
                    False,
                    f"{name} could not mirror the host's view: {mirror_error}",
                    FailKind.VIEW_MIRROR_FAILED,
                )
            # Unresolved patches are reported in the observed line and in
            # /state, but deliberately do NOT fail the run.
            #
            # A receiving peer cannot tell "the sender broadcast against
            # something it never published" from "I have not caught up yet".
            # Both look identical: a parent GUID it does not hold. Sessions
            # routinely produce a few during establishment — a peer that
            # self-elects master reaches STATE_SYNCED holding no timelines at
            # all, so even "has it joined?" does not separate them — and the
            # suite showed three otherwise-green annotation tests tripping on
            # exactly that. Failing here turned a normal startup event into a
            # red suite.
            #
            # The check that CAN be enforced belongs at the sender, which
            # always knows whether it published a parent (design.md D2, §5).

        for i in range(1, len(states)):
            st = states[i]
            if "error" in st:
                return False, f"{app_names[i]} returned error: {st['error']}", FailKind.STATE_MISMATCH

            if "media_exists" in st and not st["media_exists"]:
                return False, f"{app_names[i]} reports missing media: {st.get('media_path')}", FailKind.MISSING_MEDIA

            # Ignore transient states like playing or absolute path strings.
            # Annotation fields differ in representation per app (RV stroke
            # components vs xStudio bookmarks) and are checked separately by the
            # annotation-presence check, so exclude them from structural equality.
            # `view_mode` is excluded deliberately, for the same reason
            # `_verify_frame_sync` reports a sequence/isolated-clip split as a
            # warning rather than failing on it: the split is real and worth
            # seeing, but it is not by itself proof of desync, and /state does
            # not expose enough (a clip's offset within its sequence) to tell
            # the harmful case from the harmless one. It is reported in the
            # observed line of every check instead. Omitting it here would make
            # it a silent structural-equality criterion — which is exactly what
            # happened when the field was first added, turning a known,
            # tolerated split into `state_mismatch` failures on tests that had
            # never failed.
            # `is_host`/`host_guid` differ between peers by construction — one
            # peer holds visibility authority and the others do not — so they
            # are reported for assertions, never for structural equality.
            # `broadcast_ownership` is the same story one level down: peers
            # converge on who owns each lease channel, but `remaining_ms`
            # differs by network latency and local clock reading, so it is
            # never a structural-equality criterion either.
            # `view_mirror_error` is checked explicitly above.
            # `view_outcome` is per-peer by construction — only a follower
            # receives view instructions at all, and the record carries a
            # wall-clock `at`, so structural equality on it would fail every
            # comparison. It is reported in the observed line and available to
            # assertions instead.
            ignore_keys = {"playing", "media_path", "media_exists", "frame",
                           "view_mode",
                           "annotations", "annotation_count", "is_master",
                           "is_host", "host_guid", "view_mirror_error",
                           "view_outcome",
                           "unresolved_patches", "unpublished_parents",
                           "media_count", "broadcast_ownership"}
            s1 = {k: v for k, v in base_state.items() if k not in ignore_keys}
            s2 = {k: v for k, v in st.items() if k not in ignore_keys}

            if s1.get("clip") and s2.get("clip"):
                c1 = _normalize_clip_name(s1["clip"])
                c2 = _normalize_clip_name(s2["clip"])
                if c1 == c2:
                    s1["clip"] = s2["clip"]

            if s1 != s2:
                diff_msg = f"Mismatch between {app_names[0]} and {app_names[i]}:\n"
                diff_msg += f"{app_names[0]}: {json.dumps(s1)}\n"
                diff_msg += f"{app_names[i]}: {json.dumps(s2)}\n"
                return False, diff_msg, FailKind.STATE_MISMATCH

        return True, "", None

    def validate_checkpoint(self, states, app_names, checkpoint):
        """Check each app's reported state against a recording checkpoint.

        Only validates fields the app exposes (frame may be None for some apps).

        A frame is only comparable when the playhead is parked. If an app
        reports that it is playing, its frame is changing continuously and no
        single value is the "right" one — comparing anyway produces a mismatch
        that says nothing about sync, and polling for convergence cannot help
        because both peers keep moving. Such apps are excluded from the frame
        comparison; callers detect the situation with :func:`_any_playing` and
        report it rather than treating the result as a clean pass.

        Returns (passed, reason_string).
        """
        expected_frame = checkpoint.get("frame")
        expected_clip = checkpoint.get("timeline_name")
        frame_tolerance = checkpoint.get("frame_tolerance", 5)
        messages = []

        for state, name in zip(states, app_names):
            if "error" in state:
                return False, f"{name} returned error at checkpoint: {state['error']}"

            actual_frame = state.get("frame")
            actual_clip = state.get("clip")
            if _playhead_is_playing(state):
                # Playing: the frame is a moving target, so there is nothing
                # meaningful to assert. Skip it rather than diff against a
                # value that was already stale when it was sampled.
                pass
            elif expected_frame is not None and actual_frame is not None:
                # RV frame() is 1-indexed; PLAYBACK_SETTINGS value is 0-indexed
                adjusted = int(expected_frame) + 1
                if abs(actual_frame - adjusted) > frame_tolerance:
                    # Name the timeline the wrong frame was read on. A playhead
                    # sitting on the wrong timeline and a playhead that failed
                    # to follow a seek produce the same bare frame number, and
                    # they are entirely different faults.
                    messages.append(
                        f"{name}: expected frame ~{adjusted}, got {actual_frame}"
                        f" (on timeline {actual_clip!r})"
                    )

            if expected_clip and actual_clip is not None:
                if _normalize_clip_name(actual_clip) != _normalize_clip_name(expected_clip):
                    messages.append(
                        f"{name}: expected timeline '{expected_clip}', "
                        f"got '{actual_clip}'"
                    )

        if messages:
            t = checkpoint.get("time_offset", 0)
            # Always show every app, not just the mismatching one: whether the
            # peers agree with each other separates "one app is wrong" from
            # "the expectation is wrong".
            return False, (
                f"Checkpoint at t={t:.1f}s failed:\n" + "\n".join(messages)
                + f"\n  observed: {_format_observed(states, app_names)}"
            )
        return True, ""

    def _verify_frame_sync(self, app_ports, frame, frame_tolerance, deadline, label):
        """Poll until every app's playhead reports *frame*, or *deadline* elapses.

        A ``set_frame`` is a discrete seek, not playback: once every app has
        applied it the value is parked and cannot drift, so a mismatch here is
        a real convergence failure rather than a sampling artefact. That makes
        each seek independently checkable, which is why this is factored out
        of the single trailing shuttle check.

        :returns: ``(ok, message, elapsed_seconds, last_observed_states)``
        """
        checkpoint = {
            "time_offset": 0.0,
            "frame": frame,
            "timeline_name": None,
            "frame_tolerance": frame_tolerance,
        }
        names = [a[0] for a in app_ports]
        seen = {}

        def _check():
            states = [self.fetch_state(port) for _, port in app_ports]
            seen["states"] = states
            # A seek is supposed to park the playhead. If an app is still
            # playing, the frame is unassertable — and because
            # validate_checkpoint skips playing apps, letting this through
            # would report a clean pass having compared nothing. Keep polling
            # (playback may be about to stop) and, if it never parks, say so
            # instead of emitting a frame mismatch that misdescribes the fault.
            playing = [n for st, n in zip(states, names) if _playhead_is_playing(st)]
            if playing:
                return False, (
                    f"playback still active on {', '.join(playing)} — a seek must "
                    "park the playhead, so no frame assertion is possible\n"
                    f"  observed: {_format_observed(states, names)}"
                )
            # Same frame on different clips is not sync. Establish that the
            # apps are looking at the same media before believing any frame
            # comparison between them — otherwise a "passing" seek check can
            # be comparing frame 61 of one shot against frame 61 of another.
            disagreement = _media_disagreement(states, names)
            if disagreement:
                return False, (
                    f"apps are showing different media ({disagreement}) — a frame "
                    "comparison between them is meaningless until they agree\n"
                    f"  observed: {_format_observed(states, names)}"
                )
            # A sequence/isolated-clip split is reported but NOT failed on.
            # In principle frame indices are incomparable across it — one
            # counts from the start of the sequence, the other from the start
            # of the clip. In practice they coincide whenever the isolated clip
            # is the sequence's first, and /state does not expose the clip's
            # offset within the sequence, so we cannot tell the harmful case
            # from the harmless one here. The media check above already blocks
            # the genuinely dangerous comparison (different clips entirely).
            # Surfaced as a warning because the split may still be a real view
            # desync worth chasing on its own.
            view_split = _view_mode_disagreement(states, names)
            # Once per check, not once per poll: this is a standing condition,
            # so re-reporting it every 0.5s buries the actual result.
            if view_split and view_split != seen.get("warned_split"):
                seen["warned_split"] = view_split
                logging.warning(
                    f"View-mode split during {label}: {view_split} — frame "
                    "comparison proceeding (same media), but the apps are not "
                    "showing the same thing."
                )
            return self.validate_checkpoint(states, names, checkpoint)

        ok, msg, elapsed = _poll_until(_check, deadline, label=label)
        return ok, msg, elapsed, seen.get("states", [])

    def compare_full_states(self, full_states, app_names, frame_tolerance=5,
                            compare_frame=False):
        """Consensus check: every full-state-capable client must agree structurally.

        Projects each client's ``/full_state`` and diffs the others against the
        first valid one.  Clients lacking full state are skipped.

        :returns: ``(consistent, reason_string)``.
        """
        projected = [
            (name, project_state(st))
            for st, name in zip(full_states, app_names)
            if isinstance(st, dict) and "error" not in st
        ]
        if len(projected) < 2:
            return True, ""
        base_name, base = projected[0]
        messages = []
        for name, proj in projected[1:]:
            # Frame compared only when the caller knows the playhead is parked
            # (a frame-held checkpoint). Mid-playback, full_state frames are not
            # comparable across apps — xStudio's arrives via the ~0.5s file
            # bridge while OpenRV's is live, so a moving playhead reads frames
            # apart even when in sync.
            for d in diff_states(base, proj, frame_tolerance, compare_frame=compare_frame):
                messages.append(f"{base_name} vs {name}: {d}")
        if messages:
            return False, "Client consensus mismatch:\n" + "\n".join(messages)
        return True, ""

    def validate_state_checkpoint(self, full_states, app_names, checkpoint,
                                  frame_tolerance=5):
        """Structurally validate each client's full state at a state checkpoint.

        Each client's ``/full_state`` is projected and diffed (GUID-keyed)
        against the checkpoint's expected projection (the recorded snapshot).
        Clients that do not support full state (``error``) are skipped so a
        recording can still validate the apps that do.

        :param full_states: List of ``/full_state`` dicts, aligned with *app_names*.
        :param app_names: App names aligned with *full_states*.
        :param checkpoint: A state checkpoint from :func:`derive_state_checkpoints`.
        :param frame_tolerance: Allowed absolute frame difference.
        :returns: ``(passed, reason_string)``.
        """
        expected = checkpoint["expected"]
        messages = []
        validated_any = False
        for state, name in zip(full_states, app_names):
            if not isinstance(state, dict) or "error" in state:
                # Inspector lacks full-state support or transient failure; skip.
                logging.debug(f"state checkpoint: skipping {name}: "
                              f"{state.get('error') if isinstance(state, dict) else state}")
                continue
            validated_any = True
            # Compare frame only at frame-held checkpoints: with a moving
            # playhead the snapshot's frame is a stale point-in-time and the
            # clients read inconsistently. When parked, the frame is reliable.
            diffs = diff_states(expected, project_state(state), frame_tolerance,
                                compare_frame=checkpoint.get("frame_held", False))
            for d in diffs:
                messages.append(f"{name}: {d}")

        if messages:
            t = checkpoint.get("time_offset", 0)
            return False, f"State checkpoint at t={t:.1f}s failed:\n" + "\n".join(messages)
        # Nothing validated (no app exposed full state) is not a failure.
        return True, ("" if validated_any else "no full-state-capable apps")

    def _wait_for_all_apps(self, app_ports, timeout=90.0):
        """Poll each app's /state endpoint until all respond without error."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all("error" not in self.fetch_state(port) for _, port in app_ports):
                return True
            time.sleep(1.0)
        return False

    def _wait_for_snapshot(self, app_ports, timeout=30.0):
        """Wait until every app reports a non-null clip (STATE_SNAPSHOT received)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            states = [self.fetch_state(port) for _, port in app_ports]
            if all("error" not in st and st.get("clip") is not None for st in states):
                return True
            time.sleep(1.0)
        return False

    def _wait_for_master(self, port, timeout=15.0):
        """Poll a single app's /state until it reports ``is_master: true``.

        Structural edits (add_media, selection, ...) are only ever broadcast
        by whichever peer holds master (see ``sequence_sync.py``'s
        ``check_otio_snapshots`` gate) — a non-master peer's own local edits
        are silently dropped, never queued for later broadcast. Script-driven
        tests that drive an app's structural commands must not assume launch
        order settles this: apps self-promote to master on different
        timescales (xStudio tends to claim it immediately; OpenRV waits ~2s
        for a WHO_IS_MASTER reply before self-promoting, and can take longer
        than that to even finish booting and connecting). Returns False (not
        an exception) on timeout so callers can log a clear diagnostic instead
        of a generic downstream state-mismatch failure.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.fetch_state(port)
            if "error" not in st and st.get("is_master") is True:
                return True
            time.sleep(0.5)
        return False

    def _wait_for_media(self, app_ports, timeout=20.0):
        """Poll every app's ``/state`` until all of them report loaded media.

        An annotation names the clip it belongs to, and a peer that has not yet
        materialised that clip **drops it permanently** — xStudio's
        ``apply_remote_annotation`` returns early when
        ``media_for_sync_guid`` misses, and nothing re-delivers it (the retry
        machinery in that module is for outgoing broadcasts only).

        Nothing about that is a race the *product* loses in practice: a person
        cannot annotate media they cannot see. It is the script that annotates
        1.4s after adding media. The harness used to cover the gap with the
        flat ``time.sleep(1.0)`` between commands, which is a fixed budget for
        work whose measured cost varies far more than that — ``load_otio``
        alone ranged 0.04s→1.23s across one suite — so the margin held in
        isolation and vanished under full-suite load. That is why the
        annotation tests failed only in the suite and never on their own.

        Waits on *every* peer, not just the driver: the driver is the one app
        guaranteed to have the media already, so asking it alone would answer
        the wrong question.

        Returns False rather than raising so the caller can log a specific
        diagnostic instead of failing later as a generic state mismatch —
        same contract as :meth:`_wait_for_master`.

        :param app_ports: ``(name, port)`` tuples for every app in the test.
        :param timeout: Seconds to wait before giving up.
        :returns: True once all apps report media, False on timeout.
        """
        deadline = time.time() + timeout
        pending = []
        while time.time() < deadline:
            pending = []
            for name, port in app_ports:
                st = self.fetch_state(port)
                if "error" in st or not st.get("media_count"):
                    pending.append(name)
            if not pending:
                return True
            time.sleep(0.25)
        logging.warning(
            f"_wait_for_media: {', '.join(pending)} still report no media after "
            f"{timeout:.0f}s — an annotation sent now would be dropped silently."
        )
        return False

    def _wait_for_host_failover(self, app_ports, timeout=30.0):
        """Wait for a surviving peer to hold visibility authority.

        Called after a peer has been removed mid-test.  Until the survivors drop
        the departed peer from their peer tables, election keeps returning it —
        it is still a candidate — and because only the host may broadcast
        visibility, the session's view stays frozen with nobody able to move it.

        An unclean exit sends no departure notice, so this necessarily waits out
        the liveness timeout.  The default budget is generous relative to it: a
        tight bound here would turn a slow-but-working failover into a failure,
        and this repo has already produced false diagnoses from machine latency
        read as a protocol fault.

        :returns: ``True`` once some surviving app reports ``is_host`` and every
            app agrees on the same ``host_guid``.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            states = [(app, self.fetch_state(app[1])) for app in app_ports]
            live = [(app, st) for app, st in states if "error" not in st]
            guids = {st.get("host_guid") for _, st in live}
            if live and len(guids) == 1 and None not in guids:
                for app, st in live:
                    if st.get("is_host") is True:
                        logging.info(
                            f"     {app[0]} took visibility authority "
                            f"({time.time() - (deadline - timeout):.1f}s)"
                        )
                        return True
            time.sleep(0.5)
        return False

    def _wait_for_ownership_convergence(self, app_ports, channel, timeout=15.0):
        """Wait for every live peer to agree on the owner of a broadcast-ownership channel.

        Companion to :meth:`_wait_for_host_failover`, same shape: polls every
        peer's ``/state`` (which carries ``broadcast_ownership`` per-channel,
        see the openrv/xstudio hooks) until they all report the same
        ``owner_guid`` for *channel*. Used after a deliberately-contended
        command (:meth:`_send_concurrent_commands`) to confirm the lease
        settles on one peer rather than staying split — the property
        broadcast-ownership exists to guarantee (design.md D2).

        :param app_ports: ``[(name, port), ...]`` for every peer in the test.
        :param channel: ``"position"``, ``"display"``, or ``"structure"``.
        :param timeout: Seconds to wait before giving up.
        :returns: The agreed owner GUID, or ``None`` if the peers never
            converge within *timeout* (each still reports a different owner,
            or some report no owner at all).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            states = [(app, self.fetch_state(app[1])) for app in app_ports]
            live = [(app, st) for app, st in states if "error" not in st]
            owners = {
                (st.get("broadcast_ownership") or {}).get(channel, {}).get("owner_guid")
                for _, st in live
            }
            if live and len(owners) == 1 and None not in owners:
                owner = next(iter(owners))
                logging.info(
                    f"     {channel} lease converged on {owner[:8]} "
                    f"({time.time() - (deadline - timeout):.1f}s)"
                )
                return owner
            time.sleep(0.25)
        logging.warning(
            f"_wait_for_ownership_convergence: peers never agreed on a {channel} "
            f"owner within {timeout:.0f}s"
        )
        return None

    def _send_concurrent_commands(self, by_app_commands, app_ports):
        """Send one command to each named app at (as close to) the same instant.

        Fires every ``send_command`` call from its own thread so the requests
        overlap on the wire instead of the usual command loop's one-at-a-time,
        ``sleep(1.0)``-between ordering — that ordering would hand each
        broadcast-ownership lease to whichever peer happened to go first,
        which proves nothing about contention. Genuine overlap is what
        exercises the deterministic-tiebreak/transfer path (design.md D2/D3)
        rather than a race that never actually happens.

        :param by_app_commands: ``{app_name: command_dict}``.
        :param app_ports: ``[(name, port), ...]`` for every peer in the test.
        :returns: ``{app_name: response_dict}``, the same shape
            :meth:`send_command` returns per app.
        """
        port_by_name = {name: port for name, port in app_ports}
        results = {}
        lock = threading.Lock()

        def _fire(name, cmd):
            res = self.send_command(port_by_name[name], cmd)
            with lock:
                results[name] = res

        threads = [
            threading.Thread(target=_fire, args=(name, cmd))
            for name, cmd in by_app_commands.items()
            if name in port_by_name
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=35.0)
        return results

    def _select_host_driver(self, app_ports):
        """Return the app that holds visibility authority, or ``None``.

        Selection commands assert *visibility*, which only the elected host may
        broadcast — a follower's are stripped in ``SyncManager.broadcast_*``, so
        driving the wrong peer produces a test that silently asserts nothing.
        This is the same hazard ``_wait_for_master`` covers for structural edits,
        but it cannot be fixed by waiting: host election is a deterministic
        function of the peer set, so a driver that is not host never becomes one.

        It matters only where the peers are equally ranked. With xStudio in the
        session xStudio always hosts, so the driver is already right; in an
        OpenRV-only session the tie breaks on a random per-launch GUID, making
        "is apps[0] the host?" a coin flip. Ask rather than assume.

        Waits for every app to agree on one ``host_guid`` before answering, and
        does not accept the first app that says ``is_host``. Election is a pure
        function of the peer table, so a peer that is briefly alone elects
        *itself* and only yields once the other peer's announcement arrives —
        sampling before the set settles returns an answer that is true for a
        few hundred milliseconds and wrong for the rest of the test.

        :returns: The winning ``(name, port)`` tuple, or ``None`` if the apps do
            not converge on a host within the timeout.
        """
        deadline = time.time() + 15.0
        while time.time() < deadline:
            states = [(app, self.fetch_state(app[1])) for app in app_ports]
            guids = {
                st.get("host_guid")
                for _, st in states
                if "error" not in st
            }
            # One agreed, non-null host across every app: the set has settled.
            if len(states) == len(app_ports) and len(guids) == 1 and None not in guids:
                for app, st in states:
                    if st.get("is_host") is True:
                        return app
            time.sleep(0.5)
        return None

    _ANNOTATION_GEOMETRY_FORMULAS = {
        ("pen", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_pen_width,
        ("pen", "xstudio_to_openrv"): annotation_assertions.expected_rv_width_from_xstudio_pen_thickness,
        ("rect", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_border_width,
        ("ellipse", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_ellipse_border_width,
        ("arrow", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_arrow_thickness,
        ("text", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_font_size_from_rv_size,
    }

    def _verify_annotation_geometry(self, app_ports, cfg):
        """Verify a `draw_annotation` round-tripped to the peer within tolerance.

        `cfg` (from the test's `annotation_geometry` yaml block) names the
        `driver`/`peer` apps by their `apps:` list name, the annotation `kind`
        (`pen`/`rect`), the `nominal` value the driver was asked to draw, and a
        `tolerance`. The expected peer-side value is computed from the same
        production codec constants the apps themselves use (see
        `annotation_assertions`), not a hardcoded number — see the
        `sync-test-draw-annotation` change design doc, decision D3/D4.
        """
        driver_name = cfg["driver"]
        peer_name = cfg["peer"]
        kind = cfg.get("kind", "pen")
        nominal = float(cfg["nominal"])
        tolerance = float(cfg.get("tolerance", 1e-4))
        direction = f"{driver_name}_to_{peer_name}"

        formula = self._ANNOTATION_GEOMETRY_FORMULAS.get((kind, direction))
        if formula is None:
            return False, f"No round-trip formula for kind={kind!r} direction={direction!r}"
        expected = formula(nominal)

        peer_port = next((port for name, port in app_ports if name == peer_name), None)
        if peer_port is None:
            return False, f"No app named {peer_name!r} in this test's apps list"

        def fetch_peer_state():
            return self.fetch_state(peer_port)

        def has_geometry(state):
            return bool(state.get("annotations"))

        state = annotation_assertions.wait_for_predicate(
            fetch_peer_state, has_geometry,
            timeout=annotation_assertions.XSTUDIO_ANNOTATION_CONVERGENCE_TIMEOUT,
        )
        if not state or not state.get("annotations"):
            return False, f"{peer_name} reported no annotations before timeout"

        last = state["annotations"][-1]
        if peer_name == "xstudio":
            if kind == "text":
                values = last.get("caption_font_size") or []
            else:
                values = last.get("stroke_thickness") or []
            actual = values[-1] if values else None
        else:
            actual = last.get("width") if kind == "pen" else last.get("size")
            if isinstance(actual, list):
                actual = actual[-1] if actual else None

        try:
            annotation_assertions.assert_almost_equal(
                actual, expected, tolerance=tolerance,
                msg=f"{peer_name} {kind} geometry round-trip from {driver_name}",
            )
        except AssertionError as e:
            return False, str(e)

        # Text also carries a position, checked as an additive assertion
        # within the same round-trip config block rather than a parallel
        # verification path (see design D5) — only exercised when the yaml
        # `annotation_geometry` block opts in with a `position` field.
        position_cfg = cfg.get("position")
        if position_cfg is not None and kind == "text" and peer_name == "xstudio":
            expected_pos = annotation_assertions.expected_xstudio_caption_position_from_rv_position(
                tuple(float(v) for v in position_cfg)
            )
            positions = last.get("caption_position") or []
            actual_pos = positions[-1] if positions else None
            if actual_pos is None:
                return False, f"{peer_name} reported no caption position"
            for axis, (actual_axis, expected_axis) in enumerate(zip(actual_pos, expected_pos)):
                try:
                    annotation_assertions.assert_almost_equal(
                        actual_axis, expected_axis, tolerance=tolerance,
                        msg=f"{peer_name} {kind} position[{axis}] round-trip from {driver_name}",
                    )
                except AssertionError as e:
                    return False, str(e)

        return True, ""

    def _capture_and_measure(self, app_ports, app_name, cfg, kind, geometry, color, otio_thickness, logs_dir):
        """Capture `app_name`'s live frame and measure its rendered border against
        `otio_thickness`/`geometry`. Returns `(ok, msg)`, mirroring `_verify_visual_check`.
        """
        from . import visual_geometry

        port = next((p for name, p in app_ports if name == app_name), None)
        if port is None:
            return False, f"No app named {app_name!r} in this test's apps list"

        # Naming convention per design D3 (`capture_<app_name>_<port>_<frame>.png`),
        # consistent with the existing per-test session-dump artifacts
        # (`openrv_<port>.rv`, `xstudio_<port>.xst`) saved into `logs_dir`.
        state = self.fetch_state(port)
        frame = state.get("frame") if isinstance(state, dict) else None
        frame_label = frame if frame is not None else "current"
        capture_path = os.path.join(logs_dir, f"capture_{app_name}_{port}_{frame_label}.png")
        res = self.send_command(port, {
            "action": "capture_frame",
            "output_path": capture_path,
            "width": int(cfg.get("capture_width", 1920)),
            "height": int(cfg.get("capture_height", 1080)),
        })
        if "error" in res:
            return False, f"{app_name} capture_frame failed: {res['error']}"

        result = visual_geometry.measure_shape_border(
            capture_path, kind, geometry, color, otio_thickness
        )
        if not result["found"]:
            return False, (
                f"{app_name}: no annotation-colored ink found near expected "
                f"geometry in {capture_path}"
            )

        # Antialiased/soft-edged strokes (pen, and to a lesser extent shape
        # borders) have a Gaussian-equivalent measured width that runs
        # proportionally larger than their nominal declared thickness — the
        # same effect `testchart/compare_thickness.py` already reports as
        # normal for xStudio's own rendering (e.g. a ~1.19x scale factor on
        # solid lines), not something specific to this comparison. A fixed
        # pixel tolerance that comfortably covers thin shape borders (~5-10px)
        # is too tight for thick strokes (observed up to ~21% high on a pen
        # stroke); scale the tolerance with the expected thickness itself,
        # floored at the configured/default absolute tolerance so thin
        # borders keep a tight absolute check.
        tolerance_px = max(
            float(cfg.get("tolerance_px", 4.0)),
            0.3 * result["expected_thickness_px"],
        )
        msg = (
            f"{app_name}: expected {result['expected_thickness_px']:.2f}px, "
            f"measured {result['measured_thickness_px']:.2f}px "
            f"(offset {result['offset_px']:+.2f}px, "
            f"centroid offset {result['centroid_offset_px']:.2f}px) — {capture_path}"
        )
        if abs(result["offset_px"]) > tolerance_px:
            return False, f"{app_name} rendered border thickness mismatch: {msg}"
        return True, msg

    def _verify_visual_check(self, app_ports, cfg, draw_cmd, logs_dir):
        """Capture *both* the driver's and the peer's live frame and check the
        annotation is actually rendered where/how thick the driver's own
        `draw_annotation` geometry says it should be — the stronger, additive
        check the numeric `annotation_geometry` round-trip cannot make (see
        the `sync-test-frame-capture` change design doc, decision D4).
        Capturing the driver too (not just the peer) means both apps' PNGs
        land in `logs_dir` for inspection, and exercises both hosts'
        `capture_frame` implementations, not just the peer's — the two are
        genuinely different code paths (xStudio's direct render API vs
        OpenRV's in-process Qt grab). Supports `pen`/`rect`/`ellipse`/`arrow`
        — every `draw_annotation` kind has a straight-line cross-section to
        sample (a pen stroke's own thickness, in a pen's case).

        Soft-imports `visual_geometry` (needs PIL/numpy) and returns a passing
        result with an explanatory message if unavailable in this interpreter,
        rather than failing the whole test over an optional dependency (see
        design Risk: PIL/numpy availability).
        """
        try:
            from . import visual_geometry  # noqa: F401 (availability check only)
        except ImportError as e:
            return True, f"visual check skipped (PIL/numpy unavailable: {e})"

        driver_name = cfg["driver"]
        peer_name = cfg["peer"]
        kind = cfg.get("kind", "pen")
        nominal = float(cfg["nominal"])

        # Geometry is driver-dependent for `pen`: xStudio's native stroke
        # coordinates need an aspect_half conversion RV's raw paint
        # coordinates don't (see `shape_geometry_for_driver`).
        geometry = annotation_assertions.shape_geometry_for_driver(kind, driver_name)
        if geometry is None:
            return False, f"visual check: no supported geometry for kind={kind!r} driver={driver_name!r}"

        # Both apps are expected to render the *same* OTIO-normalized geometry
        # (that's the whole point of the shared coordinate space), so the same
        # otio_thickness/geometry ground truth applies to the driver's own
        # frame and the peer's — there's no separate "driver formula".
        otio_thickness = annotation_assertions.otio_size_from_driver_nominal(
            kind, driver_name, nominal
        )
        if otio_thickness is None:
            return False, f"visual check: no OTIO-size formula for kind={kind!r} driver={driver_name!r}"

        color = (draw_cmd or {}).get("border_rgba") or (draw_cmd or {}).get("color") or [1.0, 1.0, 1.0, 1.0]

        messages = []
        for app_name in (driver_name, peer_name):
            ok, msg = self._capture_and_measure(
                app_ports, app_name, cfg, kind, geometry, color, otio_thickness, logs_dir
            )
            messages.append(msg)
            if not ok:
                return False, "; ".join(messages)
        return True, "; ".join(messages)

    def run_test(self, test_name, script_driven=False,
                 checkpoint_validation_delay=1.5,
                 checkpoint_min_spacing=2.0,
                 frame_tolerance=5,
                 test_index=None, test_total=None):
        test_start_time = time.time()
        if SyncPlayer is None:
            raise RuntimeError("Cannot import sync_recorder.player.SyncPlayer")

        test_data = self.config.get_test(test_name)
        if not test_data:
            logging.error(f"Test '{test_name}' not found in configuration.")
            result = TestResult(
                test_name, False, message="test not found in configuration",
                duration=time.time() - test_start_time,
            )
            self._write_history_entry(result)
            return result

        apps = test_data['apps']
        script_driven = script_driven or test_data.get('script_driven', False)
        recording = test_data.get('recording')
        recording_path = (
            os.path.join(os.path.dirname(self.config_path), recording)
            if recording else None
        )

        # Annotations only flow in playback (non-script) mode. Script-driven runs
        # replay *derived media commands* (add/delete media), never the
        # recording's Annotation.1 events — so a recording that merely happens to
        # contain annotations must not make the presence check expect them.
        expect_annotations = (
            (not script_driven) and recording_path is not None
            and recording_has_annotations(recording_path)
        )

        # Allow per-test overrides for checkpoint tuning
        checkpoint_validation_delay = test_data.get('checkpoint_validation_delay', checkpoint_validation_delay)
        checkpoint_min_spacing = test_data.get('checkpoint_min_spacing', checkpoint_min_spacing)
        frame_tolerance = test_data.get('frame_tolerance', frame_tolerance)

        progress = f"  [{test_index}/{test_total}]" if test_index and test_total else ""
        print(f"\n{'='*70}")
        print(f"  ▶ RUNNING TEST: {test_name}{progress}")
        print(f"{'='*70}")
        logging.info(f"Starting test '{test_name}' with apps: {apps}")
        if recording:
            logging.info(f"Reading from recording: {recording}")
        else:
            logging.info("No recording — script-driven via explicit commands/fixtures.")

        executables = self.config.settings.get('executables', {})
        openrv_args = self.config.settings.get('openrv_args', [])
        # Unique session per test so each test runs on its own RabbitMQ exchange —
        # isolates the broker so leftover state/peers from a prior test cannot
        # leak in (the cause of suite-only flakiness).
        session_id = f"otio-sync-{test_name}-{uuid.uuid4().hex[:8]}"
        with AppSpawner(test_name, executables, session_id=session_id, openrv_args=openrv_args) as spawner:
            # Mirror all runner logging to a file in the test's log directory so
            # CI failures are diagnosable without live stdout capture.
            runner_log_path = os.path.join(spawner.logs_dir, "runner.log")
            _runner_fh = logging.FileHandler(runner_log_path, mode="w")
            # Millisecond resolution, matching the console format in cli.py —
            # this file is what gets read alongside the plugin logs, so it is
            # the one that most needs to line up with them.
            _runner_fh.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S"
            ))
            logging.getLogger().addHandler(_runner_fh)
            player = None
            player_thread = None
            playing_state = {"playing": True}
            checkpoints = []
            state_checkpoints = []

            if script_driven:
                if 'commands' in test_data:
                    logging.info(f"Running in script-driven mode. Using {len(test_data['commands'])} commands from config.")
                    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                    commands = []
                    for cmd in test_data['commands']:
                        cmd = dict(cmd)
                        if cmd.get("action") == "add_media" and cmd.get("url") and not os.path.isabs(cmd["url"]):
                            cmd["url"] = os.path.join(repo_root, cmd["url"])
                        commands.append(cmd)
                elif recording_path:
                    logging.info(f"Running in script-driven mode. Deriving commands from {recording_path}")
                    commands = derive_commands_from_recording(recording_path)
                    logging.info(f"Derived {len(commands)} commands.")
                else:
                    logging.info("Running in script-driven mode with no commands (fixture-only test).")
                    commands = []
            else:
                player = SyncPlayer(session_id=session_id)
                player.load_recording(recording_path)

                checkpoints = derive_checkpoints(
                    recording_path,
                    min_spacing=checkpoint_min_spacing,
                    frame_tolerance=frame_tolerance,
                    validation_delay=checkpoint_validation_delay,
                )
                logging.info(f"Extracted {len(checkpoints)} validation checkpoints from recording.")

                state_checkpoints = derive_state_checkpoints(
                    recording_path, validation_delay=checkpoint_validation_delay
                )
                logging.info(
                    f"Extracted {len(state_checkpoints)} structural state checkpoint(s) "
                    "from recording."
                )

                # Drain window: the last replayed event may sit *after* the last
                # validation checkpoint, or a trailing checkpoint may sit after
                # the last replayed event (e.g. a post-delete STATE_SNAPSHOT that
                # asserts a REMOVE_TIMELINE took effect). Without a drain the
                # player stops the instant its last event is sent and the harness
                # tears the apps down before they apply it or before that
                # checkpoint is reached. Linger long enough for the wall clock to
                # pass the last checkpoint + validation delay, plus settle margin.
                last_event_offset = (
                    player.events[-1]["time_offset"] if player.events else 0.0
                )
                last_cp_offset = max(
                    [c["time_offset"] for c in checkpoints]
                    + [c["time_offset"] for c in state_checkpoints]
                    + [0.0]
                )
                drain_seconds = max(
                    _MIN_DRAIN_SECONDS,
                    (last_cp_offset - last_event_offset)
                    + checkpoint_validation_delay
                    + _DRAIN_SETTLE_MARGIN,
                )
                logging.info(
                    f"Post-playback drain: {drain_seconds:.1f}s "
                    f"(last event t={last_event_offset:.1f}s, "
                    f"last checkpoint t={last_cp_offset:.1f}s)."
                )

                # Start player FIRST so it claims master before any app launches.
                # Apps that connect afterwards will send STATE_REQUEST and receive
                # the recording's STATE_SNAPSHOT from the player.
                import threading

                logging.info(f"Starting playback (waiting for {len(apps)} peer(s))...")
                player.start_playback(
                    speed=1.0, wait_for_peer=True, min_peer_count=len(apps),
                    post_snapshot_delay=2.0, drain_seconds=drain_seconds,
                )

                def player_thread_func():
                    while playing_state["playing"]:
                        if not player.tick():
                            playing_state["playing"] = False
                        time.sleep(0.01)

                player_thread = threading.Thread(target=player_thread_func, daemon=True)
                player_thread.start()

            app_ports = []
            free_ports = _find_free_ports(len(apps))
            # ``fixtures`` is a parallel list to ``apps``; entry may be None.
            fixtures = test_data.get("fixtures", [])
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            for i, (app_name, port) in enumerate(zip(apps, free_ports)):
                fixture = fixtures[i] if i < len(fixtures) else None
                if fixture and not os.path.isabs(fixture):
                    fixture = os.path.join(repo_root, fixture)
                spawner.launch(app_name, port, session_file=fixture)
                app_ports.append((app_name, port))

            logging.info("Apps launched. Waiting for all apps to connect...")
            if not self._wait_for_all_apps(app_ports, timeout=90.0):
                logging.error("Timed out waiting for apps to become ready.")
                result = TestResult(
                    test_name, False, message="apps did not connect in time",
                    recording=recording, duration=time.time() - test_start_time,
                )
                self._write_history_entry(result)
                return result
            logging.info("All apps connected.")

            if not script_driven:
                logging.info("Waiting for all apps to receive the initial snapshot...")
                if not self._wait_for_snapshot(app_ports, timeout=30.0):
                    logging.warning("Apps did not all report a clip within 30s — proceeding anyway.")
                # Only now is the recording's t=0 defined. The player's own
                # peer-join gate sees a snapshot leave over RabbitMQ, which can
                # happen while we are still inside _wait_for_all_apps; it cannot
                # see an app finish applying that state and become queryable.
                # Arming here anchors the recording to the same readiness signal
                # this runner validates against, so checkpoint hold windows
                # cannot start elapsing before we are able to check them.
                # Unconditional, matching the "proceed anyway" tolerance above:
                # a degraded run proceeds exactly as it did before, just via the
                # explicit path rather than an implicit one.
                player.arm_clock()

            failed = False
            fail_reason = ""
            fail_kind = None
            converged_late = False
            time_to_converge = 0.0
            # Applies ONLY to checks with no moving target: the live
            # apps-vs-apps mismatch watch and the terminal structural-consensus
            # check (both compare peers to each other, and the latter runs after
            # the recording has stopped). Point-in-time checkpoints validated
            # against a recorded oracle mid-playback are deliberately excluded —
            # their expected value is only true inside the window the recording
            # holds it, so "wait longer" cannot help and the extra blocking
            # delays every subsequent checkpoint. See the two checkpoint sites
            # below for the full rationale.
            RETRY_MULTIPLIER = 2

            last_check_time = time.time()
            mismatch_start_time = None
            mismatch_retry_triggered = False
            MAX_DIVERGENCE_TIME = 10.0

            checkpoint_idx = 0
            state_checkpoint_idx = 0

            if script_driven:
                driver_app = app_ports[0]

                # `drive_host: true` — drive whichever peer holds visibility
                # authority rather than apps[0]. Needed when the peers are
                # equally ranked for host election (an OpenRV-only session),
                # where the tie breaks on a random per-launch GUID: driving
                # apps[0] regardless would assert visibility propagation from a
                # follower half the time, and a follower's visibility is
                # stripped by design, so the test would fail for the reason it
                # exists to prove works.
                if test_data.get("drive_host"):
                    host_app = self._select_host_driver(app_ports)
                    if host_app is None:
                        logging.warning(
                            "drive_host: no app claimed visibility authority "
                            f"within the timeout — falling back to {driver_app[0]}."
                        )
                    else:
                        if host_app is not driver_app:
                            logging.info(
                                f"drive_host: {host_app[0]} holds visibility "
                                f"authority — driving it instead of {driver_app[0]}."
                            )
                        driver_app = host_app

                # Structural commands (add_media, set_selection, ...) are only
                # ever broadcast by whichever peer holds master — a non-master
                # driver's own edits are silently dropped, not queued for later
                # broadcast (see check_otio_snapshots's is_master gate in
                # sequence_sync.py). Apps self-promote to master on different
                # timescales (xStudio tends to claim it near-instantly; OpenRV
                # waits ~2s for a WHO_IS_MASTER reply, and can take longer than
                # that just to finish booting), so launch order alone does not
                # reliably decide who wins. Wait here rather than assume, so a
                # lost race produces a clear log line instead of a generic
                # downstream state-mismatch failure.
                if commands and not self._wait_for_master(driver_app[1]):
                    logging.warning(
                        f"{driver_app[0]} did not become master within the "
                        "wait timeout — its structural commands (add_media, "
                        "set_selection, ...) may be silently dropped rather "
                        "than broadcast to peers."
                    )

                logging.info(f"Driving {driver_app[0]} via commands...")
                last_frame_cmd = None
                for cmd in commands:
                    action = cmd.get("action")

                    # An explicit gate a test can place wherever it needs one.
                    # Handled here rather than forwarded to a hook: the
                    # condition spans every peer, and no single app can answer
                    # it about the others.
                    if action == "wait_for_media":
                        logging.info("  -> Waiting for media on all peers...")
                        self._wait_for_media(
                            app_ports, timeout=cmd.get("timeout", 20.0)
                        )
                        continue

                    # Runner-level for the same reason as wait_for_media: the
                    # subject is a peer that is going away, so it cannot be
                    # asked to report on the outcome, and the assertion is about
                    # what the *survivors* do.
                    if action == "disconnect_peer":
                        target = cmd.get("app")
                        which = cmd.get("which", "host")
                        if target is None and which == "host":
                            host_app = self._select_host_driver(app_ports)
                            if host_app is None:
                                logging.error(
                                    "disconnect_peer: no app claimed visibility "
                                    "authority, so there is no host to remove "
                                    "and the failover assertion would be vacuous."
                                )
                                failed = True
                                break
                            target = host_app[0]
                        logging.info(f"  -> Disconnecting peer: {target}")
                        if not spawner.terminate_app(target):
                            logging.error(
                                f"disconnect_peer: no running app named {target!r}"
                            )
                            failed = True
                            break
                        app_ports = [a for a in app_ports if a[0] != target]
                        if driver_app[0] == target:
                            driver_app = app_ports[0] if app_ports else None
                        continue

                    if action == "expect_host_failover":
                        timeout = cmd.get("timeout", 30.0)
                        logging.info(
                            f"  -> Waiting up to {timeout:.0f}s for a survivor "
                            "to take visibility authority..."
                        )
                        if not self._wait_for_host_failover(app_ports, timeout):
                            logging.error(
                                "No surviving peer took visibility authority. "
                                "Only the host may broadcast visibility, so the "
                                "session's view is frozen for everyone left."
                            )
                            failed = True
                            break
                        continue

                    # Runner-level, not forwarded to a single app's hook: this
                    # is the deliberately-contended case broadcast-ownership
                    # needs coverage for (design.md 1b migration step) — no
                    # existing test drives two peers into the same category at
                    # once, which is why the position/structure guards have no
                    # positive evidence either way that they are safe to
                    # retire. `by_app` maps app name -> the command to send it;
                    # every entry fires from its own thread so the requests
                    # overlap on the wire (see _send_concurrent_commands).
                    if action == "concurrent_commands":
                        by_app = cmd.get("by_app", {})
                        logging.info(f"  -> Sending concurrent commands: {by_app}")
                        results = self._send_concurrent_commands(by_app, app_ports)
                        for name, res in results.items():
                            if "error" in res:
                                logging.error(
                                    f"concurrent_commands: {name} failed: {res['error']}"
                                )
                                failed = True
                        if failed:
                            break
                        continue

                    # Confirms the lease actually settles on one peer after a
                    # concurrent_commands contention, rather than staying
                    # split between two peers' local views forever.
                    if action == "expect_ownership_convergence":
                        channel = cmd.get("channel", "position")
                        timeout = cmd.get("timeout", 15.0)
                        logging.info(
                            f"  -> Waiting up to {timeout:.0f}s for the {channel} "
                            "lease to converge..."
                        )
                        if self._wait_for_ownership_convergence(
                            app_ports, channel, timeout
                        ) is None:
                            logging.error(
                                f"Peers never agreed on a single {channel} owner — "
                                "the lease stayed split instead of converging."
                            )
                            failed = True
                            break
                        continue

                    # Implicit gate. Deliberately automatic rather than left to
                    # each test to remember: omitting it costs a silently
                    # dropped annotation and an intermittent failure elsewhere,
                    # which is exactly the class of bug this removes.
                    if action == "draw_annotation":
                        self._wait_for_media(app_ports)

                    logging.info(f"  -> Sending command: {cmd}")
                    res = self.send_command(driver_app[1], cmd)
                    if "error" in res:
                        logging.error(f"Command execution failed: {res['error']}")
                        failed = True
                        break
                    time.sleep(1.0)

                    # Verify *every* seek, not just the last one. Only checking
                    # the final set_frame lets an earlier seek fail to
                    # propagate and then be masked by a later one that happens
                    # to land — the peer looks synced at the end while having
                    # missed a seek in the middle. Each seek parks the playhead
                    # at a known value, so each is independently checkable.
                    if cmd.get("action") == "set_frame":
                        last_frame_cmd = cmd
                        ok, msg, elapsed, states = self._verify_frame_sync(
                            app_ports, cmd["frame"], frame_tolerance,
                            checkpoint_validation_delay,
                            label=f"set_frame({cmd['frame']})",
                        )
                        time_to_converge = max(time_to_converge, elapsed)
                        observed = _format_observed(states, [a[0] for a in app_ports])
                        if ok:
                            if elapsed > 0:
                                converged_late = True
                            logging.info(
                                f"✅ set_frame {cmd['frame']} synced "
                                f"(expected frame ~{int(cmd['frame']) + 1}) "
                                f"[valid after {elapsed:.1f}s] — observed: {observed}"
                            )
                        else:
                            logging.error(f"❌ FAIL: {msg} [waited {elapsed:.1f}s]")
                            failed = True
                            fail_kind = FailKind.CHECKPOINT_TIMEOUT
                            fail_reason = msg
                            break

                convergence_wait = float(test_data.get("convergence_wait", 3.0))
                logging.info(f"Command sequence completed. Waiting {convergence_wait}s for convergence...")
                time.sleep(convergence_wait)
                playing_state["playing"] = False

                # A `set_frame` command drives a real playhead seek (a
                # "shuttle") on the driver app; verify the peer(s) actually
                # followed it. compare_states ignores "frame" entirely
                # (it's transient noise during recording playback), but here
                # it is the exact thing under test, so check it explicitly
                # via the same tolerance logic as recording-mode frame
                # checkpoints.
                if not failed and last_frame_cmd is not None:
                    ok, msg, elapsed, states = self._verify_frame_sync(
                        app_ports, last_frame_cmd["frame"], frame_tolerance,
                        checkpoint_validation_delay, label="Shuttle check",
                    )
                    time_to_converge = max(time_to_converge, elapsed)
                    observed = _format_observed(states, [a[0] for a in app_ports])
                    if ok:
                        if elapsed > 0:
                            converged_late = True
                        logging.info(
                            f"✅ Shuttle check passed: expected frame "
                            f"~{int(last_frame_cmd['frame']) + 1} "
                            f"[valid after {elapsed:.1f}s] — observed: {observed}"
                        )
                    else:
                        logging.error(f"❌ FAIL: {msg} [waited {elapsed:.1f}s]")
                        failed = True
                        fail_kind = FailKind.CHECKPOINT_TIMEOUT
                        fail_reason = msg

            while playing_state["playing"]:
                if time.time() - last_check_time > 0.5:
                    last_check_time = time.time()

                    states = []
                    for name, port in app_ports:
                        st = self.fetch_state(port)
                        states.append(st)

                    match, diff, diff_kind = self.compare_states(states, [a[0] for a in app_ports])
                    if not match:
                        if diff_kind == FailKind.MISSING_MEDIA:
                            # Never resolved by waiting longer — fail immediately,
                            # no retry (see FailKind / TIMING_ELIGIBLE_FAIL_KINDS).
                            logging.error(f"❌ FAIL: {diff}")
                            failed = True
                            fail_kind = diff_kind
                            fail_reason = diff
                            playing_state["playing"] = False
                            break
                        if mismatch_start_time is None:
                            mismatch_start_time = time.time()
                            mismatch_retry_triggered = False
                            logging.warning(f"Transient mismatch detected, waiting for convergence...\n{diff}")
                        else:
                            elapsed = time.time() - mismatch_start_time
                            if elapsed > MAX_DIVERGENCE_TIME and not mismatch_retry_triggered:
                                mismatch_retry_triggered = True
                                logging.warning(
                                    f"State mismatch persisted past {MAX_DIVERGENCE_TIME}s — "
                                    f"retrying once up to {MAX_DIVERGENCE_TIME * RETRY_MULTIPLIER}s "
                                    "before failing."
                                )
                            if elapsed > MAX_DIVERGENCE_TIME * RETRY_MULTIPLIER:
                                logging.error(f"❌ FAIL: State mismatch persisted for >{MAX_DIVERGENCE_TIME * RETRY_MULTIPLIER}s in test '{test_name}'!\n{diff}")
                                logging.error("Check application logs for details:")
                                for name, port in app_ports:
                                    logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                                failed = True
                                fail_kind = diff_kind
                                fail_reason = diff
                                playing_state["playing"] = False
                                break
                    else:
                        if mismatch_start_time is not None:
                            elapsed = time.time() - mismatch_start_time
                            time_to_converge = max(time_to_converge, elapsed)
                            if mismatch_retry_triggered:
                                converged_late = True
                                logging.info(f"✅ States converged late (after {elapsed:.1f}s, within retry window).")
                            else:
                                logging.info(f"✅ States have converged again (after {elapsed:.1f}s).")
                            mismatch_start_time = None
                            mismatch_retry_triggered = False

                    # Checkpoint validation: once enough time has passed after a checkpoint
                    # event was dispatched, check that apps reflect the expected state.
                    if player and player._play_start_time is not None:
                        current_offset = (time.time() - player._play_start_time) * 1.0
                        while checkpoint_idx < len(checkpoints):
                            cp = checkpoints[checkpoint_idx]
                            if current_offset < cp["time_offset"] + checkpoint_validation_delay:
                                break
                            # Playback is frozen before state is sampled (not
                            # just around validate_checkpoint) so the app
                            # state checked is not itself racing an advancing
                            # recording. With the target no longer able to go
                            # stale mid-check, a first-attempt mismatch is
                            # retry-eligible again — poll up to
                            # FRAME_CP_BASE_DEADLINE, then once more at 2x,
                            # mirroring the bounded-retry pattern used for the
                            # live mismatch watch and terminal consensus check
                            # below. See freeze-recording-during-validation
                            # change.
                            FRAME_CP_BASE_DEADLINE = 5.0
                            cp_start = time.time()
                            cp_deadline = cp_start + FRAME_CP_BASE_DEADLINE
                            cp_retried = False
                            cp_names = [a[0] for a in app_ports]
                            with _freeze_playback(player):
                                while True:
                                    cp_states = [self.fetch_state(port) for _, port in app_ports]
                                    ok, msg = self.validate_checkpoint(cp_states, cp_names, cp)
                                    if ok and _any_playing(cp_states):
                                        # validate_checkpoint skips the frame
                                        # comparison for a playing app, so this
                                        # "pass" may have asserted nothing.
                                        # Keep waiting for the playhead to park
                                        # rather than bank a vacuous result.
                                        if time.time() < cp_deadline:
                                            time.sleep(0.5)
                                            continue
                                        ok = False
                                        msg = (
                                            f"Checkpoint at t={cp['time_offset']:.1f}s: playback "
                                            "still active at deadline — frame never became "
                                            "assertable\n  observed: "
                                            f"{_format_observed(cp_states, cp_names)}"
                                        )
                                    if ok or time.time() >= cp_deadline:
                                        if not ok and not cp_retried:
                                            cp_retried = True
                                            cp_deadline = cp_start + FRAME_CP_BASE_DEADLINE * RETRY_MULTIPLIER
                                            logging.warning(
                                                f"Checkpoint t={cp['time_offset']:.1f}s not yet matching after "
                                                f"{FRAME_CP_BASE_DEADLINE:.1f}s — retrying up to "
                                                f"{FRAME_CP_BASE_DEADLINE * RETRY_MULTIPLIER:.1f}s "
                                                "(playback stays frozen) before failing."
                                            )
                                            continue
                                        break
                                    time.sleep(0.5)
                            cp_elapsed = time.time() - cp_start
                            time_to_converge = max(time_to_converge, cp_elapsed)
                            # Real wall-clock time from the recording event to the
                            # moment it was validated — directly comparable to
                            # checkpoint_validation_delay, so this is the number to
                            # read when tuning that setting.
                            validated_after = (time.time() - player._play_start_time) - cp["time_offset"]
                            timing_note = (
                                f" [checked {validated_after:.1f}s after event, took {cp_elapsed:.1f}s; "
                                f"checkpoint_validation_delay={checkpoint_validation_delay:.1f}s]"
                            )
                            if ok:
                                if cp_retried:
                                    converged_late = True
                                logging.info(
                                    f"✅ Checkpoint t={cp['time_offset']:.1f}s passed "
                                    f"(frame={cp.get('frame')}, clip={cp.get('timeline_name')})"
                                    f"{timing_note}"
                                )
                            else:
                                logging.error(f"❌ FAIL: {msg}{timing_note}")
                                logging.error("Check application logs for details:")
                                for name, port in app_ports:
                                    logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                                failed = True
                                fail_kind = FailKind.CHECKPOINT_TIMEOUT
                                fail_reason = msg
                                playing_state["playing"] = False
                            checkpoint_idx += 1

                        # Structural state checkpoints: once a snapshot's settle
                        # time has passed, fetch each app's full state, project it,
                        # and diff against the recorded snapshot's projection.
                        while state_checkpoint_idx < len(state_checkpoints):
                            scp = state_checkpoints[state_checkpoint_idx]
                            if current_offset < scp["time_offset"] + checkpoint_validation_delay:
                                break
                            names = [a[0] for a in app_ports]
                            # Poll until the apps converge to the checkpoint state
                            # rather than sampling once: structural mutations
                            # (e.g. a reorder replayed as a burst of MOVE_CHILDs)
                            # take time to fully apply, so a one-shot check at a
                            # fixed replay offset is flaky — it can land mid-burst.
                            # The apps are eventually-consistent; a genuine desync
                            # never converges and still fails after the timeout.
                            # Bounded by SCP_BASE_DEADLINE, now extended by the
                            # same 2x retry that terminal checks get: playback
                            # is frozen for the entire poll below (including
                            # the retry extension), so the recording cannot
                            # advance while it runs and the target cannot go
                            # stale — see freeze-recording-during-validation
                            # change.
                            ok, msg = False, ""
                            SCP_BASE_DEADLINE = 10.0
                            scp_start = time.time()
                            scp_deadline = scp_start + SCP_BASE_DEADLINE
                            scp_retried = False
                            with _freeze_playback(player):
                                while True:
                                    full_states = [self.fetch_full_state(port) for _, port in app_ports]
                                    ok, msg = self.validate_state_checkpoint(
                                        full_states, names, scp, frame_tolerance=frame_tolerance,
                                    )
                                    if ok:
                                        # Oracle passed; also require client-vs-client
                                        # consensus (frame only when playhead parked).
                                        ok, msg = self.compare_full_states(
                                            full_states, names, frame_tolerance=frame_tolerance,
                                            compare_frame=scp.get("frame_held", False),
                                        )
                                    if ok or time.time() >= scp_deadline:
                                        if not ok and not scp_retried:
                                            scp_retried = True
                                            scp_deadline = scp_start + SCP_BASE_DEADLINE * RETRY_MULTIPLIER
                                            logging.warning(
                                                f"State checkpoint t={scp['time_offset']:.1f}s not yet "
                                                f"matching after {SCP_BASE_DEADLINE:.1f}s — retrying up to "
                                                f"{SCP_BASE_DEADLINE * RETRY_MULTIPLIER:.1f}s "
                                                "(playback stays frozen) before failing."
                                            )
                                            continue
                                        break
                                    time.sleep(0.5)
                            scp_elapsed = time.time() - scp_start
                            time_to_converge = max(time_to_converge, scp_elapsed)
                            validated_after = (time.time() - player._play_start_time) - scp["time_offset"]
                            timing_note = (
                                f" [valid {validated_after:.1f}s after event, took {scp_elapsed:.1f}s; "
                                f"checkpoint_validation_delay={checkpoint_validation_delay:.1f}s]"
                            )
                            if ok:
                                if scp_retried:
                                    converged_late = True
                                logging.info(
                                    f"✅ State checkpoint t={scp['time_offset']:.1f}s passed"
                                    + (f" ({msg})" if msg else "") + timing_note
                                )
                            else:
                                logging.error(f"❌ FAIL: {msg}{timing_note}")
                                logging.error("Check application logs for details:")
                                for name, port in app_ports:
                                    logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                                failed = True
                                fail_kind = FailKind.STRUCTURAL_CONSENSUS
                                fail_reason = msg
                                playing_state["playing"] = False
                            state_checkpoint_idx += 1

                time.sleep(0.5)

            if player:
                player.stop_playback()
                player_thread.join(timeout=1.0)
            else:
                # Script-driven: final coherence check
                states = [self.fetch_state(port) for _, port in app_ports]
                match, diff, diff_kind = self.compare_states(states, [a[0] for a in app_ports])
                elapsed = 0.0
                if not match and diff_kind in TIMING_ELIGIBLE_FAIL_KINDS:
                    logging.warning(
                        f"Final coherence check failed on first try ({diff_kind}) — "
                        f"retrying for up to {checkpoint_validation_delay:.1f}s more before failing."
                    )
                    _kind_box = {"kind": diff_kind}

                    def _recheck_final_coherence():
                        fresh_states = [self.fetch_state(port) for _, port in app_ports]
                        m, d, k = self.compare_states(fresh_states, [a[0] for a in app_ports])
                        _kind_box["kind"] = k
                        return m, d

                    match, diff, elapsed = _poll_until(_recheck_final_coherence, checkpoint_validation_delay)
                    diff_kind = _kind_box["kind"]
                time_to_converge = max(time_to_converge, elapsed)
                if not match:
                    logging.error(
                        f"❌ FAIL: Final state mismatch in test '{test_name}' "
                        f"[waited {elapsed:.1f}s]!\n{diff}"
                    )
                    failed = True
                    fail_kind = diff_kind
                    fail_reason = diff
                else:
                    if elapsed > 0:
                        converged_late = True
                    logging.info(f"✅ Final coherence check passed [valid after {elapsed:.1f}s]")

            # Annotation geometry round-trip check (script-driven `draw_annotation`
            # tests only): verify the peer's native readback matches the value
            # predicted by feeding the driver's nominal input through both apps'
            # real codec constants (see `annotation_assertions`).
            annotation_geometry = test_data.get("annotation_geometry")
            if not failed and annotation_geometry:
                ok, msg = self._verify_annotation_geometry(app_ports, annotation_geometry)
                if ok:
                    logging.info(f"✅ Annotation geometry round-trip verified: {annotation_geometry}")
                else:
                    logging.error(f"❌ FAIL: annotation geometry mismatch in test '{test_name}': {msg}")
                    failed = True

            # Visual check (sync-test-frame-capture change): additive to the
            # numeric round-trip above — captures the peer's live rendered
            # frame and checks the annotation actually appears where/how thick
            # expected, the class of bug (e.g. the 2x rect-border bug) that a
            # self-consistent-but-wrong numeric round-trip cannot catch.
            # Opt-in via `visual_check: true` in the `annotation_geometry` block.
            if not failed and annotation_geometry and annotation_geometry.get("visual_check"):
                draw_cmd = None
                if script_driven and "commands" in test_data:
                    kind = annotation_geometry.get("kind", "pen")
                    draw_cmd = next(
                        (c for c in commands
                         if c.get("action") == "draw_annotation" and c.get("kind") == kind),
                        None,
                    )
                ok, msg = self._verify_visual_check(
                    app_ports, annotation_geometry, draw_cmd, spawner.logs_dir
                )
                if ok:
                    logging.info(f"✅ Visual check: {msg}")
                else:
                    logging.error(f"❌ FAIL: visual check in test '{test_name}': {msg}")
                    failed = True

            # Annotation-presence check: if the recording contained annotations,
            # every app must have created at least one. Placement/frame
            # correctness is intentionally not asserted here (punted for now);
            # this only catches the "annotations silently dropped" failure.
            if not failed and expect_annotations:
                counts_by_app = {}
                for name, port in app_ports:
                    st = self.fetch_state(port)
                    cnt = st.get("annotation_count")
                    if cnt is None:
                        logging.warning(
                            f"{name} does not report annotation_count; "
                            "skipping annotation-presence check"
                        )
                    elif cnt <= 0:
                        logging.error(
                            f"❌ FAIL: {name} created 0 annotations, but the "
                            f"recording for '{test_name}' contains annotations."
                        )
                        failed = True
                        fail_kind = FailKind.ANNOTATION_MISSING
                        fail_reason = f"{name} created no annotations"
                    else:
                        logging.info(f"✅ {name} created {cnt} annotation stroke(s)")
                        counts_by_app[name] = cnt

                # Cross-app sanity check: apps should report roughly the same
                # number of annotations. A large spread (e.g. RV reporting 60+
                # against xStudio's 20) is exactly the signature of an
                # undetected duplication/pile-up bug -- a single pen gesture
                # fragmenting into many overlapping partial-tick nodes on one
                # side rather than settling into one final stroke -- not a
                # legitimate difference in how each host counts annotations.
                # Warning rather than failing: a modest divergence can be
                # legitimate, and this repo currently has one known,
                # unresolved source of inflation (see annotation_sync.py's
                # mid-drag debris notes), so hard-failing here would make
                # already-accepted flakiness look like a new regression.
                ANNOTATION_COUNT_RATIO_WARN = 1.5
                if len(counts_by_app) >= 2:
                    lo_name, lo = min(counts_by_app.items(), key=lambda kv: kv[1])
                    hi_name, hi = max(counts_by_app.items(), key=lambda kv: kv[1])
                    if lo > 0 and (hi / lo) > ANNOTATION_COUNT_RATIO_WARN:
                        logging.warning(
                            f"⚠  annotation_count mismatch across apps in '{test_name}': "
                            + ", ".join(f"{n}={c}" for n, c in counts_by_app.items())
                            + f" ({hi_name}/{lo_name} ratio {hi / lo:.1f}x > "
                            f"{ANNOTATION_COUNT_RATIO_WARN}x) — possible "
                            "duplicate/undeduplicated annotations on one side"
                        )

            # Final structural consensus: the apps must agree on timeline
            # structure (clip set + order). Catches desyncs that the lightweight
            # compare_states is blind to — e.g. a MOVE_CHILD reorder where both
            # apps still report the same active-timeline *name* but hold the
            # clips in a different order. Independent of recorded snapshots, so it
            # works even for recordings with only the initial STATE_SNAPSHOT.
            # Apps that do not expose /full_state are skipped (compare_full_states
            # needs >=2 valid projections), so this only fires when both report.
            if not failed and len(app_ports) >= 2:
                # Poll until the apps converge rather than checking once: cross-app
                # sync has lag, so a one-shot check after a fixed wait is flaky
                # (the slower peer may not have applied the last events yet). A
                # genuine desync never converges and still fails after the timeout.
                ok, msg = False, ""
                CONSENSUS_BASE_DEADLINE = 15.0
                consensus_start = time.time()
                deadline = consensus_start + CONSENSUS_BASE_DEADLINE
                consensus_retried = False
                while True:
                    full_states = [self.fetch_full_state(port) for _, port in app_ports]
                    ok, msg = self.compare_full_states(
                        full_states, [a[0] for a in app_ports], frame_tolerance=frame_tolerance
                    )
                    if ok:
                        break
                    if time.time() >= deadline:
                        if not consensus_retried:
                            # One bounded retry at 2x — structural consensus is
                            # convergence-timing-eligible.
                            consensus_retried = True
                            deadline = consensus_start + CONSENSUS_BASE_DEADLINE * RETRY_MULTIPLIER
                            logging.warning(
                                f"Structural consensus not yet reached after "
                                f"{CONSENSUS_BASE_DEADLINE:.1f}s — retrying up to "
                                f"{CONSENSUS_BASE_DEADLINE * RETRY_MULTIPLIER:.1f}s before failing."
                            )
                            continue
                        break
                    time.sleep(1.0)
                consensus_elapsed = time.time() - consensus_start
                time_to_converge = max(time_to_converge, consensus_elapsed)
                if not ok:
                    logging.error(
                        f"❌ FAIL: structural consensus in '{test_name}' "
                        f"[took {consensus_elapsed:.1f}s]:\n{msg}"
                    )
                    failed = True
                    fail_kind = FailKind.STRUCTURAL_CONSENSUS
                    fail_reason = msg
                else:
                    if consensus_retried:
                        converged_late = True
                    logging.info(
                        "✅ Apps agree on timeline structure (full-state consensus) "
                        f"[took {consensus_elapsed:.1f}s]"
                    )

            # OTIO structural comparison (§9.5): export the timeline from every
            # app and compare it against a reference .otio file.  Triggered when
            # the yaml test has an ``otio_compare`` block:
            #   otio_compare:
            #     reference: "test_media/source/otio_test_quicktime.otio"
            #     export_delay: 4.0   # optional extra settle time
            if not failed and "otio_compare" in test_data:
                try:
                    from sync_test.otio_compare import load_cut_structure, compare
                    otio_cfg = test_data["otio_compare"]
                    ref_path = otio_cfg.get("reference", "")
                    if not os.path.isabs(ref_path):
                        ref_path = os.path.join(repo_root, ref_path)
                    export_delay = float(otio_cfg.get("export_delay", 3.0))
                    logging.info(
                        f"OTIO compare: waiting {export_delay}s for sync to settle..."
                    )
                    time.sleep(export_delay)
                    ref_struct = load_cut_structure(ref_path)
                    for app_name, port in app_ports:
                        export_path = os.path.join(
                            spawner.logs_dir, f"{app_name}_{port}_export.otio"
                        )
                        res = self.send_command(port, {
                            "action": "export_otio",
                            "filepath": export_path,
                        })
                        if "error" in res:
                            logging.error(
                                f"❌ FAIL: {app_name} export_otio failed: "
                                f"{res['error']}"
                            )
                            failed = True
                            fail_kind = FailKind.OTIO_EXPORT
                            fail_reason = f"{app_name} export_otio failed: {res['error']}"
                            continue
                        import opentimelineio as otio
                        candidate = otio.adapters.read_from_file(export_path)
                        equal, diffs = compare(ref_struct, candidate)
                        if equal:
                            logging.info(
                                f"✅ {app_name} OTIO export matches reference "
                                f"'{os.path.basename(ref_path)}'"
                            )
                        else:
                            logging.error(
                                f"❌ FAIL: {app_name} OTIO export differs from "
                                f"reference '{os.path.basename(ref_path)}':\n"
                                + "\n".join(f"  {d}" for d in diffs)
                            )
                            failed = True
                            fail_kind = FailKind.OTIO_EXPORT
                            fail_reason = (
                                f"{app_name} OTIO export differs from reference "
                                f"'{os.path.basename(ref_path)}': " + "; ".join(diffs)
                            )
                except Exception as e:
                    logging.error(f"❌ FAIL: otio_compare block raised: {e}", exc_info=True)
                    failed = True
                    fail_kind = FailKind.OTIO_EXPORT
                    fail_reason = f"otio_compare block raised: {e}"

            # Save session states
            for name, port in app_ports:
                try:
                    ext = ".xst" if name == "xstudio" else ".rv"
                    session_file = os.path.join(spawner.logs_dir, f"{name}_{port}{ext}")
                    session_file = os.path.abspath(session_file)
                    res = self.send_command(port, {"action": "save_session", "filepath": session_file})
                    if "error" in res:
                        logging.error(f"Error saving {name} session: {res['error']}")
                    else:
                        logging.info(f"Saved {name} session to {session_file}")
                except Exception as e:
                    logging.error(f"Failed to save {name} session: {e}")

            if self._report_log_errors(app_ports, spawner.logs_dir):
                failed = True
                fail_kind = fail_kind or FailKind.LOG_ERROR_SIGNATURE
                fail_reason = fail_reason or "known-bad signature found in a plugin log"

            if failed:
                logging.error(f"Test '{test_name}' FAILED.")
            else:
                logging.info(f"✅ Test '{test_name}' PASSED.")

            logging.getLogger().removeHandler(_runner_fh)
            _runner_fh.close()

            result = TestResult(
                test_name, not failed,
                fail_kind=fail_kind if failed else None,
                message=fail_reason,
                converged_late=converged_late,
                time_to_converge=time_to_converge,
                recording=recording,
                duration=time.time() - test_start_time,
            )
            self._write_history_entry(result)
            return result

    def _scan_log_for_errors(self, log_path):
        """Return a Counter of {error_summary: count} found in a log file.

        Captures Python exception messages (the last line of each traceback)
        and bare xStudio '*** unexpected message' lines as distinct keys.
        """
        counts = Counter()
        if not os.path.exists(log_path):
            return counts
        try:
            with open(log_path, errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return counts

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            if "Traceback (most recent call last)" in line:
                # Scan forward for the exception line (last non-blank, non-"File" line)
                j = i + 1
                exc_line = None
                while j < len(lines):
                    l = lines[j].rstrip()
                    if l and not l.startswith("  "):
                        exc_line = l
                        # Keep scanning: a traceback may have chained exceptions
                        if not re.match(r"[A-Za-z].*Error|[A-Za-z].*Exception|[A-Za-z].*Warning", l):
                            j += 1
                            continue
                        break
                    j += 1
                if exc_line:
                    counts[exc_line] += 1
                i = j
            elif line.startswith("*** "):
                # xStudio internal actor errors, e.g. "*** unexpected message [...]"
                # Normalise the actor ID out so identical errors collapse.
                key = re.sub(r"\[id: \d+, name: [^\]]+\]", "[actor]", line)
                counts[key] += 1
                i += 1
            else:
                i += 1
        return counts

    #: Known-bad ori_sync plugin-log signatures. Unlike the generic
    #: traceback scan below (which only warns — any exception could be
    #: benign or unrelated), these are specific, previously-diagnosed
    #: signs of real correctness bugs in the annotation-sync path (see the
    #: pen-stroke debris investigation), so a match here is a hard failure.
    _KNOWN_BAD_PLUGIN_LOG_SIGNATURES = (
        "invalid property name",
        "RECV annotation dropped",
        "no paint node for",
        "Failed to apply remote annotation",
        "failed to deserialise event",
    )

    def _scan_plugin_log_for_known_bad(self, log_path):
        """Return a Counter of {signature: count} of known-bad lines in a plugin log."""
        counts = Counter()
        if not os.path.exists(log_path):
            return counts
        try:
            with open(log_path, errors="replace") as f:
                for line in f:
                    for sig in self._KNOWN_BAD_PLUGIN_LOG_SIGNATURES:
                        if sig in line:
                            counts[sig] += 1
        except OSError:
            pass
        return counts

    def _report_log_errors(self, app_ports, logs_dir):
        """Scan console and ori_sync plugin logs for problems.

        :returns: True if any known-bad plugin-log signature was found
            (callers should treat this as a test failure).
        """
        for app_name, port in app_ports:
            log_path = os.path.join(logs_dir, f"{app_name}_{port}.log")
            counts = self._scan_log_for_errors(log_path)
            if not counts:
                continue
            total = sum(counts.values())
            logging.warning(
                f"⚠  {app_name} log has {total} exception(s) — {log_path}"
            )
            for msg, n in counts.most_common(5):
                logging.warning(f"    {n:3}x  {msg}")
            if len(counts) > 5:
                logging.warning(f"    ... and {len(counts) - 5} more distinct error type(s)")

        found_known_bad = False
        for app_name, port in app_ports:
            plugin_log_path = os.path.join(logs_dir, f"{app_name}_plugin_{port}.log")
            counts = self._scan_plugin_log_for_known_bad(plugin_log_path)
            if not counts:
                continue
            found_known_bad = True
            total = sum(counts.values())
            logging.error(
                f"❌ {app_name} plugin log has {total} known-bad signature(s) — {plugin_log_path}"
            )
            for msg, n in counts.most_common():
                logging.error(f"    {n:3}x  {msg!r}")
        return found_known_bad

    def _test_status(self, test_name):
        """``(status, blocked_by)`` for a configured test, defaulting to

        ``("stable", None)`` when unset.
        """
        test_data = self.config.get_test(test_name) or {}
        return test_data.get('status', 'stable'), test_data.get('blocked_by')

    def counts_as_suite_pass(self, test_name, result):
        """Whether *result* should count toward the overall suite pass/fail.

        A ``known_broken`` test failing as expected is still reported (see
        ``run_all``'s summary), but does not fail the suite — that's the
        entire point of declaring it known_broken rather than leaving it to
        redden the suite like an undiagnosed regression. Everything else
        must pass normally.
        """
        status, _ = self._test_status(test_name)
        if status == 'known_broken':
            return True
        return result.passed

    def _format_prev_result(self, entries):
        """Compact "what happened last time" string from prior history

        entries (oldest-to-newest, *not* including the run currently in
        progress). Shows the immediately-previous result plus a short
        pass/fail trend strip so flakiness is visible without cross-checking
        `run_history.jsonl` by hand.
        """
        if not entries:
            return "no prior runs"
        recent = entries[-5:]
        trend = "".join("✅" if e.get("result") == "pass" else "❌" for e in recent)
        last = entries[-1]
        last_desc = "pass" if last.get("result") == "pass" else f"fail({last.get('fail_kind')})"
        return f"prev: {last_desc}, last {len(recent)}: {trend}"

    def run_all(self, script_driven=False):
        # Snapshot history *before* this run's own entries are appended, so
        # "previous results" in the summary reflects prior invocations only.
        previous_history = self.load_history()

        suite_start_time = time.time()
        results = {}
        total = len(self.config.tests)
        for i, t in enumerate(self.config.tests, start=1):
            test_name = t['name']
            results[test_name] = self.run_test(
                test_name, script_driven=script_driven,
                test_index=i, test_total=total,
            )
        suite_duration = time.time() - suite_start_time

        print("\n" + "="*70)
        print("  SYNC TEST SUMMARY")
        print("="*70)
        all_passed = True
        for test_name, result in results.items():
            status, blocked_by = self._test_status(test_name)
            duration_str = f"[{result.duration:.1f}s]"
            prev = self._format_prev_result(previous_history.get(test_name, []))

            if result.passed and status == 'known_broken':
                print(
                    f"  ⚠️  XPASS         |  {test_name}  {duration_str}  (status: known_broken "
                    f"but passed — check whether blocked_by '{blocked_by}' can be closed out)  ({prev})"
                )
            elif result.passed:
                suffix = (
                    f"  (converged late, {result.time_to_converge:.1f}s)"
                    if result.converged_late else ""
                )
                print(f"  ✅ PASSED        |  {test_name}  {duration_str}{suffix}  ({prev})")
            elif status == 'known_broken':
                print(
                    f"  ⚪ KNOWN_BROKEN  |  {test_name}  {duration_str}  "
                    f"({result.fail_kind}; blocked_by: {blocked_by})  ({prev})"
                )
            else:
                print(f"  ❌ FAILED        |  {test_name}  {duration_str}  ({result.fail_kind})  ({prev})")

            if not self.counts_as_suite_pass(test_name, result):
                all_passed = False
        print("="*70)
        print(f"  Total run time: {suite_duration:.1f}s across {len(results)} test(s)")
        print("="*70 + "\n")

        return all_passed


def derive_checkpoints(jsonl_path, min_spacing=2.0, frame_tolerance=5,
                       validation_delay=0.0):
    """Extract validation checkpoints from a recording.

    Only positions where the recording is silent for at least *validation_delay*
    seconds afterwards are eligible.  This ensures that by the time we validate
    the checkpoint the recording hasn't already advanced the frame further.

    Each checkpoint is a dict:
        time_offset     – seconds into the recording when the event was sent
        frame           – expected frame number (0-indexed from PLAYBACK_SETTINGS)
        timeline_name   – human-readable timeline/clip name, or None if unknown
        frame_tolerance – forwarded from the caller for use in validate_checkpoint
    """
    raw = []
    guid_to_name = {}

    with open(jsonl_path, 'r') as f:
        for line in f:
            try:
                row = json.loads(line.strip())
                time_offset = row.get("time_offset", 0)
                p = row.get("payload", {}).get("payload", {})
                schema = p.get("command_schema")
                event = p.get("command", {}).get("event")
                inner = p.get("command", {}).get("payload", {})

                # Build guid→name from snapshots and inserts
                if schema == "LiveSession.1" and event == "STATE_SNAPSHOT":
                    for tl_guid, tl in inner.get("timelines", {}).items():
                        guid_to_name[tl_guid] = tl.get("name", "")
                        for track in tl.get("tracks", {}).get("children", []):
                            for clip in track.get("children", []):
                                c_guid = clip.get("metadata", {}).get("sync", {}).get("guid")
                                if c_guid:
                                    guid_to_name[c_guid] = clip.get("name", "")

                elif schema == "OTIO_SESSION_1.0" and event == "INSERT_CHILD":
                    child = inner.get("child_data", {})
                    guid = child.get("metadata", {}).get("sync", {}).get("guid")
                    if guid:
                        guid_to_name[guid] = child.get("name", "")

                elif schema == "PLAYBACK_SETTINGS_1.0" and event == "SET":
                    if inner.get("playing") or inner.get("scrubbing"):
                        continue

                    ct = inner.get("current_time", {})
                    frame = ct.get("value")
                    if frame is None:
                        continue

                    tl_guid = inner.get("timeline_guid")
                    raw.append({
                        "time_offset": time_offset,
                        "frame": frame,
                        "timeline_name": guid_to_name.get(tl_guid) if tl_guid else None,
                        "frame_tolerance": frame_tolerance,
                    })

            except Exception:
                continue

    # Filter: only keep positions where the recording is silent for
    # validation_delay + a safety margin afterward, so validation (which can land
    # up to ~0.5 s late due to loop granularity) happens comfortably before the
    # next frame change — not at the edge of a jump back to 0 mid-scrub.
    if validation_delay > 0:
        required = validation_delay + _FRAME_HOLD_SAFETY_MARGIN
        stable = []
        for i, cp in enumerate(raw):
            next_t = raw[i + 1]["time_offset"] if i + 1 < len(raw) else float("inf")
            if next_t - cp["time_offset"] >= required:
                stable.append(cp)
        raw = stable

    # Keep the last event in each burst: scan backwards, emit when the gap to
    # the previous emitted entry is >= min_spacing.
    checkpoints = []
    for cp in reversed(raw):
        if not checkpoints or (checkpoints[-1]["time_offset"] - cp["time_offset"]) >= min_spacing:
            checkpoints.append(cp)
    checkpoints.reverse()

    return checkpoints


# Schemas whose events change a timeline's structure or active selection — i.e.
# the things the canonical projection compares. A state checkpoint is only valid
# once the recording is quiet of these for ``validation_delay`` afterward;
# otherwise the live apps will have advanced past the snapshot by the time we
# validate it (frame drift is tolerated separately by diff_states).
_STRUCTURAL_SCHEMAS = {"OTIO_SESSION_1.0", "TIMELINE_1.0", "SELECTION_1.0"}

# Extra silence required *beyond* validation_delay before a frame is treated as
# "held" and worth validating. A frame checkpoint validates at
# ``snapshot_time + validation_delay``, but the runner's validation loop only
# ticks every ~0.5 s, so validation can land up to that late. Without this margin
# a frame held only marginally longer than validation_delay (e.g. a brief pause
# mid-scrub before a jump back to 0) gets validated right as the next change
# lands, and the live apps have already followed the recording onward.
_FRAME_HOLD_SAFETY_MARGIN = 1.5

# Minimum structural silence required *beyond* validation_delay before a
# STATE_SNAPSHOT is used as a structural checkpoint.  The inspector round-trip
# takes ~0.5 s per app, so a snapshot whose next structural event fires only
# marginally after validation_delay will have already been superseded by the
# time the first poll result arrives — causing a false failure.
_SCP_SILENCE_MARGIN = 1.5

# Post-playback drain (see run_test). Minimum lingering time after the last
# replayed event so the final events always get settle time, and the extra
# margin added beyond (last_checkpoint - last_event + validation_delay) so the
# trailing checkpoint validates comfortably before the player stops.
_MIN_DRAIN_SECONDS = 3.0
_DRAIN_SETTLE_MARGIN = 2.0


def derive_state_checkpoints(jsonl_path, validation_delay=0.0):
    """Extract structural state checkpoints from a recording's STATE_SNAPSHOTs.

    Each periodic ``STATE_SNAPSHOT`` becomes a candidate checkpoint carrying the
    snapshot's ``time_offset`` and its canonical projection (the expected state).
    A snapshot is only kept if no structural event follows it within
    *validation_delay* seconds — otherwise the recording reorders/inserts after
    the snapshot but before we validate it, and the live state no longer matches
    the snapshot (this is what made the very first snapshot a false failure).

    A recording with no periodic snapshots yields an empty list (the runner then
    falls back to frame-only validation).

    Each checkpoint also carries ``frame_held``: True when the recording is quiet
    of playback (frame) changes for *validation_delay* after the snapshot — i.e.
    the playhead is parked. Frame is only compared at frame-held checkpoints,
    where it is reliable (a moving playhead reads inconsistently across apps, and
    xStudio's ~0.5s file-bridge value lags a live frame; neither matters when the
    frame is parked).

    :param jsonl_path: Path to the JSONL recording.
    :param validation_delay: Required seconds of structural silence after a
        snapshot for it to be a valid checkpoint, and of playback silence for it
        to be frame-held.
    :returns: List of ``{"time_offset", "expected", "frame_held"}`` dicts,
        ordered by ``time_offset``.
    """
    snapshots = []          # (time_offset, projection)
    structural_times = []   # offsets of structure/selection-changing events
    playback_times = []     # offsets of PLAYBACK_SETTINGS (frame) changes
    with open(jsonl_path, 'r') as f:
        for line in f:
            try:
                row = json.loads(line.strip())
            except Exception:
                continue
            t = row.get("time_offset", 0.0)
            p = row.get("payload", {}).get("payload", {})
            schema = p.get("command_schema")
            if schema == "LiveSession.1" and p.get("command", {}).get("event") == "STATE_SNAPSHOT":
                snapshots.append((t, project_state(p.get("command", {}).get("payload", {}))))
            elif schema in _STRUCTURAL_SCHEMAS:
                structural_times.append(t)
            elif schema == "PLAYBACK_SETTINGS_1.0":
                playback_times.append(t)

    structural_times.sort()
    playback_times.sort()
    checkpoints = []
    for t, proj in snapshots:
        # Next structural event at or after this snapshot (>= t is conservative:
        # an event sharing the snapshot's offset disqualifies it).
        idx = bisect.bisect_left(structural_times, t)
        next_struct = structural_times[idx] if idx < len(structural_times) else float("inf")
        # Require validation_delay + _SCP_SILENCE_MARGIN of structural quiet so
        # the runner's polling window (which can start late by up to ~0.5 s and
        # polls for up to several seconds) does not overlap with the next
        # structural mutation and catch the apps in a later state.  A gap that
        # is only marginally larger than validation_delay (e.g. 4.64 s when
        # delay=4.5) means the first MOVE_CHILD after the snapshot fires before
        # the inspector even returns the first response, causing a false failure.
        if validation_delay > 0 and (next_struct - t) < (validation_delay + _SCP_SILENCE_MARGIN):
            continue
        # Frame-held: no playback change for validation_delay + safety margin
        # after the snapshot (same margin as the frame checkpoints, so a brief
        # mid-scrub pause is never treated as a parked frame).
        pidx = bisect.bisect_right(playback_times, t)
        next_play = playback_times[pidx] if pidx < len(playback_times) else float("inf")
        frame_held = (next_play - t) >= validation_delay + _FRAME_HOLD_SAFETY_MARGIN
        checkpoints.append({"time_offset": t, "expected": proj, "frame_held": frame_held})
    checkpoints.sort(key=lambda c: c["time_offset"])
    return checkpoints


def recording_has_annotations(jsonl_path):
    """Return True if the recording contains live ``Annotation.1`` stroke messages.

    Deliberately narrow: it triggers only on the live partial/full stroke stream
    (the scenario where strokes must be drawn on receive), not on annotation
    clips merely baked into a snapshot — so the annotation-presence check does
    not fire for unrelated tests.
    """
    try:
        with open(jsonl_path, 'r') as f:
            for line in f:
                try:
                    row = json.loads(line.strip())
                except Exception:
                    continue
                p = row.get("payload", {}).get("payload", {})
                if p.get("command_schema") == "Annotation.1":
                    return True
    except OSError:
        return False
    return False


def derive_commands_from_recording(jsonl_path):
    """Parses an OTIO Sync Session recording and translates it into high-level
    commands for script-driven tests.
    """
    commands = []
    guid_to_name = {}

    # Track the current clip-selection "segment" — the span of frames the
    # recording actually shuttled through while a given clip/sequence was
    # selected — so a `set_frame` command can be derived alongside each
    # `set_selection`, using the frame the segment ends on (not just its
    # first sample). Reset whenever a new set_selection is appended,
    # regardless of which branch below appended it.
    segment_name = None
    segment_first_frame = None
    segment_last_frame = None

    def _append_selection(name):
        nonlocal segment_name, segment_first_frame, segment_last_frame
        commands.append({"action": "set_selection", "name": name})
        segment_name = name
        segment_first_frame = None
        segment_last_frame = None

    def _flush_shuttle():
        # Only emit a shuttle if the playhead actually moved within the
        # segment — a bare clip switch shouldn't manufacture a no-op seek.
        if (segment_last_frame is not None
                and segment_first_frame is not None
                and segment_last_frame != segment_first_frame):
            commands.append({"action": "set_frame", "frame": int(segment_last_frame)})

    with open(jsonl_path, 'r') as f:
        for line in f:
            try:
                row = json.loads(line.strip())
                envelope = row.get("payload", {})
                p = envelope.get("payload", {})
                command_schema = p.get("command_schema")
                event = p.get("command", {}).get("event")
                inner = p.get("command", {}).get("payload", {})

                if command_schema == "OTIO_SESSION_1.0" and event == "INSERT_CHILD":
                    child = inner.get("child_data", {})
                    schema = child.get("OTIO_SCHEMA", "")
                    name = child.get("name", "")
                    guid = child.get("metadata", {}).get("sync", {}).get("guid")
                    if guid and name:
                        guid_to_name[guid] = name

                    if schema.startswith("Clip."):
                        refs = child.get("media_references", {})
                        default_ref = refs.get("DEFAULT_MEDIA", {})
                        if default_ref.get("OTIO_SCHEMA", "").startswith("ExternalReference"):
                            url = default_ref.get("target_url")
                            if url:
                                if url.startswith("file://"):
                                    abs_url = url.replace("file://localhost", "").replace("file://", "")
                                elif not os.path.isabs(url):
                                    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                                    abs_url = os.path.join(repo_root, url)
                                else:
                                    abs_url = url
                                if not any(c.get("action") == "add_media" and c.get("url") == abs_url for c in commands):
                                    commands.append({"action": "add_media", "url": abs_url})

                elif command_schema == "OTIO_SESSION_1.0" and event == "REMOVE_CHILD":
                    child_guid = inner.get("child_uuid")
                    if child_guid:
                        name = guid_to_name.get(child_guid)
                        if name:
                            commands.append({"action": "delete_media", "name": name})

                elif command_schema == "LiveSession.1" and event == "STATE_SNAPSHOT":
                    timelines = inner.get("timelines", {})
                    for tl_guid, tl in timelines.items():
                        tl_name = tl.get("name", "")
                        guid_to_name[tl_guid] = tl_name
                        for track in tl.get("tracks", {}).get("children", []):
                            for clip in track.get("children", []):
                                c_guid = clip.get("metadata", {}).get("sync", {}).get("guid")
                                c_name = clip.get("name", "")
                                if c_guid and c_name:
                                    guid_to_name[c_guid] = c_name

                                refs = clip.get("media_references", {})
                                default_ref = refs.get("DEFAULT_MEDIA", {})
                                if default_ref.get("OTIO_SCHEMA", "").startswith("ExternalReference"):
                                    url = default_ref.get("target_url")
                                    if url:
                                        if url.startswith("file://"):
                                            abs_url = url.replace("file://localhost", "").replace("file://", "")
                                        elif not os.path.isabs(url):
                                            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                                            abs_url = os.path.join(repo_root, url)
                                        else:
                                            abs_url = url
                                        if not any(c.get("action") == "add_media" and c.get("url") == abs_url for c in commands):
                                            commands.append({"action": "add_media", "url": abs_url})

                elif command_schema == "PLAYBACK_SETTINGS_1.0" and event == "SET":
                    tl_guid = inner.get("timeline_guid")
                    clip_guid = inner.get("clip_guid")
                    # A directly-viewed clip (`view_mode: "source"`) gets an
                    # ephemeral per-view timeline_guid that never appears in
                    # guid_to_name — it isn't part of any recorded
                    # timeline/OTIO structure. clip_guid is the stable
                    # identity for that selection, so fall back to it
                    # whenever timeline_guid doesn't resolve; otherwise every
                    # direct clip select (as opposed to switching back to a
                    # named sequence/playlist) is silently dropped.
                    name = guid_to_name.get(tl_guid) if tl_guid else None
                    if not name and clip_guid:
                        name = guid_to_name.get(clip_guid)
                    if name:
                        if name != segment_name:
                            _flush_shuttle()
                            _append_selection(name)
                        ct = inner.get("current_time", {})
                        frame = ct.get("value")
                        if frame is not None:
                            if segment_first_frame is None:
                                segment_first_frame = frame
                            segment_last_frame = frame

                elif command_schema == "SELECTION_1.0" and event == "SET":
                    selected_guids = inner.get("selected_guids", [])
                    if selected_guids:
                        clip_guid = selected_guids[0]
                        name = guid_to_name.get(clip_guid)
                        if name and name != segment_name:
                            _flush_shuttle()
                            _append_selection(name)

            except Exception:
                continue
    _flush_shuttle()
    return commands
