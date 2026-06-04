import time
import urllib.request
import json
import logging
import sys
import os
import socket

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


def _normalize_clip_name(name):
    return str(name).replace(" ", "").lower().replace("sequence", "")


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

    def send_command(self, port, payload):
        url = f"http://127.0.0.1:{port}/command"
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode('utf-8'), method='POST'
            )
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=5.0) as response:
                data = response.read()
                return json.loads(data.decode('utf-8'))
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

            # Ignore transient states like playing or absolute path strings
            ignore_keys = {"playing", "media_path", "media_exists", "frame"}
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

        recording_path = os.path.join(os.path.dirname(self.config_path), test_data['recording'])
        apps = test_data['apps']

        script_driven = script_driven or test_data.get('script_driven', False)

        # Allow per-test overrides for checkpoint tuning
        checkpoint_validation_delay = test_data.get('checkpoint_validation_delay', checkpoint_validation_delay)
        checkpoint_min_spacing = test_data.get('checkpoint_min_spacing', checkpoint_min_spacing)
        frame_tolerance = test_data.get('frame_tolerance', frame_tolerance)

        print(f"\n{'='*70}")
        print(f"  ▶ RUNNING TEST: {test_name}")
        print(f"{'='*70}")
        logging.info(f"Starting test '{test_name}' with apps: {apps}")

        executables = self.config.settings.get('executables', {})
        with AppSpawner(test_name, executables) as spawner:
            player = None
            player_thread = None
            playing_state = {"playing": True}
            checkpoints = []

            if script_driven:
                if 'commands' in test_data:
                    logging.info(f"Running in script-driven mode. Using {len(test_data['commands'])} commands from config.")
                    commands = test_data['commands']
                else:
                    logging.info(f"Running in script-driven mode. Deriving commands from {recording_path}")
                    commands = derive_commands_from_recording(recording_path)
                    logging.info(f"Derived {len(commands)} commands.")
            else:
                player = SyncPlayer(session_id="otio-sync-demo")
                player.load_recording(recording_path)

                checkpoints = derive_checkpoints(
                    recording_path,
                    min_spacing=checkpoint_min_spacing,
                    frame_tolerance=frame_tolerance,
                )
                logging.info(f"Extracted {len(checkpoints)} validation checkpoints from recording.")

                logging.info("Starting playback (waiting for peer)...")
                player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=2.0)

                import threading

                def player_thread_func():
                    while playing_state["playing"]:
                        if not player.tick():
                            playing_state["playing"] = False
                        time.sleep(0.01)

                player_thread = threading.Thread(target=player_thread_func, daemon=True)
                player_thread.start()

            app_ports = []
            free_ports = _find_free_ports(len(apps))
            for app_name, port in zip(apps, free_ports):
                spawner.launch(app_name, port)
                app_ports.append((app_name, port))

            logging.info("Apps launched. Waiting for them to settle (5s)...")
            time.sleep(5.0)

            failed = False
            fail_reason = ""

            last_check_time = time.time()
            mismatch_start_time = None
            MAX_DIVERGENCE_TIME = 10.0

            checkpoint_idx = 0

            if script_driven:
                driver_app = app_ports[0]
                logging.info(f"Driving {driver_app[0]} via commands...")
                for cmd in commands:
                    logging.info(f"  -> Sending command: {cmd}")
                    res = self.send_command(driver_app[1], cmd)
                    if "error" in res:
                        logging.error(f"Command execution failed: {res['error']}")
                        failed = True
                        break
                    time.sleep(1.0)

                logging.info("Command sequence completed. Waiting for convergence...")
                time.sleep(3.0)
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

            if failed:
                logging.error(f"Test '{test_name}' FAILED.")
                return False
            else:
                logging.info(f"✅ Test '{test_name}' PASSED.")
                return True

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


def derive_checkpoints(jsonl_path, min_spacing=2.0, frame_tolerance=5):
    """Extract validation checkpoints from a recording.

    Returns stable PLAYBACK_SETTINGS positions (not scrubbing, not playing).
    Rapid scrubs produce many events close together; we keep the *last* one
    in each burst so we validate the position the user actually landed on,
    not the first frame they touched.

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

    # Keep the last event in each burst: scan backwards, emit when the gap to
    # the previous emitted entry is >= min_spacing.
    checkpoints = []
    for cp in reversed(raw):
        if not checkpoints or (checkpoints[-1]["time_offset"] - cp["time_offset"]) >= min_spacing:
            checkpoints.append(cp)
    checkpoints.reverse()

    return checkpoints


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
