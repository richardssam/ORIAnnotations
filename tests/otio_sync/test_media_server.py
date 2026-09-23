"""Tests for ``sync_test.media_server.MediaServer``, the Range-request-capable
HTTP server used by the ``http_media_fallback`` sync_test scenario.

Covers the scenarios in
``openspec/changes/http-media-fallback/specs/http-media-test-server/spec.md``.
"""

import os
import socket
import sys

import pytest
import urllib.request
import urllib.error

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(project_root, "sync_test", "python"))

from sync_test.media_server import MediaServer  # noqa: E402


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def media_dir(tmp_path):
    (tmp_path / "car.mov").write_bytes(b"0123456789" * 20)
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("should never be served")
    yield tmp_path


@pytest.fixture
def running_server(media_dir):
    server = MediaServer(str(media_dir), _free_port())
    server.start()
    yield server
    server.stop()


def test_plain_get_returns_full_content(running_server):
    url = f"http://127.0.0.1:{running_server.port}/car.mov"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        assert resp.headers.get("Accept-Ranges") == "bytes"
        assert resp.read() == b"0123456789" * 20


def test_ranged_get_returns_partial_content(running_server):
    url = f"http://127.0.0.1:{running_server.port}/car.mov"
    req = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers.get("Content-Range") == "bytes 10-19/200"
        assert resp.read() == b"0123456789"


def test_path_traversal_is_rejected(running_server):
    url = f"http://127.0.0.1:{running_server.port}/../outside_secret.txt"
    req = urllib.request.Request(url)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 404
