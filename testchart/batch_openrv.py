#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Batch script to automate OpenRV execution, OTIO import,
and image rendering of drawings.

Usage:
    python testchart/batch_openrv.py <path_to_otio> [output_directory]
"""

import os
import sys
import shutil
import subprocess

def main():
    if len(sys.argv) < 2:
        print("Usage: python testchart/batch_openrv.py <path_to_otio> [output_directory]")
        sys.exit(1)

    otio_path = os.path.abspath(sys.argv[1])
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    if len(sys.argv) > 2:
        output_dir = os.path.abspath(sys.argv[2])
    else:
        output_dir = os.path.join(repo_root, "batch_output_rv")

    if not os.path.exists(otio_path):
        print(f"Error: OTIO file not found: {otio_path}")
        sys.exit(1)

    print(f"Processing annotations file: {otio_path}")
    print(f"Rendering outputs to: {output_dir}")

    # Prepare output directory
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # Set up environment variables for OpenRV
    env = os.environ.copy()
    env["BATCH_OTIO_PATH"] = otio_path
    env["BATCH_OUTPUT_DIR"] = output_dir
    env["PYTHONUNBUFFERED"] = "1"
    env["OTIO_PLUGIN_MANIFEST_PATH"] = os.path.join(repo_root, "otio_event_plugin", "plugin_manifest.json")
    
    # Ensure the repository's python directory is in PYTHONPATH so OpenRV can import ORIAnnotations
    python_dir = os.path.join(repo_root, "python")
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{python_dir}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = python_dir

    # Launch OpenRV with -pyeval to execute the batch processing helper
    rv_bin = "/Applications/OpenRV.app/Contents/MacOS/RV"
    #rv_bin = "/Users/sam/git/openrv_annotations/_build/stage/app/RV.app/Contents/MacOS/RV"
    testchart_dir = os.path.dirname(os.path.abspath(__file__))
    
    cmd = [
        rv_bin,
        "-pyeval",
        f"import sys; sys.path.append('{testchart_dir}'); import batch_openrv_helper; batch_openrv_helper.run_batch()"
    ]

    print("Launching OpenRV...")
    try:
        # Run OpenRV and block until it exits
        subprocess.run(cmd, env=env, check=True)
        print("OpenRV execution completed.")
        
        # List the generated files
        print("\nGenerated files:")
        if os.path.exists(output_dir):
            generated = os.listdir(output_dir)
            for f in sorted(generated):
                if f.endswith(".png") or f.endswith(".otio"):
                    path = os.path.join(output_dir, f)
                    size = os.path.getsize(path)
                    print(f"  - {f} ({size} bytes)")
        else:
            print("Error: Output directory was not created.")
            
    except subprocess.CalledProcessError as e:
        print(f"Error: OpenRV process exited with error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to execute OpenRV batch: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
