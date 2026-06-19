import http.server
import socketserver
import threading
import json
import logging

class InspectionServer:
    """
    A lightweight HTTP server meant to be injected into target applications
    (like XStudio or OpenRV) to expose their true internal state for testing.
    """
    def __init__(self, port, get_state_callback, execute_command_callback=None,
                 get_full_state_callback=None):
        self.port = port
        self.get_state_callback = get_state_callback
        self.execute_command_callback = execute_command_callback
        # Optional richer state for structural validation: returns a
        # StateSnapshot-shaped dict suitable for otio_sync_core.project_state.
        self.get_full_state_callback = get_full_state_callback
        self.server = None
        self.thread = None

    def start(self):
        get_callback = self.get_state_callback
        exec_callback = self.execute_command_callback
        full_state_callback = self.get_full_state_callback

        class Handler(http.server.SimpleHTTPRequestHandler):
            def _write_json(self, code, obj):
                self.send_response(code)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(obj).encode('utf-8'))

            def do_GET(self):
                if self.path == '/state':
                    try:
                        self._write_json(200, get_callback())
                    except Exception as e:
                        logging.error(f"Error getting state: {e}")
                        self._write_json(500, {"error": str(e)})
                elif self.path == '/full_state':
                    if not full_state_callback:
                        self._write_json(501, {"error": "full_state not supported"})
                        return
                    try:
                        self._write_json(200, full_state_callback())
                    except Exception as e:
                        logging.error(f"Error getting full state: {e}")
                        self._write_json(500, {"error": str(e)})
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == '/command':
                    if not exec_callback:
                        self.send_response(501)
                        self.end_headers()
                        return
                        
                    try:
                        content_length = int(self.headers.get('Content-Length', 0))
                        post_data = self.rfile.read(content_length)
                        payload = json.loads(post_data.decode('utf-8'))
                        
                        result = exec_callback(payload)
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "ok", "result": result}).encode('utf-8'))
                    except Exception as e:
                        logging.error(f"Error executing command: {e}")
                        self.send_response(500)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        error_res = {"error": str(e)}
                        self.wfile.write(json.dumps(error_res).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                # Suppress default HTTP logging to avoid cluttering the app's stdout
                pass

        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        # Use 127.0.0.1 to avoid IPv6 resolution issues locally
        self.server = ReusableTCPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logging.info(f"Inspection server started on port {self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=1.0)
            logging.info("Inspection server stopped.")
