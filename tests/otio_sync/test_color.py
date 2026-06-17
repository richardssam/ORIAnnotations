"""Tests for the color pipeline metadata schema, parsing, and resolution."""

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import color


class FakeObj:
    """Minimal stand-in for an OTIO object exposing a ``.metadata`` mapping."""

    def __init__(self, metadata=None):
        self.metadata = metadata if metadata is not None else {}


# ---------------------------------------------------------------------------
# parse_colorspace
# ---------------------------------------------------------------------------


def test_parse_bare_name_uses_default_vocabulary():
    assert color.parse_colorspace("ACEScg") == (color.DEFAULT_VOCABULARY, "ACEScg")


def test_parse_known_vocabulary():
    assert color.parse_colorspace("ocio:ACEScg") == ("ocio", "ACEScg")
    assert color.parse_colorspace("interop:ACEScg") == ("interop", "ACEScg")


def test_parse_unknown_vocabulary_preserved():
    assert color.parse_colorspace("resolve:DaVinci Wide Gamut Intermediate") == (
        "resolve",
        "DaVinci Wide Gamut Intermediate",
    )


def test_parse_name_with_colon_after_valid_prefix():
    # Only the text before the first colon is the tag.
    assert color.parse_colorspace("ocio:Utility - Curve - sRGB") == (
        "ocio",
        "Utility - Curve - sRGB",
    )
    assert color.parse_colorspace("foo:bar:baz") == ("foo", "bar:baz")


def test_parse_invalid_prefix_treated_as_bare_name():
    # Leading segment is not a valid tag (spaces/case), so the whole string is
    # a bare name in the default vocabulary.
    value = "Rec.709 - Display"
    assert color.parse_colorspace(value) == (color.DEFAULT_VOCABULARY, value)
    value2 = "ACES 2.0:not a tag"  # leading segment has spaces/uppercase
    assert color.parse_colorspace(value2) == (color.DEFAULT_VOCABULARY, value2)


def test_is_resolved_vocabulary():
    assert color.is_resolved_vocabulary("ocio:ACEScg")
    assert color.is_resolved_vocabulary("interop:ACEScg")
    assert color.is_resolved_vocabulary("ACEScg")  # bare -> ocio
    assert not color.is_resolved_vocabulary("resolve:ARRI LogC3")
    assert not color.is_resolved_vocabulary("cicp:9-16-9-full")


# ---------------------------------------------------------------------------
# Defensive reads
# ---------------------------------------------------------------------------


def test_read_timeline_color_empty_when_unmanaged():
    assert color.read_timeline_color(FakeObj()) == {}


def test_read_timeline_color_returns_set_fields_only():
    tl = FakeObj({"color": {"working_space": "ACEScg", "config": "", "output_space": None}})
    assert color.read_timeline_color(tl) == {"working_space": "ACEScg"}


def test_read_timeline_color_survives_bad_metadata():
    class Broken:
        @property
        def metadata(self):
            raise RuntimeError("bad any cast")

    assert color.read_timeline_color(Broken()) == {}


def test_read_color_space():
    assert color.read_color_space(FakeObj({"color_space": "ocio:ACEScg"})) == "ocio:ACEScg"
    assert color.read_color_space(FakeObj({"color_space": ""})) is None
    assert color.read_color_space(FakeObj()) is None


# ---------------------------------------------------------------------------
# Hierarchical resolution
# ---------------------------------------------------------------------------


def test_resolve_clip_override_wins():
    clip = FakeObj({"color_space": "ocio:ARRI LogC3"})
    tl = FakeObj({"color": {"working_space": "ACEScg"}})
    assert color.resolve_input_colorspace(clip, tl, "host") == "ocio:ARRI LogC3"


def test_resolve_falls_back_to_timeline_working_space():
    clip = FakeObj()
    tl = FakeObj({"color": {"working_space": "ACEScg"}})
    assert color.resolve_input_colorspace(clip, tl, "host") == "ACEScg"


def test_resolve_falls_back_to_host_default():
    clip = FakeObj()
    tl = FakeObj()
    assert color.resolve_input_colorspace(clip, tl, "interop:lin_rec709_scene") == (
        "interop:lin_rec709_scene"
    )


def test_resolve_none_when_nothing_set():
    assert color.resolve_input_colorspace(FakeObj(), FakeObj()) is None


def test_resolve_without_timeline():
    clip = FakeObj()
    assert color.resolve_input_colorspace(clip, None, "host") == "host"
