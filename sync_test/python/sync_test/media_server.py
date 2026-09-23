import http.server
import os
import re
import socketserver
import threading
import logging


class MediaServer:
    """
    A small HTTP server for serving media files to test scenarios, with
    HTTP range-request support.

    Plain ``http.server.SimpleHTTPRequestHandler`` does not support
    ``Range:`` requests, which real media readers (confirmed for both
    OpenRV's ``MovieFFMpeg`` and xStudio's media backend) rely on to seek
    a container's header/moov atom — without range support, playback fails
    outright (``MovieFFMpeg: Invalid pixel format!``) rather than degrading
    gracefully. See ``docs/http_media_fallback.md``.
    """

    def __init__(self, directory, port):
        self.directory = os.path.abspath(directory)
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        directory = self.directory

        class Handler(http.server.SimpleHTTPRequestHandler):
            def translate_path(self, path):
                # Base translation resolves against cwd; force it against
                # our configured directory instead, and collapse any
                # traversal (../) before comparing against it.
                path = super().translate_path(path)
                cwd = os.getcwd()
                if path.startswith(cwd):
                    path = directory + path[len(cwd):]
                resolved = os.path.realpath(path)
                if os.path.commonpath([resolved, directory]) != directory:
                    return directory  # traversal attempt: serve directory root (404s as a dir)
                return path

            def send_head(self):
                path = self.translate_path(self.path)
                if not os.path.isfile(path):
                    return super().send_head()

                file_size = os.path.getsize(path)
                range_header = self.headers.get("Range")
                if not range_header:
                    self.send_response(200)
                    self.send_header("Content-type", self.guess_type(path))
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    return open(path, "rb")

                m = re.match(r"bytes=(\d*)-(\d*)", range_header)
                if not m:
                    self.send_response(416)
                    self.end_headers()
                    return None
                start, end = m.group(1), m.group(2)
                start = int(start) if start else 0
                end = int(end) if end else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                f = open(path, "rb")
                f.seek(start)
                self.send_response(206)
                self.send_header("Content-type", self.guess_type(path))
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self._range_remaining = length
                return f

            def copyfile(self, source, outputfile):
                if hasattr(self, "_range_remaining"):
                    remaining = self._range_remaining
                    buf_size = 64 * 1024
                    while remaining > 0:
                        chunk = source.read(min(buf_size, remaining))
                        if not chunk:
                            break
                        outputfile.write(chunk)
                        remaining -= len(chunk)
                else:
                    super().copyfile(source, outputfile)

            def log_message(self, format, *args):
                # Suppress default HTTP logging to avoid cluttering test output.
                pass

        class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logging.info(f"Media server started on port {self.port}, serving {self.directory}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=1.0)
            logging.info("Media server stopped.")


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    directory = sys.argv[2] if len(sys.argv) > 2 else "."
    logging.basicConfig(level=logging.INFO)
    server = MediaServer(directory, port)
    server.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
