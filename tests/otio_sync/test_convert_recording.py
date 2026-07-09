import json
import os
import sys
import tempfile
import unittest

import opentimelineio as otio

# Ensure we can import sync_recorder
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.append(project_root)

from sync_recorder.convert_recording_to_timeline import convert_recording


class TestConvertRecording(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_path = os.path.join(self.temp_dir.name, "session_record.jsonl")
        self.output_path = os.path.join(self.temp_dir.name, "output_timeline.otio")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_convert_recording(self):
        # Create a mock timeline with a clip
        # Media carries embedded timecode: it really starts at frame 1000 and
        # exposes no explicit source_range (source_range is None -> use the
        # whole available_range). The protocol view frames (0-based) must be
        # resolved into this 1000-based media space, not copied verbatim.
        clip_guid = "test_clip_123"
        template_clip = otio.schema.Clip(
            name="test_media.mov",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///path/to/test_media.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(1000, 24.0),
                    duration=otio.opentime.RationalTime(300, 24.0)
                )
            ),
        )
        template_clip.metadata["sync"] = {"guid": clip_guid}

        timeline = otio.schema.Timeline("Default Timeline")
        track = otio.schema.Track("Media")
        track.append(template_clip)
        timeline.tracks.append(track)

        # Build mock events
        snapshot_timeline_dict = json.loads(
            otio.adapters.write_to_string(timeline, "otio_json")
        )

        events = [
            # 1. State Snapshot (start at t=0)
            {
                "time_offset": 0.0,
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_SNAPSHOT",
                        "payload": {
                            "active_timeline_guid": "timeline_abc",
                            "timelines": {"timeline_abc": snapshot_timeline_dict},
                            "playback_state": {
                                "playing": False,
                                "current_time": {"value": 0.0, "rate": 24.0}
                            }
                        }
                    }
                }
            },
            # 2. Start playing at t=2.0 (duration 2.0s paused at frame 0)
            {
                "time_offset": 2.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": True,
                            "current_time": {"value": 0.0, "rate": 24.0}
                        }
                    }
                }
            },
            # 3. Pause at t=12.0 (duration 10.0s playing -> 240 frames)
            {
                "time_offset": 12.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": False,
                            "current_time": {"value": 240.0, "rate": 24.0}
                        }
                    }
                }
            },
            # 4. End recording at t=15.0 (duration 3.0s paused at frame 240)
            {
                "time_offset": 15.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": False,
                            "current_time": {"value": 240.0, "rate": 24.0}
                        }
                    }
                }
            }
        ]

        # Write to JSONL
        with open(self.recording_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        # Convert
        convert_recording(self.recording_path, self.output_path, target_fps=24.0)

        # Read back and assert
        self.assertTrue(os.path.exists(self.output_path))
        converted_timeline = otio.adapters.read_from_file(self.output_path)

        self.assertEqual(len(converted_timeline.tracks), 1)
        bg_track = converted_timeline.tracks[0]
        self.assertEqual(bg_track.name, "Background Media")

        # Visual segments should be (all in MEDIA frame space, base 1000):
        # Segment 1 (t=0.0->2.0, 2.0s): paused at view frame 0 -> media 1000,
        #   48 output frames, LinearTimeWarp freeze.
        # Segment 2 (t=2.0->12.0, 10.0s): playing from view 0 -> media 1000,
        #   240 frames, no time warp (media advances 1000..1240).
        # Segment 3 (t=12.0->15.0, 3.0s): paused at view frame 240 -> media
        #   1240, 72 output frames, LinearTimeWarp freeze.
        self.assertEqual(len(bg_track), 3)

        clip1 = bg_track[0]
        self.assertEqual(clip1.name, "test_media.mov")
        self.assertEqual(clip1.source_range.start_time.value, 1000.0)
        self.assertEqual(clip1.source_range.duration.value, 48.0)
        self.assertEqual(len(clip1.effects), 1)
        self.assertTrue(isinstance(clip1.effects[0], otio.schema.LinearTimeWarp))
        self.assertEqual(clip1.effects[0].time_scalar, 0.0)

        clip2 = bg_track[1]
        self.assertEqual(clip2.name, "test_media.mov")
        self.assertEqual(clip2.source_range.start_time.value, 1000.0)
        self.assertEqual(clip2.source_range.duration.value, 240.0)
        self.assertEqual(len(clip2.effects), 0)

        clip3 = bg_track[2]
        self.assertEqual(clip3.name, "test_media.mov")
        self.assertEqual(clip3.source_range.start_time.value, 1240.0)
        self.assertEqual(clip3.source_range.duration.value, 72.0)
        self.assertEqual(len(clip3.effects), 1)
        self.assertTrue(isinstance(clip3.effects[0], otio.schema.LinearTimeWarp))
        self.assertEqual(clip3.effects[0].time_scalar, 0.0)


def _rt(v, rate=24.0):
    return otio.opentime.RationalTime(v, rate)


def _clip(name, guid, avail_start, avail_dur):
    ref = otio.schema.ExternalReference(
        target_url=f"/{name}.mov",
        available_range=otio.opentime.TimeRange(_rt(avail_start), _rt(avail_dur)),
    )
    c = otio.schema.Clip(name=name, media_reference=ref)
    c.metadata["sync"] = {"guid": guid}
    return c


def _event(t, cmd, evt, payload):
    return {
        "time_offset": t,
        "payload": {"command_schema": cmd, "command": {"event": evt, "payload": payload}},
    }


def _snapshot(t, tl_guid, tl_dict, playing=False, frame=0.0, mode="loop"):
    return _event(t, "LiveSession.1", "STATE_SNAPSHOT", {
        "active_timeline_guid": tl_guid,
        "timelines": {tl_guid: tl_dict},
        "playback_state": {
            "playing": playing,
            "current_time": {"value": frame, "rate": 24.0},
            "playback_mode": mode,
        },
    })


def _playback(t, playing, frame, mode="loop", tl_guid=None, view_mode="sequence"):
    payload = {
        "playing": playing,
        "current_time": {"value": frame, "rate": 24.0},
        "playback_mode": mode,
        "view_mode": view_mode,
    }
    if tl_guid:
        payload["timeline_guid"] = tl_guid
    return _event(t, "PLAYBACK_SETTINGS_1.0", "SET", payload)


def _write_and_convert(events, tmpdir, fps=24.0):
    rec = os.path.join(tmpdir, "rec.jsonl")
    out = os.path.join(tmpdir, "out.otio")
    with open(rec, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    convert_recording(rec, out, target_fps=fps)
    return otio.adapters.read_from_file(out)


def _bg_clips(timeline):
    return [c for c in timeline.tracks[0] if isinstance(c, otio.schema.Clip)]


class TestSequenceTraversalAndLoop(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _two_clip_timeline(self):
        tl = otio.schema.Timeline("Seq")
        tl.metadata["sync"] = {"guid": "TL"}
        track = otio.schema.Track("Media")
        track.append(_clip("A", "gA", 1000, 50))   # view 0..49  -> media 1000..1049
        track.append(_clip("B", "gB", 2000, 30))   # view 50..79 -> media 2000..2029
        tl.tracks.append(track)
        return tl, json.loads(otio.adapters.write_to_string(tl, "otio_json"))

    def test_traversal_and_loop_wrap(self):
        _, tl_dict = self._two_clip_timeline()
        # play 100 frames from view 0 in loop mode: A(50) -> B(30) -> wrap A(20)
        events = [
            _snapshot(0.0, "TL", tl_dict),
            _playback(1.0, True, 0.0, mode="loop", tl_guid="TL"),
            _playback(1.0 + 100 / 24.0, False, 20.0, mode="loop", tl_guid="TL"),
        ]
        out = _write_and_convert(events, self.temp_dir.name)
        playing = [c for c in _bg_clips(out)
                   if not any(getattr(e, "time_scalar", None) == 0.0 for e in c.effects)]
        got = [(c.name, int(c.source_range.start_time.value), int(c.source_range.duration.value))
               for c in playing]
        self.assertEqual(got, [
            ("A", 1000, 50),   # clip A in its own media space
            ("B", 2000, 30),   # crossed the cut into clip B's media space
            ("A", 1000, 20),   # loop wrapped back to sequence start
        ])

    def test_non_loop_holds_last_frame(self):
        _, tl_dict = self._two_clip_timeline()
        # play 100 frames from view 0 with mode=play-once: A(50)->B(30) then HOLD
        events = [
            _snapshot(0.0, "TL", tl_dict),
            _playback(1.0, True, 0.0, mode="play_once", tl_guid="TL"),
            _playback(1.0 + 100 / 24.0, False, 79.0, mode="play_once", tl_guid="TL"),
        ]
        out = _write_and_convert(events, self.temp_dir.name)
        clips = _bg_clips(out)
        frozen = [c for c in clips
                  if any(getattr(e, "time_scalar", None) == 0.0 for e in c.effects)]
        # the trailing hold freezes clip B's last media frame (2029)
        self.assertTrue(frozen, "expected a freeze/hold segment at sequence end")
        last = clips[-1]
        self.assertTrue(any(getattr(e, "time_scalar", None) == 0.0 for e in last.effects))
        self.assertEqual(int(last.source_range.start_time.value), 2029)
        # and it never wrapped back to clip A
        self.assertNotIn(("A", 1000), [
            (c.name, int(c.source_range.start_time.value)) for c in clips[2:]
        ])

    def test_all_source_ranges_within_available_range(self):
        _, tl_dict = self._two_clip_timeline()
        events = [
            _snapshot(0.0, "TL", tl_dict),
            _playback(1.0, True, 0.0, mode="loop", tl_guid="TL"),
            _playback(1.0 + 200 / 24.0, False, 40.0, mode="loop", tl_guid="TL"),
        ]
        out = _write_and_convert(events, self.temp_dir.name)
        ranges = {"A": (1000, 1050), "B": (2000, 2030)}
        for c in _bg_clips(out):
            lo, hi = ranges[c.name]
            s = int(c.source_range.start_time.value)
            frozen = any(getattr(e, "time_scalar", None) == 0.0 for e in c.effects)
            end = s if frozen else s + int(c.source_range.duration.value) - 1
            self.assertGreaterEqual(s, lo, f"{c.name} start {s} < {lo}")
            self.assertLess(end, hi, f"{c.name} end {end} >= {hi}")

    def test_missing_range_fails_loudly(self):
        # A clip with neither source_range nor available_range is unresolvable.
        tl = otio.schema.Timeline("Seq")
        tl.metadata["sync"] = {"guid": "TL"}
        track = otio.schema.Track("Media")
        c = otio.schema.Clip(name="X", media_reference=otio.schema.ExternalReference(target_url="/x.mov"))
        c.metadata["sync"] = {"guid": "gX"}
        track.append(c)
        tl.tracks.append(track)
        tl_dict = json.loads(otio.adapters.write_to_string(tl, "otio_json"))
        events = [
            _snapshot(0.0, "TL", tl_dict),
            _playback(1.0, False, 0.0, tl_guid="TL"),
            _playback(3.0, False, 0.0, tl_guid="TL"),
        ]
        with self.assertRaises(Exception):
            _write_and_convert(events, self.temp_dir.name)

    def test_playback_before_snapshot_is_skipped_not_fatal(self):
        # A playback event can name a timeline before the snapshot defining it
        # arrives (early master-state noise). That interval has no structure to
        # resolve against and must be skipped, not crash the conversion.
        _, tl_dict = self._two_clip_timeline()
        events = [
            # play references TL before it is known
            _playback(0.0, True, 0.0, mode="loop", tl_guid="TL"),
            # snapshot arrives later
            _snapshot(2.0, "TL", tl_dict, playing=True, frame=0.0),
            _playback(2.0 + 30 / 24.0, False, 30.0, mode="loop", tl_guid="TL"),
        ]
        out = _write_and_convert(events, self.temp_dir.name)
        bg = _bg_clips(out)
        self.assertTrue(bg, "expected segments once the snapshot is known")
        # every emitted clip resolves into real media space (>= 1000)
        for c in bg:
            self.assertGreaterEqual(int(c.source_range.start_time.value), 1000)

    def test_source_mode_clip_guid_from_playback_event(self):
        # In source view mode the selected clip rides on the playback event
        # itself; there may be no separate SELECTION event.
        _, tl_dict = self._two_clip_timeline()
        # Collapse the pre-source interval to zero (snapshot + source-select +
        # play all at t=0) so every emitted segment is in source mode.
        events = [
            _snapshot(0.0, "TL", tl_dict),
            _playback(0.0, False, 0.0, tl_guid="TL", view_mode="source"),
            _playback(0.0, True, 0.0, tl_guid="TL", view_mode="source"),
            _playback(20 / 24.0, False, 20.0, tl_guid="TL", view_mode="source"),
        ]
        # source-view clip B rides on each playback payload
        for ev in events[1:]:
            ev["payload"]["command"]["payload"]["clip_guid"] = "gB"

        out = _write_and_convert(events, self.temp_dir.name)
        bg = _bg_clips(out)
        self.assertTrue(bg)
        # clip B media base is 2000; source-view frames must resolve there
        for c in bg:
            self.assertEqual(c.name, "B")
            self.assertGreaterEqual(int(c.source_range.start_time.value), 2000)


class TestRealDemoRecordingInRange(unittest.TestCase):
    def test_demo_recording_source_ranges_in_media_range(self):
        demo = os.path.join(project_root, "sync_test", "recordings", "demo-otioconvert-1.jsonl")
        if not os.path.exists(demo):
            self.skipTest("demo recording fixture not present")
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "out.otio")
            convert_recording(demo, out_path, target_fps=24.0)
            out = otio.adapters.read_from_file(out_path)
        bg = _bg_clips(out)
        self.assertTrue(bg)
        # media available_range from the snapshot is [98499, 98600)
        for c in bg:
            s = int(c.source_range.start_time.value)
            frozen = any(getattr(e, "time_scalar", None) == 0.0 for e in c.effects)
            end = s if frozen else s + int(c.source_range.duration.value) - 1
            self.assertGreaterEqual(s, 98499, f"start {s} below media base")
            self.assertLess(end, 98600, f"end {end} past media range")
        # the annotation was drawn at view frame 31 -> media frame 98530
        if len(out.tracks) > 1:
            ov = [c for c in out.tracks[1] if isinstance(c, otio.schema.Clip)]
            frames = {c.media_reference.target_url.split("/")[-1].rsplit("_", 2)[1] for c in ov}
            self.assertEqual(frames, {"98530"})

    def test_multiclip_demo_recording_each_clip_in_its_own_range(self):
        # demo-2 is a multi-clip sequence with a pre-snapshot playback event and
        # a source-view section whose clip rides on the playback payload.
        demo = os.path.join(project_root, "sync_test", "recordings", "demo-otioconvert-2.jsonl")
        if not os.path.exists(demo):
            self.skipTest("multi-clip demo recording fixture not present")
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "out.otio")
            convert_recording(demo, out_path, target_fps=24.0)
            out = otio.adapters.read_from_file(out_path)
        bg = _bg_clips(out)
        self.assertTrue(bg)
        # every clip must land within ITS OWN media's available_range
        for c in bg:
            ar = c.media_reference.available_range
            if ar is None:
                continue
            lo = int(ar.start_time.value)
            hi = lo + int(ar.duration.value) - 1
            s = int(c.source_range.start_time.value)
            frozen = any(getattr(e, "time_scalar", None) == 0.0 for e in c.effects)
            end = s if frozen else s + int(c.source_range.duration.value) - 1
            self.assertGreaterEqual(s, lo, f"{c.name} start {s} < {lo}")
            self.assertLessEqual(end, hi, f"{c.name} end {end} > {hi}")


if __name__ == "__main__":
    unittest.main()
