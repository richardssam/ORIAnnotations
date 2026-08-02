# fix-xs-annotation-draw-subscription

## Why

The xStudio plugin's two annotation subscriptions joined event groups that nothing ever broadcasts on, so neither handler had ever fired: `on_core_annotation_event` (the `[2C]` live-stroke path — mid-stroke partials and the pen-up flush) and `on_annotation_event` (`PaintClear`, `HideDrawings`/`ShowDrawings`) were both dead. Annotation sync out of xStudio was therefore running entirely on the 30-second fallback scan that `xs-event-annotation` describes as a safety net.

`PluginBase` spawns a plugin's `plugin_events_` group without an owner and never broadcasts on it. AnnotationsCore emits live strokes on `live_edit_event_group_` (not reachable from Python) and a serialised copy, plus the raw draw interactions, on the group returned by `get_event_group_atom + annotation_atom` — which `plugin_base.subscribe_to_annotation_draw_events` exists to join. Probing both the `develop` and per-subscription-listener xStudio builds showed 0 events on `plugin_events_` and 10 on the draw-events group for one pen gesture, so this is a plugin-side wiring bug, independent of the xStudio event-routing work.

## What Changes

- Replace the `subscribe_to_plugin_events(AnnotationsUI, …)` and `subscribe_to_plugin_events(AnnotationsCore, …)` calls with a single `subscribe_to_annotation_draw_events(…)`.
- Add an `on_draw_event(event_data, user_id, stroke_completed)` dispatcher that routes on `stroke_completed`: `None` means a raw draw interaction, otherwise a serialised live stroke.
- `on_annotation_event` and `on_core_annotation_event` take decoded payloads instead of CAF tuples; the tuple-shape guards, the legacy geometry-less 4-tuple branch, and the 3-tuple clear branch all go away.
- **BREAKING** (spec-level only, no API surface): a local clear is now detected from the `PaintClear` interaction rather than from AnnotationsCore's 3-tuple `(event_atom, annotation_data_atom, AnnotationBasePtr)` broadcast, which is emitted on a group Python cannot join.
- Keep the `get_plugin("AnnotationsUI")` handle — `display_sync` needs it for the "Visibility" attribute — but no longer subscribe to it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `xs-event-annotation`: the subscription route and the message shapes consumed; the AnnotationsUI-interaction requirement now describes traffic arriving on AnnotationsCore's draw-events group; local-clear detection moves to `PaintClear`.
- `annotation-lifecycle-sync`: the local-clear scenario names the 3-tuple trigger that no longer exists.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — subscription block, callback wrapper.
- `xstudio_plugin/ori_sync/annotation_sync.py` — new dispatcher, both handlers, three now-unused `xstudio.core` imports.
- Log lines: `"[2C] First AnnotationsCore event received"` now actually emits; interaction logs no longer claim to come from AnnotationsUI.
- `docs/xstudio_constraints.md` — the section stating `on_core_annotation_event` is "confirmed firing" on `plugin_events_` was true of an older xStudio build and is now wrong.
- No change to the sync protocol, to peers, or to the xStudio C++ side.
