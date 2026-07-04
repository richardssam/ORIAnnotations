## Why

Partial (in-progress) annotation strokes drawn in xStudio do not reach peers — only the final committed stroke appears after pen-up. The root cause is a geometry gap: xStudio's `AnnotationsCore::broadcast_live_stroke` emits a geometry-less 4-tuple to its `plugin_events_` group, so the ORI sync plugin cannot see the stroke points mid-gesture. The plugin's only fallback is to hot-scan bookmarks every poll tick — but no committed bookmark exists mid-stroke, so partials never render. Meanwhile the plugin's *direct* geometry path (`broadcast_live_stroke_from_json`) is already fully built and waiting for a JSON payload that nothing sends.

The receive side already works (RV→xStudio partials render), the in-progress `live_stroke` is cumulative, and `Annotation::serialise()` already produces the exact JSON shape the plugin expects. Closing the gap is a small, well-scoped change that also lets us **remove the hot-scan polling path** — a standing goal, since bookmark hot-scanning is CPU-wasteful, races the commit, and is the source of the current failure.

## What Changes

- **xStudio C++ (`AnnotationsCore::broadcast_live_stroke`)**: **amend the `pr/annotation-stroke-events` broadcast** (commit `7d679cc8`) rather than adding a new one — serialize the cumulative `live_stroke` via `Annotation::serialise()` and include it in the existing `plugin_events_` broadcast, turning the geometry-less 4-tuple that PR introduced into the geometry-bearing 5-tuple `(event_atom, annotation_data_atom, JsonStore, user_id, stroke_completed)`. The PR is not yet landed upstream, so it should ship as the 5-tuple from the start.
- **ORI sync plugin (Python)**: make the direct live-stroke JSON path the primary partial-broadcast mechanism. On each PaintPoint the plugin broadcasts the growing stroke directly from event JSON, keyed by a stable UUID so peers update in place.
- **Remove/retire bookmark hot-scan as a partial-stroke path**: with geometry arriving directly, `hot_scan_active_annotation` and its per-tick bookmark scanning are no longer needed for partials. Retire the hot-scan trigger; keep only the debounced pen-up flush (bookmark-backed) for the final committed annotation. **BREAKING** for the `xs-event-annotation` capability's hot-scan requirements.
- **Legacy fallback**: retain graceful handling of the geometry-less 4-tuple (older xStudio builds) so the plugin degrades to pen-up-only rather than erroring.

## Capabilities

### New Capabilities
- `xstudio-live-stroke-broadcast`: The xStudio AnnotationsCore contract for broadcasting in-progress stroke geometry to Python plugins — serializing the cumulative `live_stroke` into the `plugin_events_` group as a JSON-bearing event on every PaintStart/PaintPoint/PaintEnd. Realized by amending the `pr/annotation-stroke-events` broadcast, not by adding a second one.

### Modified Capabilities
- `xs-event-annotation`: Partial strokes flow via the direct live-stroke JSON (5-tuple) path instead of bookmark hot-scan; the "PaintPoint triggers immediate hot-scan" requirement is replaced by direct-geometry broadcast, and hot-scan is demoted to a legacy-only fallback (or removed).

## Impact

- **xStudio repo** (`/Users/sam/git/xstudio`): `src/plugin/viewport_overlay/annotations/src/annotations_core_plugin.cpp` — amends commit `7d679cc8` on the `pr/annotation-stroke-events` branch (mirrored on `xstudio_sync_fixes`) so the PR lands as the geometry-bearing 5-tuple. Requires a rebuild of the annotations plugin.
- **ORI sync plugin**: `xstudio_plugin/ori_sync/annotation_sync.py` (dispatch, `broadcast_live_stroke_from_json`, removal of `hot_scan_active_annotation` wiring), `playback_sync.py` and `ori_sync_plugin.py` (hot-scan trigger/poll references).
- **Cross-build compatibility**: plugin must tolerate both the new 5-tuple and the legacy 4-tuple.
- **Performance**: eliminates per-tick bookmark scanning during drawing — the explicit goal of minimizing polling/hot-scanning.
