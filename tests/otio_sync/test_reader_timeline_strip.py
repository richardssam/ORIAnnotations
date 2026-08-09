"""Which tracks reach RV's OTIO reader, and why the rest must not.

``create_rv_node_from_otio`` builds one ``RVSourceGroup`` per clip per track,
with no notion that two tracks can name the same *source*.  Two kinds of track
therefore produce nodes RV does not want:

* the logical **Annotations** track, whose clips have no media at all; and
* an **Audio** track that mirrors a video track, which xStudio exports as a
  matter of course.  RV's reader appends a blank movieproc to each audio clip's
  media and wires a whole second ``RVSequenceGroup`` of those blank-video
  sources into the stack — while an RV source already plays its own movie's
  audio.  Observed 2026-08-09 11:35: a 3-clip Video Track plus a 2-clip Audio
  Track over the same movies produced 5 source groups where 3 were wanted.

The removal is *conditional*, and that is the part worth defending: an audio
track over media no video track references is a real stem, and dropping it
would silently lose audio.  Every test here pins one side of that line.
"""
import os
import sys
import types
import unittest

import opentimelineio as otio

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))
sys.path.insert(0, os.path.join(repo_root, "rvplugin", "ori_sync"))

# ``sequence_sync`` is host-coupled; nothing under test touches RV, but the
# import does.
_fake_rv = types.ModuleType("rv")
_fake_cmds = types.ModuleType("rv.commands")
_fake_cmds.nodeType = lambda n: "RVSourceGroup"
_fake_rv.commands = _fake_cmds
sys.modules.setdefault("rv", _fake_rv)
sys.modules.setdefault("rv.commands", _fake_cmds)

import sequence_sync  # noqa: E402


CAR = "file:///media/car.mov"
GRAPHIC = "file:///media/graphic.mov"
LASER = "file:///media/laser.mov"
STEM = "file:///media/dialogue_stem.wav"


def _clip(name, url):
    return otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(target_url=url),
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(24, 24)
        ),
    )


def _track(name, kind, urls):
    track = otio.schema.Track(name=name, kind=kind)
    for i, url in enumerate(urls):
        track.append(_clip(f"{name}_clip{i}", url))
    return track


def _timeline(*tracks):
    tl = otio.schema.Timeline(name="Sequence 1")
    for track in tracks:
        tl.tracks.append(track)
    return tl


def _names(tl):
    return [t.name for t in tl.tracks]


class ReaderTimelineStripTest(unittest.TestCase):
    """``_reader_timeline`` decides what the reader is allowed to build."""

    def setUp(self):
        # The method reaches only for _clip_media_urls, so the controller needs
        # no session — __init__ would demand a live plugin.
        self.ctl = sequence_sync.SequenceSyncController.__new__(
            sequence_sync.SequenceSyncController
        )

    def test_mirroring_audio_track_is_dropped(self):
        """The observed shape: audio over movies the video track already names."""
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR, GRAPHIC, LASER]),
            _track("Audio Track", otio.schema.TrackKind.Audio, [CAR, GRAPHIC]),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track"])

    def test_audio_track_on_its_own_media_survives(self):
        """A separate stem is real audio — dropping it would lose it silently."""
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR, GRAPHIC]),
            _track("Dialogue", otio.schema.TrackKind.Audio, [STEM]),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track", "Dialogue"])

    def test_partly_covered_audio_track_survives(self):
        """One uncovered clip is enough to make the whole track real audio.

        Subset, not intersection: the track goes only when every movie on it is
        already reachable through a video track.
        """
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR]),
            _track("Audio Track", otio.schema.TrackKind.Audio, [CAR, STEM]),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track", "Audio Track"])

    def test_annotations_track_is_still_dropped(self):
        """The pre-existing strip this refactor absorbed."""
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR]),
            otio.schema.Track(name="Annotations", kind=otio.schema.TrackKind.Video),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track"])

    def test_annotations_media_does_not_cover_an_audio_track(self):
        """The Annotations track is excluded from the covering set.

        It is dropped itself, so counting its clips as "already on a video
        track" would let it authorise dropping real audio that nothing else
        carries.
        """
        annotations = _track("Annotations", otio.schema.TrackKind.Video, [STEM])
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR]),
            annotations,
            _track("Audio Track", otio.schema.TrackKind.Audio, [STEM]),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track", "Audio Track"])

    def test_empty_audio_track_is_dropped(self):
        """It references nothing, so it can lose nothing — and still costs a
        sequence group in the stack."""
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR]),
            otio.schema.Track(name="Audio Track", kind=otio.schema.TrackKind.Audio),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track"])

    def test_gaps_do_not_count_as_covering_media(self):
        """A Gap has no media_reference; reading one must not raise or match."""
        video = _track("Video Track", otio.schema.TrackKind.Video, [CAR])
        video.append(otio.schema.Gap(
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(12, 24)
            )
        ))
        tl = _timeline(
            video, _track("Audio Track", otio.schema.TrackKind.Audio, [CAR])
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(out), ["Video Track"])

    def test_timeline_needing_nothing_removed_is_passed_through_unchanged(self):
        """Not merely equal — the same object, so the common path copies nothing."""
        tl = _timeline(_track("Video Track", otio.schema.TrackKind.Video, [CAR]))
        self.assertIs(self.ctl._reader_timeline(tl), tl)

    def test_the_received_timeline_is_never_mutated(self):
        """The manager keeps the full timeline for annotation lookup; only the
        reader gets the reduced one."""
        tl = _timeline(
            _track("Video Track", otio.schema.TrackKind.Video, [CAR]),
            _track("Audio Track", otio.schema.TrackKind.Audio, [CAR]),
        )
        out = self.ctl._reader_timeline(tl)
        self.assertEqual(_names(tl), ["Video Track", "Audio Track"])
        self.assertIsNot(out, tl)

    def test_a_broken_track_leaves_the_timeline_untouched(self):
        """A rebuild is worth more than a strip: on error the reader gets
        everything rather than nothing."""
        class Exploding(list):
            @property
            def tracks(self):
                raise RuntimeError("no tracks for you")

        boom = Exploding()
        self.assertIs(self.ctl._reader_timeline(boom), boom)


class ClipMediaUrlsTest(unittest.TestCase):
    """The covering set is only as good as what it reads off a track."""

    def setUp(self):
        self.urls = sequence_sync.SequenceSyncController._clip_media_urls

    def test_collects_external_reference_urls(self):
        track = _track("Video Track", otio.schema.TrackKind.Video, [CAR, GRAPHIC])
        self.assertEqual(self.urls(track), {CAR, GRAPHIC})

    def test_ignores_items_without_media(self):
        track = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
        track.append(otio.schema.Gap(
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(12, 24)
            )
        ))
        track.append(otio.schema.Clip(
            name="generated",
            media_reference=otio.schema.MissingReference(),
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(12, 24)
            ),
        ))
        self.assertEqual(self.urls(track), set())


if __name__ == "__main__":
    unittest.main()
