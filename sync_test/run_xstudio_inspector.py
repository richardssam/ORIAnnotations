#!/usr/bin/env python
import sys
import time

def main():
    if len(sys.argv) < 3:
        print("Usage: run_xstudio_inspector.py <http_port> <xstudio_port>")
        sys.exit(1)
        
    http_port = int(sys.argv[1])
    xstudio_port = int(sys.argv[2])
    
    # We must append the parent dir to sys.path to import sync_test
    import os
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
        
    from sync_test.xstudio_hook import start_xstudio_inspector
    
    print(f"Starting XStudio Inspector Server on HTTP port {http_port}, bridging to XStudio port {xstudio_port}...")
    server = start_xstudio_inspector(http_port, xstudio_port)
    
    # Keep the main thread alive so the daemon thread can serve requests
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down XStudio Inspector Server.")
        server.stop()

if __name__ == "__main__":
    main()
