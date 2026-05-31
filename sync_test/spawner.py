import subprocess
import os
import time
import logging

class AppSpawner:
    """
    Manages launching XStudio and OpenRV as subprocesses, handles log redirection,
    and ensures clean teardown without leaving zombie processes.
    """
    def __init__(self, test_name):
        self.test_name = test_name
        self.processes = []
        self.log_files = []
        
        # Ensure log directory exists
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.logs_dir = os.path.join(base_dir, "logs", test_name)
        os.makedirs(self.logs_dir, exist_ok=True)
        self.base_dir = base_dir

    def launch(self, app_name, http_port):
        log_path = os.path.join(self.logs_dir, f"{app_name}_{http_port}.log")
        log_file = open(log_path, 'w')
        self.log_files.append(log_file)
        
        if app_name == "xstudio":
            # Paths to xStudio and its python interpreter
            xstudio_bin = "/Users/sam/git/xstudio/build/bin/xstudio"
            python_bin = "/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3"
            
            # Launch XStudio
            cmd = [xstudio_bin]
            logging.info(f"Launching XStudio. Logging to {log_path}")
            p = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            self.processes.append((app_name, p))
            
            # Give XStudio a moment to start its internal API server
            time.sleep(2.0)
            
            # Launch the companion Inspection Server
            inspector_script = os.path.join(self.base_dir, "sync_test", "run_xstudio_inspector.py")
            inspector_log_path = os.path.join(self.logs_dir, f"xstudio_inspector_{http_port}.log")
            inspector_log = open(inspector_log_path, 'w')
            self.log_files.append(inspector_log)
            
            inspector_cmd = [python_bin, inspector_script, str(http_port), "14441"]
            logging.info(f"Launching XStudio Inspector on port {http_port}")
            ip = subprocess.Popen(inspector_cmd, stdout=inspector_log, stderr=subprocess.STDOUT)
            self.processes.append(("xstudio_inspector", ip))
            
        elif app_name == "openrv":
            # For OpenRV, we can inject the python code directly using -pyeval
            pyeval_cmd = (
                f"import sys; sys.path.insert(0, '{self.base_dir}'); "
                f"import sync_test.openrv_hook as hook; "
                f"hook.start_openrv_inspector({http_port})"
            )
            cmd = ["rv", "-pyeval", pyeval_cmd]
            logging.info(f"Launching OpenRV on port {http_port}. Logging to {log_path}")
            p = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
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
