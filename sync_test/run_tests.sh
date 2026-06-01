#!/usr/bin/env bash
# Wrapper script to run the sync_test framework with the correct PYTHONPATH

# Get the absolute path to the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PYTHON_DIR="${SCRIPT_DIR}/python"

# Export PYTHONPATH so the sync_test package can be found
export PYTHONPATH="${PYTHON_DIR}:${PYTHONPATH}"

# Run the python CLI module from the script directory
cd "${SCRIPT_DIR}"
python -m sync_test.cli "$@"
