import time
import urllib.request
import json
import logging
import sys
import os

from .spawner import AppSpawner
from .config import SyncTestConfig

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
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=5.0) as response:
                data = response.read()
                return json.loads(data.decode('utf-8'))
        except Exception as e:
            return {"error": str(e)}

    def compare_states(self, states, app_names):
        # We need to compare states from all apps to ensure they match
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
            
            # Normalize clip names (e.g. "Default Sequence" vs "defaultSequence" vs "Sequence")
            if s1.get("clip") and s2.get("clip"):
                def normalize(name):
                    n = str(name).replace(" ", "").lower().replace("sequence", "")
                    return "" if n == "default" else n
                    
                c1 = normalize(s1["clip"])
                c2 = normalize(s2["clip"])
                
                if c1 == c2:
                    s1["clip"] = s2["clip"]  # make them identical for comparison
                    
            if s1 != s2:
                diff_msg = f"Mismatch between {app_names[0]} and {app_names[i]}:\n"
                diff_msg += f"{app_names[0]}: {json.dumps(s1)}\n"
                diff_msg += f"{app_names[i]}: {json.dumps(s2)}\n"
                return False, diff_msg
                
        return True, ""

    def run_test(self, test_name, script_driven=False):
        if SyncPlayer is None:
            raise RuntimeError("Cannot import sync_recorder.player.SyncPlayer")

        test_data = self.config.get_test(test_name)
        if not test_data:
            logging.error(f"Test '{test_name}' not found in configuration.")
            return False
            
        recording_path = os.path.join(os.path.dirname(self.config_path), test_data['recording'])
        apps = test_data['apps']
        
        # Override with test config if the global flag is false
        script_driven = script_driven or test_data.get('script_driven', False)
        
        print(f"\n{'='*70}")
        print(f"  ▶ RUNNING TEST: {test_name}")
        print(f"{'='*70}")
        logging.info(f"Starting test '{test_name}' with apps: {apps}")
        
        executables = self.config.settings.get('executables', {})
        with AppSpawner(test_name, executables) as spawner:
            player = None
            player_thread = None
            playing_state = {"playing": True}
            
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
                logging.info("Starting playback (waiting for peer)...")
                player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=2.0)
                
                # Start player ticking in a background thread so it can respond to WHO_IS_MASTER
                # while apps are launching (since spawner.launch blocks).
                import threading
                
                def player_thread_func():
                    while playing_state["playing"]:
                        if not player.tick():
                            playing_state["playing"] = False
                        time.sleep(0.01)
                        
                player_thread = threading.Thread(target=player_thread_func, daemon=True)
                player_thread.start()
            
            app_ports = []
            base_port = 9000
            for i, app_name in enumerate(apps):
                port = base_port + i
                spawner.launch(app_name, port)
                app_ports.append((app_name, port))
                
            logging.info("Apps launched. Waiting for them to settle (5s)...")
            time.sleep(5.0)
            
            failed = False
            fail_reason = ""
            
            last_check_time = time.time()
            mismatch_start_time = None
            MAX_DIVERGENCE_TIME = 10.0
            
            if script_driven:
                # Drive the UI by sending the derived commands to the first app
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
                # Check state occasionally (e.g. every 0.5 seconds)
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
                            
                            # Provide logs paths
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
                
                time.sleep(0.5)
                
            if player:
                player.stop_playback()
                player_thread.join(timeout=1.0)
            else:
                # Script-driven convergence check at the end
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

def derive_commands_from_recording(jsonl_path):
    """
    Parses an OTIO Sync Session recording and translates it back into high-level
    commands (e.g., 'add_media', 'set_selection') for script-driven tests.
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
                                import os
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
                                        import os
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



