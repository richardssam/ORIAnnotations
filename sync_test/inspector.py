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
    def __init__(self, port, get_state_callback):
        self.port = port
        self.get_state_callback = get_state_callback
        self.server = None
        self.thread = None

    def start(self):
        callback = self.get_state_callback

        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/state':
                    try:
                        state = callback()
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(state).encode('utf-8'))
                    except Exception as e:
                        logging.error(f"Error getting state: {e}")
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

        self.server = ReusableTCPServer(("localhost", self.port), Handler)
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
