## Context

See proposal.md — Why. The design-relevant facts, all measured against a running xStudio rather than inferred from source:

- `plugin_base.subscribe_to_plugin_events` joins `plugin_events_group_atom()`. `PluginBase` spawns that group without an owner and nothing sends to it, so the subscription is inert but raises no error.
- AnnotationsCore emits live strokes twice: `mail(...)` to `live_edit_event_group_` carrying an `AnnotationBasePtr`, and `anon_mail(...)` to `draw_events_event_group_` carrying the serialised JSON with `user_id` and `stroke_completed` injected. Only the second is reachable from Python — `live_edit_event_group_` is exposed solely through a four-argument `join_broadcast` handler that the Python API cannot send.
- AnnotationsCore also re-broadcasts the raw draw interactions (`PaintStart`, `PaintClear`, `HideDrawings`, …) on `draw_events_event_group_`, having received them point-to-point from AnnotationsUI.
- One synthetic pen gesture produced 0 events on `plugin_events_` and 10 on the draw-events group, identically on the `develop` and per-subscription-listener xStudio builds.

## Goals / Non-Goals

**Goals:**

- One subscription carrying both event kinds, using an existing xStudio API.
- Preserve every observable behaviour the current handlers implement, including the debounced-flush scheduling and the `[2C]` log lines the spec requires.
- Remove shape-guessing: no tuple-length discrimination, no CAF element type checks in the handlers.

**Non-Goals:**

- Reaching `live_edit_event_group_`. It would need an xStudio-side accessor; the serialised form on the draw-events group carries the same geometry.
- Any change to the wire protocol, to peers, or to how partials are throttled, uuid-stamped, or flushed.
- Fixing the `xstudio_draws_pen_openrv_verifies` suite failure, which writes bookmarks directly and never reaches AnnotationsCore.

## Decisions

**Subscribe via `subscribe_to_annotation_draw_events` rather than joining the group by hand.**
The helper resolves `get_event_group_atom + annotation_atom`, decodes the `JsonStore`, and flattens both message shapes into `(event_data, user_id, stroke_completed)`. Joining the group directly would mean re-implementing that decode for no benefit. Alternative considered: adding a `get_event_group_atom(annotation_data_atom)` accessor to xStudio for `live_edit_event_group_` — rejected as it blocks this fix behind an upstream merge, for geometry we can already get.

**Dispatch on the presence of `stroke_completed`, not on payload keys.**
The helper passes `user_id=None, stroke_completed=None` for interactions and real values for strokes, so the discriminator is structural rather than a guess about JSON content. Alternative considered: sniffing for an `"event"` key — rejected as it couples the dispatcher to payload schema that AnnotationsCore may change.

**Keep the two handlers, change their input contract.**
`on_annotation_event` and `on_core_annotation_event` keep their names and their logic; only their parameters change, from a CAF tuple to decoded data. This keeps the diff to the parsing preamble and leaves existing spec and doc references pointing at real methods.

**Keep the `AnnotationsUI` handle without subscribing to it.**
`display_sync.apply_display_state` sets its "Visibility" attribute. The lookup stays; only the inert subscription goes.

## Risks / Trade-offs

- **A clear is now detected from `PaintClear` rather than from AnnotationsCore's post-clear state broadcast.** → `PaintClear` fires on the same gesture and schedules the same debounced flush, and the count-decrease detection in the scan does the real work. Verified reachable in the probe output.
- **The draw-events group is created lazily by AnnotationsCore on first request.** → `subscribe_to_annotation_draw_events` issues that request, so the group exists by the time we join; the connect-time subscription is wrapped in the existing try/except and degrades to the fallback scan if it ever raises.
- **Shape tools deliver no geometry until completed** (AnnotationsCore only serialises them once finished). → Already the documented no-partial path; the pen-up flush still carries the committed shape.
- **Interactions now arrive that never did before** (`PaintStart`/`PaintPoint`/`PaintEnd` each schedule a scan). → These set the existing debounce timestamp rather than performing work, and pen-up scheduling is what the spec already requires; watch for scan-rate regression in the fallback-scan requirement.

## Migration Plan

None. No persisted state, no protocol change, and no peer-visible behaviour that could be half-migrated: the plugin either has the subscription or falls back to the scan, exactly as before. Rollback is reverting the two files.
