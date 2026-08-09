import pytest
import sys

# Attempt to import xstudio, skip the whole directory if not available.
try:
    import xstudio.core
    _has_xstudio = True
except ImportError:
    _has_xstudio = False

# Only apply the skip at collection time if xstudio is missing
if not _has_xstudio:
    pytest.skip("xStudio python environment required. Use run_tests_xstudio.sh", allow_module_level=True)

# Add the xstudio marker to all tests in this directory
def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.xstudio)
