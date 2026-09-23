#!/usr/bin/env python3
"""Runs the ``http_media_fallback`` sync_test scenario end to end.

Starts the Range-capable media server (see ``sync_test/python/sync_test/media_server.py``
and ``openspec/changes/http-media-fallback``) pointed at the same
``test_media/source/encoded/`` directory used by the scenario's host
peer, runs the scenario, and tears the server down afterward — the same
"external service the test needs already running" pattern this project
already assumes for RabbitMQ, kept self-contained here instead of pushed
onto whoever runs the suite.
"""

import logging
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, "python"))

from sync_test.media_server import MediaServer  # noqa: E402
from sync_test.runner import TestRunner  # noqa: E402

MEDIA_DIR = os.path.join(REPO_ROOT, "test_media", "source", "encoded")
MEDIA_PORT = 8899
CONFIG = os.path.join(SCRIPT_DIR, "sync_tests_xstudio.yaml")
TEST_NAME = "http_media_fallback"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    server = MediaServer(MEDIA_DIR, MEDIA_PORT)
    server.start()
    try:
        runner = TestRunner(CONFIG)
        result = runner.run_test(TEST_NAME, script_driven=True)
        if result.passed:
            logging.info(f"Result: PASSED [{result.duration:.1f}s]")
        else:
            logging.error(
                f"Result: FAILED ({result.fail_kind}) [{result.duration:.1f}s] "
                f"— {result.message}"
            )
        return 0 if result.passed else 1
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
