#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Batch script to automate xStudio execution, OTIO import,
and image snapshot rendering of drawings.

Launches a non-interactive xStudio instance on a custom port (keeping the Qt
GUI active so OpenGL is available for rendering snapshots), imports the
specified OTIO annotation file (which automatically loads target media),
renders annotation drawing PNGs, and terminates xStudio.

Usage:
    python debug/batch_annotations.py <path_to_otio> [output_directory]
"""

import os
import sys

# Auto-re-execute using the correct xStudio Python interpreter if not already running under it
target_python = "/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3"
if os.path.exists(target_python) and sys.executable != target_python:
    os.execv(target_python, [target_python] + sys.argv)

import time
import shutil
import subprocess

# 1. Setup Python paths to access xStudio Python API, our plugin, and Python modules
xstudio_site_packages = "/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages"
if os.path.exists(xstudio_site_packages) and xstudio_site_packages not in sys.path:
    sys.path.insert(0, xstudio_site_packages)

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(repo_root, "python"))
sys.path.insert(0, os.path.join(repo_root, "xstudio_plugin/ori_annotations"))

try:
    from xstudio.connection import Connection
    from ori_annotations import ORIAnnotationsPlugin
except ImportError as e:
    print(f"Error: Could not import xStudio modules: {e}")
    sys.exit(1)

def batch_process(otio_path, output_dir):
    otio_path = os.path.abspath(otio_path)
    output_dir = os.path.abspath(output_dir)

    if not os.path.exists(otio_path):
        print(f"Error: OTIO file not found: {otio_path}")
        sys.exit(1)

    print(f"Processing annotations file: {otio_path}")
    print(f"Rendering outputs to: {output_dir}")

    # Prepare output directory
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # Launch automated xStudio instance
    xstudio_bin = "/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/MacOS/xstudio.bin"
    port = 14455  # Custom port for automated execution to avoid conflicts

    # Generate flat preference override JSON file to force xStudio to bind to our custom port
    import tempfile
    import json
    pref_data = {
        "/core/api/port_minimum/value": port,
        "/core/api/port_maximum/value": port,
        "/core/api/enabled/value": True
    }
    pref_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(pref_data, pref_file)
    pref_file.close()
    pref_file_path = pref_file.name

    # Redirect stdout/stderr to log files inside output_dir
    stdout_log_path = os.path.join(output_dir, "xstudio_stdout.log")
    stderr_log_path = os.path.join(output_dir, "xstudio_stderr.log")
    stdout_log = open(stdout_log_path, "w")
    stderr_log = open(stderr_log_path, "w")

    cmd = [
        xstudio_bin,
        f"--override-pref={pref_file_path}",
        "--new-session",
        "--user-prefs-off"
    ]

    print(f"Starting xStudio on port {port}...")
    proc = subprocess.Popen(cmd, stdout=stdout_log, stderr=stderr_log)

    conn = Connection(auto_connect=False)
    connected = False
    print("Waiting for xStudio remote API to become active...")
    for attempt in range(25):
        try:
            conn.connect_remote("127.0.0.1", port)
            connected = True
            print("Connected successfully to xStudio!")
            break
        except Exception:
            time.sleep(0.5)

    if not connected:
        print("Error: Failed to connect to xStudio within timeout.")
        proc.terminate()
        if os.path.exists(pref_file_path):
            os.unlink(pref_file_path)
        sys.exit(1)

    try:
        # Instantiate the annotations plugin
        plugin = ORIAnnotationsPlugin(conn)

        # Import the annotations (creates the playlist and auto-loads media if missing)
        print("Importing annotations timeline...")
        success, message = plugin.import_annotations(otio_path)
        print(f"Import status: {'SUCCESS' if success else 'FAILED'}")
        print(f"Import message: {message}")

        if not success:
            print("Aborting. Import failed.")
            sys.exit(1)

        # Allow time for xStudio to resolve media reference headers
        print("Waiting 5 seconds for media files to load and resolve in xStudio...")
        time.sleep(5)

        # Export and render snapshots
        print("Exporting and rendering annotation overlay PNGs...")
        success, message = plugin.export_annotations(
            output_folder=output_dir,
            otio_name=os.path.basename(otio_path),
            include_media=False,
            include_images=True
        )
        print(f"Export status: {'SUCCESS' if success else 'FAILED'}")
        print(f"Export message: {message}")

        # List the generated files
        print("\nGenerated files:")
        generated = os.listdir(output_dir)
        for f in sorted(generated):
            if f.endswith(".png") or f.endswith(".otio"):
                path = os.path.join(output_dir, f)
                size = os.path.getsize(path)
                print(f"  - {f} ({size} bytes)")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Batch execution failed with exception: {e}")
    finally:
        print("Closing connection...")
        conn.disconnect()
        print("Terminating xStudio...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("xStudio did not exit cleanly. Killing process...")
            proc.kill()
        
        stdout_log.close()
        stderr_log.close()
        
        # Clean up preference override file
        if os.path.exists(pref_file_path):
            try:
                os.unlink(pref_file_path)
            except Exception as ce:
                print(f"Failed to delete preference override file {pref_file_path}: {ce}")
        print("Batch processing finished.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug/batch_annotations.py <path_to_otio> [output_directory]")
        sys.exit(1)

    otio_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(repo_root, "batch_output")
    batch_process(otio_file, out_dir)
