#!/bin/bash
# Run xStudio plugin tests using the bundled xStudio Python interpreter.

XSTUDIO_PYTHON="/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3"

if [ ! -f "$XSTUDIO_PYTHON" ]; then
    echo "Error: xStudio Python interpreter not found at $XSTUDIO_PYTHON"
    echo "Please ensure xStudio is built or update the path in this script."
    exit 1
fi

"$XSTUDIO_PYTHON" -m pytest tests/xstudio_plugin/ "$@"
