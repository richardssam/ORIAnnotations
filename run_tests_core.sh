#!/bin/bash
# Run core OTIO Sync tests.
# This uses the standard python environment. Pytest will automatically skip 
# any tests marked with 'xstudio' if the xstudio module is missing.

pytest tests/otio_sync/ "$@"
