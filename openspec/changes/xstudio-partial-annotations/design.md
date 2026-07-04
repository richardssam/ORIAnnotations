## Context

Partial annotation strokes drawn in xStudio never reach peers because the mid-stroke geometry is dropped at the C++/Python boundary. `AnnotationsCore::broadcast_live_stroke` builds an `Annotation` holding the cumulative in-progress stroke, but broadcasts only a geometry-less 4-tuple to the Python-facing `plugin_events_` group (added by the `pr/annotation-stroke-events` branch, commit `7d679cc8`). The ORI sync plugin therefore falls back to per-tick bookmark hot-scanning, which finds nothing because no bookmark is committed mid-stroke.

Two facts from exploration make this a small change:
- The in-progress `live_stroke` is **cumulative** — PaintStart resets it, each PaintPoint `add_points()` grows it, so serializing at any PaintPoint yields the full stroke-so-far.
- The Python receive path already works (RV→xStudio partials render), and `broadcast_live_stroke_from_json` is already wired to a `("live_stroke", raw_json)` dispatch expecting `{"Data": {"pen_strokes": [...]}}` — exactly what `Annotation::serialise()` produces.

The change spans two repos: xStudio C++ (`annotations_core_plugin.cpp`) and the ORI sync Python plugin (`annotation_sync.py`, `playback_sync.py`, `ori_sync_plugin.py`).

## Goals / Non-Goals

**Goals:**
- xStudio partial strokes appear on peers in near-real-time, using the direct event-geometry path.
- Eliminate per-tick bookmark hot-scanning as a partial-stroke mechanism.
- Land the C++ change as an amendment to `pr/annotation-stroke-events` so the PR ships as the 5-tuple, not a superseded 4-tuple.
- Preserve backward tolerance: builds still emitting the 4-tuple degrade to pen-up-only.

**Non-Goals:**
- Changing the pen-up / committed-annotation bookmark flush path (unchanged).
- Changing the 30-second fallback safety-net scan (unchanged).
- Changing the `live_edit_event_group_` in-process `AnnotationBasePtr` broadcast (unchanged).
- Reworking the receive-side partial renderer (`apply_partial_annotation_xs`) — already working.
- Laser strokes (`broadcast_live_laser_stroke`) — out of scope for this change.

## Decisions

### D1: Serialize the live stroke into the existing `plugin_events_` broadcast (amend, don't add)

Replace the geometry-less 4-tuple that `pr/annotation-stroke-events` introduced with the 5-tuple `(event_atom, annotation_data_atom, JsonStore, user_id, stroke_completed)`, where the `JsonStore` is `Annotation::serialise()` of the same `anno` already constructed for the `live_edit_event_group_` send.

*Alternative considered:* broadcast `annotation_atom` (PaintPoint payloads) from `AnnotationsUI` to its own `plugin_events_` (the reference `xstudio_live_review.py` approach). Rejected: requires incremental point accumulation + coordinate mapping in Python, a second codec path, and re-plumbing the UI plugin — whereas AnnotationsCore already assembles the cumulative stroke and the Python JSON path already exists.

### D2: Ownership — serialize before transferring `anno` to `AnnotationBasePtr`

`anno` is `new Annotation()` and currently moved into `AnnotationBasePtr(anno)` for the `live_edit_event_group_` send. `Annotation::serialise(utility::Uuid&)` is const, so the safe ordering is: build `anno`, wrap it in a shared `AnnotationBasePtr` once, serialize via that pointer, then send both broadcasts from the shared pointer. This avoids a second `new`, a use-after-move, and a leak.

*Alternative considered:* serialize from the raw `anno` before wrapping. Works, but reusing one `AnnotationBasePtr` for both sends is clearer and matches existing lifetime handling.

### D3: Make the JSON path primary in Python; remove hot-scan trigger

In `on_core_annotation_event`, the `has_json` branch (`("live_stroke", raw_json)`) becomes the sole mid-stroke path. Remove the legacy `else` branch's hot-scan activation and the `("hot_scan", None)` command. Retire `hot_scan_active_annotation` and its poll-loop invocation, plus the `_hot_scan_*` state and any `playback_sync.py`/`ori_sync_plugin.py` references that arm it. The geometry-less 4-tuple path keeps only the `stroke_completed=True` flush (pen-up), giving old builds pen-up-only behavior.

*Alternative considered:* keep hot-scan strictly as a legacy fallback. Rejected per the explicit goal of minimizing polling; the user accepted pen-up-only for old builds.

### D4: UUID stability preserved as-is

`broadcast_live_stroke_from_json` already assigns a stable per-gesture UUID via `_stroke_uuid_cache` / `_live_stroke_current_key`, and `clear_live_stroke` resets it on pen-up so the committed flush reuses the slot. No change needed; the removal of hot-scan must not disturb this cache or the `clear_live_stroke` command.

## Risks / Trade-offs

- **[Serialisation cost per PaintPoint]** → `Annotation::serialise()` runs on every point at pen speed. Strokes are small (one growing stroke); cost is bounded and only during active drawing. If it proves heavy, throttle on the Python side (already has `_last_partial_render_time` scaffolding), not in C++.
- **[Cumulative resend grows with stroke length]** → each PaintPoint reserializes the whole stroke-so-far, so a long stroke sends O(n²) total points across the gesture. Acceptable for interactive strokes; the receiver replaces in place by UUID so no accumulation there.
- **[Old xStudio builds lose partials]** → accepted trade-off; they degrade to pen-up-only via the 4-tuple flush path. No error, no hot-scan.
- **[Removing hot-scan touches shared state]** → `_stroke_uuid_cache` and `clear_live_stroke` are shared between the hot-scan and JSON paths. Mitigation: remove only hot-scan-exclusive state; verify the JSON path and pen-up flush still resolve the same UUID slot.
- **[Two-repo lockstep]** → the Python primary-path change assumes the 5-tuple. Mitigation: the legacy-tolerant discrimination (by tuple length) means a Python build ahead of the C++ build still works (pen-up-only) rather than breaking.

## Migration Plan

1. Amend commit `7d679cc8` on `pr/annotation-stroke-events` to emit the 5-tuple; rebuild the annotations plugin.
2. Land the Python changes (JSON path primary, hot-scan removed).
3. Verify end-to-end: xStudio→RV and xStudio→xStudio show partials mid-stroke; confirm no per-tick bookmark enumeration during drawing (log/inspection).
4. Rollback: revert the Python hot-scan removal and the C++ amendment independently — the tuple-length discrimination keeps either side safe if only one is reverted.

## Open Questions

- Should partial broadcasts be throttled (e.g., coalesce PaintPoints) to cap network/serialisation rate, or is one-per-point acceptable at typical pen rates? Default: no throttle initially; revisit if load is observed.
- Do shape tools (Square/Circle/Arrow/Line) — which set the stroke via `make_*` rather than `add_points` — serialize sensibly mid-drag through the same path, or should they broadcast only on pen-up? To confirm during implementation.
