import subprocess
import os
import time
import logging

class AppSpawner:
    """
    Manages launching XStudio and OpenRV as subprocesses, handles log redirection,
    and ensures clean teardown without leaving zombie processes.
    """
    def __init__(self, test_name, executables=None):
        self.test_name = test_name
        self.executables = executables or {}
        self.processes = []
        self.log_files = []
        
        # Ensure log directory exists
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.logs_dir = os.path.join(base_dir, "logs", test_name)
        os.makedirs(self.logs_dir, exist_ok=True)
        self.base_dir = base_dir

    def launch(self, app_name, http_port):
        log_path = os.path.join(self.logs_dir, f"{app_name}_{http_port}.log")
        log_file = open(log_path, 'w')
        self.log_files.append(log_file)
        
        if app_name == "xstudio":
            # Paths to xStudio and its python interpreter
            xstudio_bin = self.executables.get("xstudio", "xstudio")
            python_bin = self.executables.get("xstudio_python", "python3")
            
            # Launch XStudio
            cmd = [xstudio_bin]
            logging.info(f"Launching XStudio. Logging to {log_path}")
            # Configure environment variables for the plugins
            env = os.environ.copy()
            plugin_log_path = os.path.join(self.logs_dir, f"xstudio_plugin.log")
            env["ORI_SYNC_LOG_FILE"] = plugin_log_path
            
            repo_root = os.path.abspath(os.path.join(self.base_dir, ".."))
            env["XSTUDIO_PYTHON_PLUGIN_PATH"] = os.path.join(repo_root, "xstudio_plugin")
            
            python_path = os.environ.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{os.path.join(repo_root, 'python')}:{python_path}"
            env["OTIO_PLUGIN_MANIFEST_PATH"] = os.path.join(repo_root, "otio_event_plugin", "plugin_manifest.json")
            env["ORI_SESSION"] = "otio-sync-demo"
            
            p = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
            self.processes.append((app_name, p))
            
            # Read log file to find xstudio's dynamically allocated API port
            xstudio_api_port = "14441"
            import re
            
            start_time = time.time()
            found = False
            with open(log_path, 'r') as f:
                while time.time() - start_time < 5.0:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                        
                    match = re.search(r"API enabled on [^:]+:(\d+)", line)
                    if match:
                        xstudio_api_port = match.group(1)
                        found = True
                        break
                        
            if not found:
                logging.warning(f"Could not read xstudio API port from log, defaulting to {xstudio_api_port}")
            else:
                logging.info(f"Detected xStudio API port: {xstudio_api_port}")
            
            # Give XStudio a moment to start its internal API server
            time.sleep(2.0)
            
            # Launch the companion Inspection Server
            inspector_script = os.path.join(self.base_dir, "python", "sync_test", "run_xstudio_inspector.py")
            inspector_log_path = os.path.join(self.logs_dir, f"xstudio_inspector_{http_port}.log")
            inspector_log = open(inspector_log_path, 'w')
            self.log_files.append(inspector_log)
            
            inspector_cmd = [python_bin, "-u", inspector_script, str(http_port), xstudio_api_port]
            logging.info(f"Launching XStudio Inspector on port {http_port} (talking to xstudio on {xstudio_api_port})")
            ip = subprocess.Popen(inspector_cmd, stdout=inspector_log, stderr=subprocess.STDOUT)
            self.processes.append(("xstudio_inspector", ip))
            
        elif app_name == "openrv":
            # For OpenRV, we can inject the python code directly using -pyeval
            pyeval_cmd = (
                f"import sys; sys.path.insert(0, '{self.base_dir}/python'); "
                f"import sync_test.openrv_hook as hook; "
                f"hook.start_openrv_inspector({http_port})"
            )
            openrv_bin = self.executables.get("openrv", "rv")
            cmd = [openrv_bin, "-pyeval", pyeval_cmd]
            logging.info(f"Launching OpenRV on port {http_port}. Logging to {log_path}")
            
            env = os.environ.copy()
            plugin_log_path = os.path.join(self.logs_dir, f"openrv_plugin.log")
            env["RV_OTIO_SYNC_LOG_FILE"] = plugin_log_path
            env["ORI_SESSION"] = "otio-sync-demo"
            env["RV_NO_CONSOLE_REDIRECT"] = "1"
            
            p = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
            self.processes.append((app_name, p))
            
        else:
            raise ValueError(f"Unknown app: {app_name}")

        # Wait for the app and inspector to fully initialize
        time.sleep(2.0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.teardown()

    def teardown(self):
        logging.info("Tearing down spawned applications...")
        # Terminate in reverse order (inspectors first, then apps)
        for app_name, p in reversed(self.processes):
            logging.info(f"Terminating {app_name} (PID: {p.pid})")
            p.terminate()
            try:
                p.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logging.warning(f"{app_name} did not terminate gracefully, killing...")
                p.kill()
        
        for f in self.log_files:
            try:
                f.close()
            except Exception:
                pass
                
        self.processes = []
        self.log_files = []
