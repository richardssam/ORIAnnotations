## Why

`testchart/` only exercises the *forward* annotation path — pre-built OTIO `SyncEvent`s imported into OpenRV or xStudio and rendered (`sync_events_to_rv_specs` / `sync_events_to_xs_strokes`). It never exercises the *reverse* path — an app drawing an annotation natively and that being read back and converted into a `SyncEvent` to broadcast (`rv_strokes_to_sync_events` / `xs_strokes_to_sync_events`). A pen-stroke width bug just shipped and went undetected for this exact reason: `rv_strokes_to_sync_events` forgot to invert the forward direction's `* RV_WIDTH_SCALE`, so any stroke drawn natively in RV was broadcast at the wrong width — invisible to testchart, which never drives that code path, and invisible to unit tests, which only exercised each direction in isolation rather than a peer's live readback of the other's draw.

`sync_test` already launches real app instances, wires them together over a real RabbitMQ bus, and has a script-driven command mechanism (`add_media`, `set_selection`, ...) plus a `/state` RPC for introspection — the two missing pieces are (1) a way to make an app draw an annotation natively, and (2) exposing enough of the received annotation's geometry via `/state` to assert on it. Both are small, additive extensions of infrastructure that already exists.

## What Changes

- Add a `draw_annotation` script-driven action to `sync_test`, supported by both `openrv_hook.py` and `xstudio_hook.py`, covering two kinds for v1: `pen` (a short stroke) and `rect` (a square). The action writes native properties/dicts directly — RV paint-node properties via `rv_paint_applier`-shaped writes, xStudio stroke dicts via `Bookmark.set_annotation` — at a caller-specified nominal width/size, as if a real draw had just completed. It does not drive real mouse/UI input.
- Let the existing plugin broadcast path pick up the native write and send it over the real bus to the peer, unchanged.
- Extend the `/state` RPC on both hooks to surface per-stroke width/size, not just counts:
  - `xstudio_hook.py`'s state getter already extracts the raw `pen_strokes` list from `annotation_data` and discards everything but counts — surface `thickness` (and `size` for tessellated shapes) per stroke.
  - `openrv_hook.py`'s state getter currently only counts strokes — call `rv_paint_applier.read_frame_strokes` and surface `width`/`size` per stroke.
- Add sync test cases that draw in one app and assert, via the peer's `/state` readback, that the received native width/size round-trips (within `assertAlmostEqual`-style tolerance) to the same OTIO size the drawing app's own codec produced. Run both directions — RV draws/xStudio reads, and xStudio draws/RV reads — since a one-directional test would not have caught the asymmetric bug that motivated this change.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `ui-sync-testing`: adds a `draw_annotation` script-driven action (alongside `add_media`/`set_selection`/etc.) and extends the RPC introspection requirement so annotation state includes per-stroke width/size, not just a count.

## Impact

- `sync_test/python/sync_test/openrv_hook.py`: new `draw_annotation` branch in `execute_openrv_command`; state getter extended to call `rv_paint_applier.read_frame_strokes` and include `width`.
- `sync_test/python/sync_test/xstudio_hook.py`: new `draw_annotation` branch in `execute_xstudio_command`; state getter extended to include `thickness`/`size` from the already-fetched `pen_strokes` data.
- `sync_test/python/sync_test/runner.py`: no dispatch changes needed (actions already pass through generically) — new test case(s) in `sync_tests.yaml` / `sync_tests_xstudio.yaml` and possibly a small runner-side assertion helper for width tolerance comparison.
- `sync_test/README.md`: document the new `draw_annotation` action alongside the existing action table.
- `python/otio_sync_core/manager.py`: **one small production-code addition, found during implementation** — `SyncManager.export_state()` now includes a read-only `is_master` field. This surfaced from a real master-race bug the new tests exposed (see design.md D5): script-driven commands are only ever broadcast by whichever peer holds master, apps self-promote to master on different timescales, and no existing test previously drove OpenRV to make a live structural change (`add_media`) while xStudio was merely a peer — so this exact gap had never been exercised. `export_state()`'s consumers (`project_state`/`diff_states`) only read named `StateSnapshot` fields, so the extra key is inert for existing structural comparisons; `compare_states`' `ignore_keys` was updated to exclude it explicitly. This is the one deviation from this change's original "test-infrastructure only" framing — flagged rather than silently expanded.
