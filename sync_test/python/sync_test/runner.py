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
from collections import Counter

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


class TestRunner:
    def __init__(self, config_path="sync_tests.yaml"):
        self.config_path = config_path
        self.config = SyncTestConfig.from_file(config_path)

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
        if len(states) < 2:
            return True, ""

        base_state = states[0]
        if "error" in base_state:
            return False, f"{app_names[0]} returned error: {base_state['error']}"

        if "media_exists" in base_state and not base_state["media_exists"]:
            return False, f"{app_names[0]} reports missing media: {base_state.get('media_path')}"

        for i in range(1, len(states)):
            st = states[i]
            if "error" in st:
                return False, f"{app_names[i]} returned error: {st['error']}"

            if "media_exists" in st and not st["media_exists"]:
                return False, f"{app_names[i]} reports missing media: {st.get('media_path')}"

            # Ignore transient states like playing or absolute path strings.
            # Annotation fields differ in representation per app (RV stroke
            # components vs xStudio bookmarks) and are checked separately by the
            # annotation-presence check, so exclude them from structural equality.
            ignore_keys = {"playing", "media_path", "media_exists", "frame",
                           "annotations", "annotation_count", "is_master"}
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
                return False, diff_msg

        return True, ""

    def validate_checkpoint(self, states, app_names, checkpoint):
        """Check each app's reported state against a recording checkpoint.

        Only validates fields the app exposes (frame may be None for some apps).
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
            if expected_frame is not None and actual_frame is not None:
                # RV frame() is 1-indexed; PLAYBACK_SETTINGS value is 0-indexed
                adjusted = int(expected_frame) + 1
                if abs(actual_frame - adjusted) > frame_tolerance:
                    messages.append(
                        f"{name}: expected frame ~{adjusted}, got {actual_frame}"
                    )

            actual_clip = state.get("clip")
            if expected_clip and actual_clip is not None:
                if _normalize_clip_name(actual_clip) != _normalize_clip_name(expected_clip):
                    messages.append(
                        f"{name}: expected clip '{expected_clip}', got '{actual_clip}'"
                    )

        if messages:
            t = checkpoint.get("time_offset", 0)
            return False, f"Checkpoint at t={t:.1f}s failed:\n" + "\n".join(messages)
        return True, ""

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

    _ANNOTATION_GEOMETRY_FORMULAS = {
        ("pen", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_pen_width,
        ("pen", "xstudio_to_openrv"): annotation_assertions.expected_rv_width_from_xstudio_pen_thickness,
        ("rect", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_border_width,
        ("ellipse", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_ellipse_border_width,
        ("arrow", "openrv_to_xstudio"): annotation_assertions.expected_xstudio_thickness_from_rv_arrow_thickness,
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
            thicknesses = last.get("stroke_thickness") or []
            actual = thicknesses[-1] if thicknesses else None
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
                 frame_tolerance=5):
        if SyncPlayer is None:
            raise RuntimeError("Cannot import sync_recorder.player.SyncPlayer")

        test_data = self.config.get_test(test_name)
        if not test_data:
            logging.error(f"Test '{test_name}' not found in configuration.")
            return False

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

        print(f"\n{'='*70}")
        print(f"  ▶ RUNNING TEST: {test_name}")
        print(f"{'='*70}")
        logging.info(f"Starting test '{test_name}' with apps: {apps}")

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
            _runner_fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
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
                return False
            logging.info("All apps connected.")

            if not script_driven:
                logging.info("Waiting for all apps to receive the initial snapshot...")
                if not self._wait_for_snapshot(app_ports, timeout=30.0):
                    logging.warning("Apps did not all report a clip within 30s — proceeding anyway.")

            failed = False
            fail_reason = ""

            last_check_time = time.time()
            mismatch_start_time = None
            MAX_DIVERGENCE_TIME = 10.0

            checkpoint_idx = 0
            state_checkpoint_idx = 0

            if script_driven:
                driver_app = app_ports[0]

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
                for cmd in commands:
                    logging.info(f"  -> Sending command: {cmd}")
                    res = self.send_command(driver_app[1], cmd)
                    if "error" in res:
                        logging.error(f"Command execution failed: {res['error']}")
                        failed = True
                        break
                    time.sleep(1.0)

                convergence_wait = float(test_data.get("convergence_wait", 3.0))
                logging.info(f"Command sequence completed. Waiting {convergence_wait}s for convergence...")
                time.sleep(convergence_wait)
                playing_state["playing"] = False

            while playing_state["playing"]:
                if time.time() - last_check_time > 0.5:
                    last_check_time = time.time()

                    states = []
                    for name, port in app_ports:
                        st = self.fetch_state(port)
                        states.append(st)

                    match, diff = self.compare_states(states, [a[0] for a in app_ports])
                    if not match:
                        if mismatch_start_time is None:
                            mismatch_start_time = time.time()
                            logging.warning(f"Transient mismatch detected, waiting for convergence...\n{diff}")
                        elif time.time() - mismatch_start_time > MAX_DIVERGENCE_TIME:
                            logging.error(f"❌ FAIL: State mismatch persisted for >{MAX_DIVERGENCE_TIME}s in test '{test_name}'!\n{diff}")
                            logging.error("Check application logs for details:")
                            for name, port in app_ports:
                                logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                            failed = True
                            fail_reason = diff
                            playing_state["playing"] = False
                            break
                    else:
                        if mismatch_start_time is not None:
                            logging.info("✅ States have converged again.")
                            mismatch_start_time = None

                    # Checkpoint validation: once enough time has passed after a checkpoint
                    # event was dispatched, check that apps reflect the expected state.
                    if player and player._play_start_time is not None:
                        current_offset = (time.time() - player._play_start_time) * 1.0
                        while checkpoint_idx < len(checkpoints):
                            cp = checkpoints[checkpoint_idx]
                            if current_offset < cp["time_offset"] + checkpoint_validation_delay:
                                break
                            ok, msg = self.validate_checkpoint(states, [a[0] for a in app_ports], cp)
                            if ok:
                                logging.info(
                                    f"✅ Checkpoint t={cp['time_offset']:.1f}s passed "
                                    f"(frame={cp.get('frame')}, clip={cp.get('timeline_name')})"
                                )
                            else:
                                logging.error(f"❌ FAIL: {msg}")
                                logging.error("Check application logs for details:")
                                for name, port in app_ports:
                                    logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                                failed = True
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
                            ok, msg = False, ""
                            scp_deadline = time.time() + 10.0
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
                                    break
                                time.sleep(0.5)
                            if ok:
                                logging.info(
                                    f"✅ State checkpoint t={scp['time_offset']:.1f}s passed"
                                    + (f" ({msg})" if msg else "")
                                )
                            else:
                                logging.error(f"❌ FAIL: {msg}")
                                logging.error("Check application logs for details:")
                                for name, port in app_ports:
                                    logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                                failed = True
                                fail_reason = msg
                                playing_state["playing"] = False
                            state_checkpoint_idx += 1

                time.sleep(0.5)

            if player:
                player.stop_playback()
                player_thread.join(timeout=1.0)
            else:
                # Script-driven: final coherence check
                states = []
                for name, port in app_ports:
                    st = self.fetch_state(port)
                    states.append(st)
                match, diff = self.compare_states(states, [a[0] for a in app_ports])
                if not match:
                    logging.error(f"❌ FAIL: Final state mismatch in test '{test_name}'!\n{diff}")
                    failed = True

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
                        fail_reason = f"{name} created no annotations"
                    else:
                        logging.info(f"✅ {name} created {cnt} annotation stroke(s)")

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
                deadline = time.time() + 15.0
                while True:
                    full_states = [self.fetch_full_state(port) for _, port in app_ports]
                    ok, msg = self.compare_full_states(
                        full_states, [a[0] for a in app_ports], frame_tolerance=frame_tolerance
                    )
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(1.0)
                if not ok:
                    logging.error(f"❌ FAIL: structural consensus in '{test_name}':\n{msg}")
                    failed = True
                    fail_reason = msg
                else:
                    logging.info("✅ Apps agree on timeline structure (full-state consensus)")

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
                except Exception as e:
                    logging.error(f"❌ FAIL: otio_compare block raised: {e}", exc_info=True)
                    failed = True

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

            self._report_log_errors(app_ports, spawner.logs_dir)

            if failed:
                logging.error(f"Test '{test_name}' FAILED.")
            else:
                logging.info(f"✅ Test '{test_name}' PASSED.")

            logging.getLogger().removeHandler(_runner_fh)
            _runner_fh.close()

            if failed:
                return False
            return True

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

    def _report_log_errors(self, app_ports, logs_dir):
        """Scan all app log files and emit a warning for any exceptions found."""
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

    def run_all(self, script_driven=False):
        results = {}
        for t in self.config.tests:
            test_name = t['name']
            success = self.run_test(test_name, script_driven=script_driven)
            results[test_name] = success

        print("\n" + "="*70)
        print("  SYNC TEST SUMMARY")
        print("="*70)
        all_passed = True
        for test_name, success in results.items():
            status = "✅ PASSED" if success else "❌ FAILED"
            print(f"  {status}  |  {test_name}")
            if not success:
                all_passed = False
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
                    if tl_guid:
                        name = guid_to_name.get(tl_guid)
                        if name:
                            last_sel = next((c for c in reversed(commands) if c.get("action") == "set_selection"), None)
                            if not last_sel or last_sel.get("name") != name:
                                commands.append({"action": "set_selection", "name": name})

                elif command_schema == "SELECTION_1.0" and event == "SET":
                    selected_guids = inner.get("selected_guids", [])
                    if selected_guids:
                        clip_guid = selected_guids[0]
                        name = guid_to_name.get(clip_guid)
                        if name:
                            last_sel = next((c for c in reversed(commands) if c.get("action") == "set_selection"), None)
                            if not last_sel or last_sel.get("name") != name:
                                commands.append({"action": "set_selection", "name": name})

            except Exception:
                continue
    return commands
