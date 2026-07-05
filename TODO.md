
Launching rabbitmq on macOS:

```bash
CONF_ENV_FILE="/opt/homebrew/etc/rabbitmq/rabbitmq-env.conf" /opt/homebrew/opt/rabbitmq/sbin/rabbitmq-server
```

Launching rabbitmq on macOS:

```bash
CONF_ENV_FILE="/opt/homebrew/etc/rabbitmq/rabbitmq-env.conf" /opt/homebrew/opt/rabbitmq/sbin/rabbitmq-server
```

# Todo Critical

- [ ] Annotations are still different sizes between RV and xSTUDIO need to investigate.
- [ ] Need to figure out how to handle regular playlists reordering for xstudio playlists in rv.
- [x] XStudio plugin for annotations inport/export - currently imports are failing.
- [ ] Test a media handler, so missing media can be substituted.
- [x] Test Squares, arrows, and circles from new OpenRV.
- [ ] Handle x, y flip
- [ ] Handle play backwards. (xstudio doesnt have a button for this)
- [ ] Need loop controls, do we loop, or rock-and-roll? - started, but not complete.
- [ ] Changes to frame rate.

* Creating prototype C++ changes to OTIO, currently its failing running the tests.


# TODO Cleanup
- [ ] Move loader and saver for RV to export/import menu.
- [ ] Figure out if we can move the pika install into the plugin for xstudio
- [ ] README says it needs a OTIO_PLUGIN_MANIFEST_PATH for xstudio. Can we make it part of the plugin.

# Sync Security and Session Users
- [ ] Sync manager should keep track of who is connected.
- [ ] Need security controls for who can drive, or modify, or annotate.
- [ ] Laser pointer.
- [ ] We should be able to request the state of a particular host, and be able to compare it to what it thinks is there.
- [x] Update protocol to new format (only slightly different to before).
- [x] Add in menu options for registering with environment variable for stream-name.
- [ ] Handle encryption and possibly compression.

- [x] Fix text size issues.
- [x] Figure out issue with xstudio and timecode in quicktime files.



# openrv_sync_plugin

- [x] importing full OTIO sessions (Assuming single track).
- [x] Add colorspace OTIO/OCIO attributes and link it to OCIO.
- [ ] Figure out redraw issue where text gets visually duplicated for a moment. - created test for this - text_annotations_xstudio_to_rv duplicate text not in rv file.
- [ ] Handle clear annotations.

# XStudio Plugin

- [ ] Add tests for full OTIO sessions - need to understand what options there are (e.g. edits, different frame rates, etc).
- [x] Add colorspace OTIO/OCIO attributes and link it to OCIO.
- [ ] Handle clear annotations, not clear if we need to handle the undo/redo delete.
- [x] Handle partial annotations.
- [ ] Handle Shapes as OTIO objects: draw a Square/Circle/Arrow/Line; confirm **no** mid-drag partial is broadcast and the shape appears only on pen-up.

## Known Limitations

### Client→Host sequence sync is unreliable

Changes made in RV (as a sync client) to a sequence timeline — trims, clip
repositioning, reorders — do not reliably propagate back to the xStudio host.
Root cause: `to_otio_string()` on a client-loaded sequence loses both media
references (becomes `MissingReference`) and clip metadata (sync GUIDs stripped),
leaving nothing to match on.  See `docs/plugin_tasks.md` for the two fix paths.

## API Improvement Tasks (from xstudio_questions.md replies)

### [2C] Event-driven annotation detection

Replace the hot-scan (33 ms poll) and 1 s fallback scan with a subscription to
`ANNOTATIONS_CORE_PLUGIN`'s `live_edit_event_group_`.  Subscribe via
`join_broadcast_atom() + annotation_atom()` and handle
`(event_atom, annotation_data_atom, AnnotationBasePtr, user_id, stroke_completed)`.
The `stroke_completed=True` flag on `PaintEnd` is the pen-up signal.

**Testing:**

* Draw a stroke on a new frame in xStudio → RV peer should receive the annotation immediately after pen-up (not after 250 ms debounce)
* Draw a second stroke on an existing bookmark (same frame) → peer should receive it promptly (was: up to 1 s latency)
* While drawing, partial strokes should still stream live to peers (confirm the `stroke_completed=False` events deliver mid-stroke points)
* Undo a stroke → peer's annotation should update
* Erase all annotations on a frame → peer should see the frame annotation cleared
* Test that remote-sourced bookmarks (`_our_bookmark_uuids`) are still correctly filtered and not echoed back

I

**Testing:**

* Zoom in/out in xStudio → RV peer should reflect the zoom change (already worked via serialise_atom; confirm still works)
* Zoom in/out in RV → xStudio should now follow (previously blocked — confirm it works for the first time)
* Pan in xStudio → RV peer should pan to match (previously disabled)
* Pan in RV → xStudio should follow (previously disabled)
* Reconnect after zooming → baseline calibration via `fit_mode_atom` should be consistent (previous first-seen was session-dependent)
* Verify no pan jump (~50% offset) when xStudio joins an existing session

### [2B] Playhead subscription management fix

Switch from `auto_cancel=True` (which cancels all subscriptions) to
`auto_cancel=False` with a manual `{playhead_actor_id: subscription_id}` dict.
Cancel only the entry matching the target playhead before re-subscribing.
This also sets up a clean path to test `position_atom` event delivery.

**Testing:**

* Load two sequence timelines; scrub on the first → confirm frame broadcasts arrive
* Switch to the second timeline and scrub → confirm frame broadcasts still arrive (was: broken after switch)
* Switch back to the first → confirm it still works
* Check logs for `[TEST position_atom] FIRED` to see if `position_atom` is now reliable — if so, note it for potential future event-driven frame sync

### [2F] Event-driven clip insertion detection

Subscribe to Timeline `item_atom` events via `join_event_group` on each tracked
xStudio Timeline actor.  Handle `(event_atom, item_atom, JsonStore changes, bool hidden)`
to detect clip insertions.  Remove `_poll_sequence_new_media` once confirmed working.
Requires `item_atom` to be in `py_atoms.cpp` (confirm this is the case).

**Testing:**

* On xStudio master, add a clip to a sequence timeline → RV/client should receive the clip within ~100 ms (the `item_atom` event fires immediately; `change_atom` has a 50 ms debounce — note which one we use)
* Add multiple clips in quick succession → confirm all arrive in order
* Confirm `_poll_sequence_new_media` is no longer running (check logs — the 500 ms poll log lines should disappear)
* Flat playlists are not affected (they use a different poll path); confirm flat playlist new-media still works

### [2G] Source-switch debounce for navigation

Replace the 1 s `_playhead_lock_until` hard block with a 50–100 ms seek
debounce: cancel any pending seek when a new `viewport_playhead_atom` Form-2
event arrives, and schedule the seek 75 ms after the most recent one.  Remove
the `ThreadPoolExecutor` playhead-refresh path if the Form-2 event reliably
delivers the new playhead actor.

**Testing:**

* Receive a clip selection from RV → xStudio should switch to the correct clip and land on the right frame
* Receive two rapid clip selections in quick succession → only the last one should apply cleanly (no seek to a stale frame)
* After a selection switch, scrub in xStudio → confirm `active_playhead` is correct (frame broadcasts go to the right timeline, not the previous one)
* Sequence timeline seek (multi-clip): confirm the playhead actually reaches the clip start frame without the retry loop failing

# LiveReviewOpenRV

* Its failing to handle erase brushes.

# Test Charts

* Brush widths are possibly wrong on xstudio version.
* Missing brush width for test sizes.
* Need to add more opacity brushes
* Need to add some test annotations.
* See if we can get an SVG version too of the annotations for verification purposes.
* Need a test harness that can load the OTIO Annotation file for openRV, and then render it out.
* Need a test harness that can load the OTIO Annotation file for xSTUDIO, and then render it out.

# Questions for xStudio developers meeting

## Priority questions (blockers)

### 1. Clip selection event — what is the right API?

`viewport_active_media_container_atom()` always returns the Timeline regardless of what
the user clicks.  `Timeline.selection` is always empty.  `show_atom` fires during
auto-play too, so we can't distinguish deliberate user selection from playback advance.

**Ask**: Is there a canonical event for "user deliberately selected this clip to view in the
viewport"?  Something like a `media_selection_atom` or `viewed_media_atom` that fires only on
explicit user action, not on every clip change during playback?  Also — why does
`Timeline.selection` always return `[]`?  Is that the wrong property for the currently-active
clip in the timeline editor?

### 2. viewport_playhead_atom Form-2 cycles every ~2 s — is this expected?

Our plugin sees `viewport_playhead_atom` Form-2 events firing roughly every 2 seconds,
cycling through all loaded playlist playheads.  We call `subscribe_to_playhead_events(...,
auto_cancel=True)` on each one, which may be amplifying the cycle.  This makes viewport
container state unreliable and forces us to use heuristic suppression timers.

**Ask**: Is the periodic Form-2 cycling expected behaviour?  What is the correct way to
subscribe to "the active viewport playhead changed" without receiving background cycling
events for inactive playlists?  Is `auto_cancel=False` with manual subscription management
safe, and what does `subscribe_to_playhead_events` return so we can cancel specific entries?

### 3. Annotation events — confirm ANNOTATIONS_CORE_PLUGIN broadcast group API

`annotation_atom` from the AnnotationsUI plugin never fires to an external plugin (it is an
internal routing tag, not a broadcast).  We currently hot-scan every 33 ms as a workaround.
The suggested fix is `join_broadcast_atom() + annotation_atom()` on `ANNOTATIONS_CORE_PLUGIN`'s
`live_edit_event_group_`, handling `(event_atom, annotation_data_atom, AnnotationBasePtr,
user_id, stroke_completed)` with `stroke_completed=True` as the pen-up signal.

**Ask**: Can they confirm this is the right group and atom?  Is `stroke_completed` reliably
set to `True` on the final `PaintEnd` event even for single-stroke bookmarks?

## Secondary questions (API gaps)

### 4. viewport_scale_atom / viewport_pan_atom in py_atoms.cpp

We read zoom via `serialise_atom` (fragile, write crashes xStudio) and pan is completely
disabled.  The fix is two `ADD_ATOM` lines in `py_atoms.cpp`.

**Ask**: Are `viewport_scale_atom` and `viewport_pan_atom` now exposed?  If so, what is the
Python call signature for reading and writing each?

### 5. item_atom on Timeline actors — is it in py_atoms.cpp?

We poll `_poll_sequence_new_media` every 500 ms to detect new clips added to a sequence.
The event-driven fix subscribes to Timeline `item_atom` events via `join_event_group`.

**Ask**: Is `item_atom` available on Timeline actors from Python?  Our `[TEST change_atom]`
subscription attempt failed with `invalid_argument` at connect — are we subscribing to the
wrong actor, or is the atom not yet exposed?

### 6. Adding playlists via script

**Ask**: What is the right API to create a new playlist/timeline and have it appear in the
xStudio UI?  Currently using internal actor calls that may not be the intended path.

# Questions for xstudio (older / misc)

* How is the plugin doing annotations, it seems to be getting messages directly from the AnnotationPlugin?
* **Viewport pan/zoom atoms** — expose `viewport_scale_atom` and `viewport_pan_atom` in `py_atoms.cpp` so Python plugins can read/write viewport zoom and pan without going through `deserialize_atom` (which crashes on `ColorTriplet` deserialization). `viewport_scale_atom` takes/returns a plain `float`; `viewport_pan_atom` needs either an `Imath::V2f` binding or a new `(float, float)` overload. This would enable bidirectional zoom sync (currently only xStudio→RV works).
* Once I've selected a clip, I cant select a sequence any more.
* Whats the right way to add playlists with a script to the UI?

# For Disney

* How do they handle playlists? What approaches are there?

# General questions

* Exposure is currently applied to all clips (all RVColor nodes normalized on change). This avoids spurious re-broadcasts when navigating between clips.


# Questions for openrv

How to lock out parts of the interface


33/42 tasks done. The remaining 9 are all live verification — no more implementation code needed.

The code work is complete. What's left is you running live tests in RV (+xStudio) to confirm:

Task	What to verify
6.3	Paint a stroke on an OTIO-origin clip; confirm it reaches the peer
7.2	Run an existing native-timeline test (add/delete/reorder clip); confirm no regression
8.1	Already covered by otio_import_rv_to_rv passing — just needs checkbox
8.2	Swap a clip's source file; confirm a whole-OTIO push arrives on peer (not a per-property patch)
8.3	Trim a cut in/out; confirm it reaches peer via REPLACE_TIMELINE
8.4	Change OCIO colorspace on an OTIO clip; confirm it syncs
8.5	Add a clip to the OTIO sequence; confirm REPLACE_TIMELINE reaches peer
8.6	Connect an older peer with no sync.origin; confirm treated as native
8.7	Reorder in a native session; confirm MOVE_CHILD still fires, not snapshot
8.2 and 8.3 description in the tasks says "property patch" — worth noting those actually fall back to whole-OTIO push (per the §5 implementation choices), so the assertions should say "REPLACE_TIMELINE arrives" not "SET_PROPERTY arrives". Once you've done the live run, I can check those off and we can archive the change.

ORI_SESSION="amqps://aswf:29a5953658144449ac73385b9e3144eedb76@146.235.219.100:5671/aswf?cacertfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/ca.pem&certfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.crt&keyfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.key:samtest"  ORI_SYNC_LOG_FILE=/Users/sam/git/ORIAnnotations/rvplugin/ori_sync/xstudio_host2.log xstudio test_media/source/encoded/*.mov

ORI_SESSION="amqps://aswf:29a5953658144449ac73385b9e3144eedb76@146.235.219.100:5671/aswf?cacertfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/ca.pem&certfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.crt&keyfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.key:samtest"  ORI_SYNC_LOG_FILE=/Users/sam/git/ORIAnnotations/rvplugin/ori_sync/xstudio_client2.log xstudio -n