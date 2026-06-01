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
        self.config = SyncTestConfig.from_file(config_path)

    def fetch_state(self, port):
        url = f"http://localhost:{port}/state"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as response:
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
            
            # Normalize clip names (e.g. "Default Sequence" vs "defaultSequence")
            if s1.get("clip") and s2.get("clip"):
                c1 = str(s1["clip"]).replace(" ", "").lower()
                c2 = str(s2["clip"]).replace(" ", "").lower()
                if c1 == c2:
                    s1["clip"] = c1
                    s2["clip"] = c1
                    
            if s1 != s2:
                diff_msg = f"Mismatch between {app_names[0]} and {app_names[i]}:\n"
                diff_msg += f"{app_names[0]}: {json.dumps(s1)}\n"
                diff_msg += f"{app_names[i]}: {json.dumps(s2)}\n"
                return False, diff_msg
                
        return True, ""

    def run_test(self, test_name):
        if SyncPlayer is None:
            raise RuntimeError("Cannot import sync_recorder.player.SyncPlayer")

        test_data = self.config.get_test(test_name)
        if not test_data:
            raise ValueError(f"Test '{test_name}' not found in config.")

        recording_path = os.path.join(sync_test_dir, test_data['recording'])
        apps = test_data['apps']
        
        logging.info(f"Starting test '{test_name}' with apps: {apps}")
        
        executables = self.config.settings.get('executables', {})
        with AppSpawner(test_name, executables) as spawner:
            player = SyncPlayer(session_id="otio-sync-demo")
            player.load_recording(recording_path)
            logging.info("Starting playback (waiting for peer)...")
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=2.0)
            
            # Start player ticking in a background thread so it can respond to WHO_IS_MASTER
            # while apps are launching (since spawner.launch blocks).
            import threading
            playing_state = {"playing": True}
            
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
                
                time.sleep(0.01)
                
            player.stop_playback()
            player_thread.join(timeout=1.0)
                
            if failed:
                logging.error(f"Test '{test_name}' FAILED.")
                return False
            else:
                logging.info(f"✅ Test '{test_name}' PASSED.")
                return True

    def run_all(self):
        all_passed = True
        for t in self.config.tests:
            if not self.run_test(t['name']):
                all_passed = False
                
        return all_passed
