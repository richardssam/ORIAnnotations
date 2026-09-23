"""Tests for the ``http_fallback`` OTIO media_linker plugin.

Covers the scenarios in
``openspec/changes/http-media-fallback/specs/otio-media-http-fallback/spec.md``.
"""

import os
import sys

import pytest
import opentimelineio as otio

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(project_root, "otio_event_plugin", "media_linkers"))

import http_fallback  # noqa: E402


def _clip(target_url, duration=10):
    return otio.schema.Clip(
        name=os.path.basename(target_url),
        media_reference=otio.schema.ExternalReference(
            target_url=target_url,
            available_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(duration, 24),
            ),
        ),
        metadata={"sync": {"guid": "test-guid-1234"}},
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, raising=False)
    monkeypatch.delenv(http_fallback.ORI_MEDIA_HTTP_BASE_URL, raising=False)


def test_local_media_present_leaves_reference_unchanged(tmp_path, monkeypatch):
    media_file = tmp_path / "car.mov"
    media_file.write_bytes(b"fake movie data")

    monkeypatch.setenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, str(tmp_path))
    monkeypatch.setenv(http_fallback.ORI_MEDIA_HTTP_BASE_URL, "http://127.0.0.1:8899")

    clip = _clip(str(media_file))
    result = http_fallback.link_media_reference(clip, {})

    assert result.target_url == str(media_file)


def test_local_media_missing_falls_back_to_http(tmp_path, monkeypatch):
    monkeypatch.setenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, str(tmp_path))
    monkeypatch.setenv(http_fallback.ORI_MEDIA_HTTP_BASE_URL, "http://127.0.0.1:8899")

    clip = _clip(str(tmp_path / "missing.mov"))
    result = http_fallback.link_media_reference(clip, {})

    assert result.target_url == "http://127.0.0.1:8899/missing.mov"
    assert result.available_range == clip.media_reference.available_range


def test_unconfigured_env_is_inert(tmp_path):
    clip = _clip(str(tmp_path / "missing.mov"))
    result = http_fallback.link_media_reference(clip, {})

    assert result is clip.media_reference


def test_partially_configured_env_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, str(tmp_path))
    # ORI_MEDIA_HTTP_BASE_URL intentionally left unset.

    clip = _clip(str(tmp_path / "missing.mov"))
    result = http_fallback.link_media_reference(clip, {})

    assert result is clip.media_reference


def test_sync_guid_unchanged_by_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, str(tmp_path))
    monkeypatch.setenv(http_fallback.ORI_MEDIA_HTTP_BASE_URL, "http://127.0.0.1:8899")

    clip = _clip(str(tmp_path / "missing.mov"))
    http_fallback.link_media_reference(clip, {})

    assert clip.metadata["sync"]["guid"] == "test-guid-1234"


def test_reevaluated_on_every_invocation(tmp_path, monkeypatch):
    monkeypatch.setenv(http_fallback.ORI_MEDIA_LOCAL_ROOT, str(tmp_path))
    monkeypatch.setenv(http_fallback.ORI_MEDIA_HTTP_BASE_URL, "http://127.0.0.1:8899")

    clip = _clip(str(tmp_path / "car.mov"))

    first = http_fallback.link_media_reference(clip, {})
    assert first.target_url == "http://127.0.0.1:8899/car.mov"

    # Media appears locally between the two calls; simulate the adapter
    # having already swapped the clip's media_reference to the HTTP result.
    clip.media_reference = first
    (tmp_path / "car.mov").write_bytes(b"fake movie data")

    second = http_fallback.link_media_reference(clip, {})
    assert second.target_url == str(tmp_path / "car.mov")
