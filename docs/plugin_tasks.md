# Plugin improvement tasks

Derived from comparison with `sync_review_marshal.py` (LiveReview reference) and
`xstudio_live_review.py`.  RV fixes are mechanical and low-risk; xStudio items
depend on API investigation first.

---

## RV plugin — straightforward fixes

- [x] **Switch frame sync to `frame-changed` event**
  Replace the 33 ms poll for frame position with a `frame-changed` event binding.
  Only broadcast when not playing (play-start / play-stop already handle the
  playing case).  Removes idle CPU usage and reduces latency to zero.

- [x] **Switch pen-up detection to pointer events**
  Replace the 150 ms debounce timer with bindings for `pointer-1--release`,
  `pointer--leave`, and `pointer--control--leave`.  These fire immediately and
  unambiguously when the user lifts the pen or leaves the viewport.

- [x] **Switch stroke-start signal to `paint.nextId`**
  Detect new strokes by watching `paint.nextId` increment in `graph-state-change`
  (regex `r"paint\.nextId$"`) rather than inferring start from the first `.points`
  write.  `nextId` fires once before any points arrive — the definitive start
  signal with no timing heuristics.

- [x] **Increment `paint.nextId` when applying received strokes**
  After writing a remote stroke into RV's paint node, increment `nextId` by 1:
  ```python
  stroke_id = commands.getIntProperty(f"{paint_component}.nextId")[0]
  commands.setIntProperty(f"{paint_component}.nextId", [stroke_id + 1], True)
  ```
  Without this, RV's internal ID counter drifts and subsequent local strokes can
  silently overwrite received ones.

- [x] **Sync ghost/hold brush metadata**
  Include `hold`, `ghost`, `ghostBefore`, `ghostAfter` in the `PaintStart` payload
  and apply them on receive.  These control whether an annotation is visible on
  adjacent frames in RV; omitting them reduces annotation fidelity vs the
  reference implementation.

---

## xStudio plugin — investigate first

- [ ] **Investigate whether `annotation_atom` fires in current builds**
  `xstudio_live_review.py` receives annotation events via
  `subscribe_to_plugin_events(annotation_plugin, handler)` with
  `isinstance(data[1], annotation_atom)`.  Our plugin uses the same subscription
  but `annotation_atom` did not fire in early tested builds.  Re-test with the
  current xStudio build:
  - Add a log line inside the `annotation_atom` branch
  - Draw a stroke and check whether it fires
  - If it fires: remove the 500 ms fallback scan and replace with the event path
  - If it does not fire: document the xStudio build version and raise with the
    xStudio team

- [ ] **Investigate event-driven frame sync via `position_atom`**
  `xstudio_live_review.py` uses `subscribe_to_playhead_events` +
  `position_atom` for frame changes.  Our plugin polls because re-subscribing
  with `auto_cancel=True` drops previous subscriptions when multiple timelines
  are loaded.  Investigate whether subscribing once to the viewport playhead
  (obtained from `viewport_playhead_atom` in `global_playhead_events`) is
  sufficient for the single-active-playhead case, and whether it survives
  timeline switches without re-subscribing to all timelines.

- [ ] **Investigate `on_screen_container_events` for selection/container changes**
  `xstudio_live_review.py` uses `subscribe_to_event_group` with `change_atom`
  to detect when the viewed container changes.  We currently poll for this.
  If the event fires reliably, the container-change poll path can be removed.

- [ ] **Fix client→host sequence sync (trims, repositioning, reorders)**

  Changes made to a sequence timeline on a sync CLIENT (RV) do not reliably
  reach the xStudio HOST.  There are two compounding losses in the
  `to_otio_string()` round-trip that the polling code relies on:

  1. **MissingReference** — clips loaded from an xStudio sequence have
     `xstudio://` media URIs that RV cannot resolve; `to_otio_string()`
     emits `MissingReference` for every clip.  `poll_sequence_reorders` uses
     URL matching to detect reorders, so it always produces an empty
     `current_order` and never broadcasts `MOVE_CHILD`.
  2. **Metadata stripped** — xStudio's OTIO export copies no clip metadata,
     so the sync GUIDs assigned in `timeline_build.py` do not survive the
     round-trip.  Even if we switched from URL-based to GUID-based matching,
     the exported OTIO would have no GUIDs to match on.

  Gap-duration changes (clip repositioning within the timeline) are also
  invisible to the source-range fingerprint and not captured by any other poll.

  **Path A — fix xStudio's OTIO export (core C++ change):**
  If `to_otio_string()` preserved clip metadata, sync GUIDs would survive and
  GUID-based matching could replace URL-based matching.  Both reorder and
  source-range polling would work correctly.  Requires a change to xStudio's
  OTIO serializer.  *Ask the xStudio team: can clip metadata be preserved in
  `to_otio_string()`, or is there an alternative export path that does so?*

  **Path B — bypass OTIO round-trips with event-driven detection (plugin-only):**
  xStudio fires `item_atom` events when clips are trimmed or moved.
  `bootstrap_mapping` already builds a map from xStudio internal UUIDs to sync
  GUIDs.  If the client subscribed to `item_atom` events and used that
  UUID→sync-GUID map to identify which clip changed, it could query the clip's
  current `source_range` and track position directly from the xStudio API (no
  `to_otio_string()` needed) and broadcast the change.  This requires
  confirming that the clip `source_range` and Gap neighbors are queryable from
  Python without going through OTIO export (see `item_atom` question above).

  Current state: HOST→CLIENT sync (all three operations) works.
  CLIENT→HOST: none of the three operations (trim, reposition, reorder) is
  reliably working.
