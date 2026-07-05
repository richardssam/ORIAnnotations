## Context

`testchart/` (`batch_openrv_helper.py`, `batch_xstudio.py`) only exercises the forward direction: pre-built `SyncEvent`s imported into an app (`sync_events_to_rv_specs`, `sync_events_to_xs_strokes`) and rendered. It never asks an app to draw something natively and read it back (`rv_strokes_to_sync_events`, `xs_strokes_to_sync_events`). A pen-width bug (forward multiplied by `RV_WIDTH_SCALE`, reverse forgot to divide by it) shipped invisibly for exactly this reason, and a user report that rectangle borders look "even more off" than pen lines suggests the box-shape conversion (`_box_shape_spec`'s `size / 2.0`, with no analogous empirical correction) may have a similar unverified-calibration problem.

Traced during design (see below): RV's send path is a direct, synchronous, in-process function call reachable from the test harness. xStudio's send path is event/poll-driven and reachable only through the harness's existing remote `Connection`, not a direct Python call — so the two hooks need materially different trigger mechanisms even though the write side (raw native properties/dicts) is similar in spirit.

## Goals / Non-Goals

**Goals:**
- Add a `draw_annotation` script-driven action to both `openrv_hook.py` and `xstudio_hook.py`, covering `pen` and `rect` kinds.
- Trigger the *real* production send path in both apps — not a reimplementation of broadcasting — so the test exercises exactly what a live user stroke would.
- Extend the `/state` RPC on both hooks to surface per-stroke native width/size/thickness, additively (existing `annotation_count`/`strokes`/`captions` fields untouched).
- Assert round-trip fidelity by computing the *expected* peer-side value from the same production codec functions (`rv_annotation_codec`, `xs_annotation_codec`) the app itself uses, rather than a hardcoded number — so the assertion fails whenever forward/reverse disagree, which is exactly the shape of the bug that motivated this change, and stays meaningful if a calibration constant is later retuned.
- Run `pen` bidirectionally (RV draws/xStudio reads, xStudio draws/RV reads).

**Non-Goals:**
- Driving real mouse/UI input — writes go directly to native properties/dicts.
- Partial/live-stroke (mid-drag) broadcast timing — this tests the final/committed stroke only.
- `rect` in the xStudio → RV direction — xStudio's native shape-drawing tools (Square/Circle/Arrow/Line/Ellipse, per the `xstudio-partial-annotations` change) have no wired-up Python broadcast path yet: `xs_annotation_codec.SUPPORTED_KINDS` is `{pen, erase, text}` only, and there is no `xs_strokes_to_sync_events`-side shape handling — only the receive-side tessellation (`sync_events_to_xs_strokes`'s `is_rect`/`is_ellipse`/`is_arrow` branch). Bidirectional shape coverage is future work gated on that capability (tracked as task 4.3 in `xstudio-partial-annotations`).
- Pixel-rendered visual comparison — that's `testchart/compare_thickness.py`'s job (a different, complementary concern: does it *look* right vs. is the *data* self-consistent).
- Gaussian-brush calibration (`xs_annotation_codec`'s `* 0.75` correction) — v1 uses a plain/oval brush to keep the expected-value formula simple.

## Decisions

### D1: RV send-trigger — call `_broadcast_annotation` directly via a new registration slot

`AnnotationSyncController._broadcast_annotation(node_name, component, partial=False)` is confirmed (by reading `rvplugin/ori_sync/annotation_sync.py`) to be the exact function RV's real pen-up handler (`_on_pen_up` → `_flush_pending_stroke`) calls. `openrv_hook.py` runs in-process (it's loaded into the same RV Python interpreter, unlike the xStudio hook), so it can call this directly — it just needs a handle to the live controller instance.

`otio_sync_core/inspection.py` already has exactly this pattern for the `SyncManager` (`register_manager`/`get_registered_manager`, called from `plugin.py`). Add a parallel pair, `register_annotation_controller`/`get_registered_annotation_controller`, and call it from `plugin.py` right next to the existing `otio_sync_core.register_manager(self.sync_manager)` call (`self.annotation` already exists as a sibling attribute at that point).

`draw_annotation` then:
1. Writes raw paint-node properties directly via `rv.commands.newProperty`/`setFloatProperty` — **not** via `rv_paint_applier.apply_specs` / `sync_events_to_rv_specs`. Going through the applier would re-exercise the already-covered forward codec (OTIO → RV) and silently test the wrong direction. Property sets per kind, matching what `rv_paint_applier.read_stroke` parses back:
   - `pen`: `brush`, `color`, `width` (chosen nominal RV-native value), `points`.
   - `rect`: `min`, `max`, `borderColor`, `innerColor`, `borderWidth` (chosen nominal RV-native value).
2. Appends the item to `{node}.frame:{frame}.order` using the same `f"{prefix}:{strokeid}:{frame}:{user}"` naming `rv_paint_applier._apply_append` uses (import the prefix map / next-id helper rather than re-deriving the format ad hoc).
3. Calls `get_registered_annotation_controller()._broadcast_annotation(node_name, component)` — the real send path, unmodified.

*Alternative considered:* simulate a pen-up by binding/firing RV's actual UI event. Rejected — brittle to drive headlessly, and the reverse codec functions only care about node-property state, not how it got there.

### D2: xStudio send-trigger — write via the existing remote API, rely on the plugin's own poll loop

`xstudio_hook.py` runs **out-of-process**, talking to the live xStudio session only through `xstudio.connection.Connection` (confirmed by reading the file — no in-process handle to the plugin's Python objects is available, unlike RV). The real send-side function (`AnnotationSyncController.flush_pending_annotations` → `broadcast_local_bookmark`, in `xstudio_plugin/ori_sync/annotation_sync.py`) already runs continuously from the plugin's own poll loop and scans **all** session bookmarks each tick (not just ones it created) — it does not require an in-process call to trigger.

So `draw_annotation` on the xStudio side:
1. Writes a bookmark on the current frame via `bm.set_annotation(strokes=[{...}])` through the harness's existing `Connection` — the same remote API `xstudio_hook.py` already uses for state reads. Because harness and plugin share the same live document, this is a real bookmark the plugin's poll loop will see, not a shadow copy.
2. The test then waits (bounded, polling `/state`, matching the convergence-wait pattern already used elsewhere in `runner.py`) up to `DEBOUNCE_SECONDS + ANNOTATION_SCAN_INTERVAL` (+ margin) for the plugin's own `flush_pending_annotations` to pick it up and broadcast for real.

This requires **zero new xStudio-plugin code** — the send side is 100% existing production code, exercised as-is. The only cost is that this path is asynchronous/timing-based rather than a direct call like RV's.

*Alternative considered:* have the harness call into the plugin's Python objects directly, the way `batch_xstudio.py` instantiates `ORIAnnotationsPlugin(conn)` for OTIO import. Rejected for this use case — that pattern works for `ori_annotations` (a stateless import plugin designed to be driven externally), but `ori_sync`'s live `AnnotationSyncController` is not designed as an externally-driven object, and reaching into it would mean adding new plugin-side surface area for zero benefit over just letting the existing poll loop do its job.

### D3: Assertions compute expected values from the same codecs, end-to-end

For `pen`, RV → xStudio: expected xStudio thickness = `(nominal_rv_width / RV_WIDTH_SCALE) / (2 * aspect_half)`, i.e. run the nominal RV-native width through `rv_annotation_codec`'s reverse formula to get OTIO size, then through `xs_annotation_codec`'s forward formula to get xStudio thickness — reusing the actual functions/constants, not copy-pasted arithmetic, so the test doesn't rot if either formula changes. Symmetric for xStudio → RV. For `rect` (RV → xStudio only), same idea through `_box_shape_spec`'s `size / 2.0` inverse and `sync_events_to_xs_strokes`'s `is_rect` thickness formula.

This is deliberately an **end-to-end closed-form check**, not a "does the peer's value differ from the sender's by a plausible amount" fuzzy check — it will fail precisely when the forward and reverse halves of a codec disagree, which is the exact shape of the bug that prompted this change, and it also gives the still-open box-shape-calibration question a concrete numeric answer once it's wired up (a *separate* rendering-calibration follow-up, not this change).

### D4: State-readback extensions are additive

`xstudio_hook.py`'s state getter already extracts the full `pen_strokes` list from `annotation_data["Data"]` and discards everything but counts — add `thickness` per stroke without touching the existing `strokes`/`captions`/`annotation_count` fields. `openrv_hook.py`'s getter currently only counts strokes — call the already-tested `rv_paint_applier.read_frame_strokes` and add `width`/`size` per stroke, same principle. No existing assertions elsewhere in `sync_test` should be affected.

### D5: Script-driven commands must wait for the driver to hold master (found via live testing)

A live run of `openrv_draws_rect_xstudio_verifies` surfaced a real bug this change's own tests exposed: OpenRV's `add_media`/`set_selection` commands were silently dropped and never reached the xStudio peer at all, even though the `draw_annotation` broadcast itself worked correctly end-to-end (xStudio's plugin log showed a real `RectangleAnnotation` arrive with the correctly-computed `size`, but referencing a `clip_guid` xStudio had never heard of).

Root cause, confirmed by reading `sequence_sync.py`'s `check_otio_snapshots`: structural changes (new media, selection) are broadcast **only by whichever peer currently holds master** — `if not mgr.is_master: self._otio_dirty = False; return` clears the pending-change flag even while not master, so the change is not merely delayed, it is permanently lost once missed. Annotation broadcasts (`broadcast_add_annotation`) have no such gate — any peer may broadcast its own annotation regardless of mastership — which is why the annotation itself went out fine while the underlying media add did not.

xStudio's manager self-promotes to master close to immediately on connect; OpenRV's manager instead asks `WHO_IS_MASTER` and only self-promotes after ~2s of silence, and (being a much heavier app to boot, per its own plugin-loading startup banner) can take considerably longer than that just to reach the point of asking. No existing `sync_test` case exercises "OpenRV drives a live structural change while xStudio is merely a peer" — existing xStudio-involving script-driven tests always have xStudio as the driver (already master), and existing OpenRV-driver tests pair it against another OpenRV instance (no cross-app asymmetry) — so this exact interaction had never been exercised before these new tests.

**Fix**: expose `is_master` as an extra key on `SyncManager.export_state()` (inert for existing structural comparisons — `project_state`/`diff_states` only read named `StateSnapshot` fields, not this key — but added to `compare_states`' `ignore_keys` anyway since it legitimately differs between peers). `openrv_hook.py`'s `/state` reads it directly off the registered manager (in-process); `xstudio_hook.py`'s `/state` reads it opportunistically from the existing `ORI_FULLSTATE_FILE` bridge (out-of-process, can't reach the manager object directly, but that file is `export_state()`'s own output). `Runner.run_test` now waits (bounded, polling) for the script-driven driver app to report `is_master: true` before sending its commands, logging a clear diagnostic on timeout instead of surfacing only as a generic downstream state-mismatch.

This is the one place this change touches genuinely non-test-only code (`manager.py`) — flagged explicitly in the proposal's Impact section rather than silently expanding scope, since it deviates from the original "test-infrastructure only" framing.

## Risks / Trade-offs

- **[RV harness reaches into paint-node naming conventions]** → mitigated by importing the prefix/strokeid helpers from `rv_paint_applier` rather than re-deriving the `"{prefix}:{strokeid}:{frame}:{user}"` format ad hoc in the hook.
- **[xStudio path is timing-based, not a direct call]** → slower and marginally more flake-prone than RV's synchronous path. Mitigated by a bounded poll-for-convergence (existing `runner.py` pattern) rather than a fixed sleep; `DEBOUNCE_SECONDS`/`ANNOTATION_SCAN_INTERVAL` are read from the live plugin config, not hardcoded, so the wait bound tracks reality if those constants change.
- **[New production-code surface for testing]** → `register_annotation_controller`/`get_registered_annotation_controller` in `plugin.py`/`inspection.py` is small, additive, and mirrors an existing, already-shipped pattern (`register_manager`) — low risk.
- **[Asymmetric coverage]** → `rect` is RV → xStudio only in this change; xStudio → RV shape coverage is explicitly deferred, not silently assumed. Documented in Non-Goals so it isn't mistaken for full symmetric coverage later.

## Migration Plan

N/A — additive test infrastructure only; no production behavior changes, no rollback concerns.

## Open Questions

- Once xStudio's native shape-drawing broadcast path lands (`xstudio-partial-annotations` task 4.3), extend `rect` (and other shapes) to the xStudio → RV direction here.
- Should gaussian-brush pen coverage (exercising `xs_annotation_codec`'s `* 0.75` correction) be added as a follow-up once plain-brush coverage proves the harness out?
