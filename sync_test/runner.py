import time
import urllib.request
import json
import logging
import sys
import os

from .spawner import AppSpawner
from .config import SyncTestConfig

# Try to import SyncPlayer from sync_recorder
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

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
            
        for i in range(1, len(states)):
            st = states[i]
            if "error" in st:
                return False, f"{app_names[i]} returned error: {st['error']}"
                
            # Ignore "playing" state mismatch for now, it can be transient
            s1 = {k: v for k, v in base_state.items() if k != "playing"}
            s2 = {k: v for k, v in st.items() if k != "playing"}
            
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

        recording_path = os.path.join(base_dir, test_data['recording'])
        apps = test_data['apps']
        
        logging.info(f"Starting test '{test_name}' with apps: {apps}")
        
        with AppSpawner(test_name) as spawner:
            app_ports = []
            base_port = 9000
            for i, app_name in enumerate(apps):
                port = base_port + i
                spawner.launch(app_name, port)
                app_ports.append((app_name, port))
                
            logging.info("Apps launched. Waiting for them to settle (5s)...")
            time.sleep(5.0)
            
            player = SyncPlayer(session_id="otio-sync-demo")
            player.load_recording(recording_path)
            
            logging.info("Starting playback...")
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=2.0)
            
            failed = False
            fail_reason = ""
            
            playing = True
            last_check_time = time.time()
            
            while playing:
                playing = player.tick()
                
                # Check state occasionally (e.g. every 0.5 seconds)
                if time.time() - last_check_time > 0.5:
                    last_check_time = time.time()
                    
                    states = []
                    for name, port in app_ports:
                        st = self.fetch_state(port)
                        states.append(st)
                    
                    match, diff = self.compare_states(states, [a[0] for a in app_ports])
                    if not match:
                        logging.error(f"❌ FAIL: State mismatch detected in test '{test_name}'!\n{diff}")
                        
                        # Provide logs paths
                        logging.error("Check application logs for details:")
                        for name, port in app_ports:
                            logging.error(f"  {os.path.join(spawner.logs_dir, f'{name}_{port}.log')}")
                            
                        failed = True
                        fail_reason = diff
                        break
                
                time.sleep(0.01)
                
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
