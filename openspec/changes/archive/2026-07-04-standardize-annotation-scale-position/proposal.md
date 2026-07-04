## Why

The OTIO-SyncEvent ⇄ RV-paint-node mapping is implemented **four separate times** with no shared code:

1. `testchart/batch_openrv_helper.py` — richest offline renderer (pen · erase · text · ellipse · rect · arrow, gauss/splat, `w*0.6`, `size/2`, height-aware)
2. `rvplugin/ori_annotations/ori_annotations_plugin.py::import_annotations` — stale subset (pen · erase · text, `splat` hardcoded `0`)
3. `rvplugin/ori_annotations/ori_annotations_plugin.py::export_annotations` — the reverse (paint → SyncEvent)
4. `rvplugin/ori_sync/annotation_sync.py` — live sync (`_apply_annotation_render` / `_replace` / `_partial` / `_import_existing_rv_annotations`), full incl. shapes and UUID-keyed reconcile

The xStudio side does **not** have this problem: `batch_xstudio.py` renders by calling the plugin, which delegates to the single pure `otio_sync_core.xs_annotation_codec`. RV has no equivalent — the testchart bypasses the plugin entirely and hand-rolls rendering.

On top of the duplicated *structure*, the underlying coordinate/scale *math* is also duplicated: `0.8889` appears 13×, `5000.0` appears 9×, `w*0.6` 5×, with conflicting spacing defaults (0.0 / 0.8 / 1.0) and a `scale` field that is silently dropped on some round-trips. Copies 2 and 4 match events with `isinstance(ev, otio.schemadef.SyncEvent.PaintStart)`, which silently returns `False` under schemadef double-load — a real correctness bug that makes annotations vanish.

## What Changes

Two layers, one authoritative each, consumed by all RV call sites:

- **Layer 1 — `otio_sync_core.coords`**: host-neutral OTIO-normalized geometry — aspect_half, pixel↔OTIO-norm, and shared annotation defaults (named constants). Host-specific *unit* conversions (RV's `font_size↔rv_size`, width scale) live in that host's codec, not here — mirroring how `xs_annotation_codec` already keeps xStudio's `2.5` font factor inline.
- **Layer 2 — `otio_sync_core.rv_annotation_codec`** (new, beside `xs_annotation_codec`): the RV structure codec. Pure `sync_events_to_rv_specs(events, ctx) -> list[PaintNodeSpec]` and `rv_specs_to_sync_events(read_props) -> list[SyncEvent]`, plus a thin RV-only `apply_specs(specs, commands, mode=...)` edge (append vs UUID-keyed reconcile). Matches events by `schema_name()`, not `isinstance` — fixes the double-load bug.

- **Design for future hosts.** OTIO SyncEvent is the interlingua; each host codec is a spoke (`host-native ⇄ SyncEvent`) with no host-to-host adapters, so a new host (Nuke Studio, other RV-layered paint tools) is added as one new codec conforming to a shared contract — without editing existing codecs or `coords`. Shape tessellation (currently buried in `xs_annotation_codec`) moves to a shared `otio_sync_core.shapes` helper so any host lacking native shapes degrades gracefully. Two hosts (RV, xStudio) prove the seams; no third host is built here.
- **Migrate all four RV call sites** onto the codec (full scope): `batch_openrv_helper.py`, `ori_annotations_plugin.py` (import **and** export), and `rvplugin/ori_sync/annotation_sync.py` (all render/replace/partial/import paths).
- Wire `xs_annotation_codec.py` and both xStudio load plugins to `coords` (constants only).
- Add automated text-annotation comparison to the testchart harness (caption anchor position + rendered size).
- Reconcile conflicting defaults: one canonical `DEFAULT_SPACING` (0.8) and `DEFAULT_FONT_SIZE`.
- Fix the stale height/width coordinate comment in `generate_testchart.py`.

### Release note: xs-originated caption spacing is now visibly different in RV

Captions authored in xStudio (which has no letter-spacing concept) previously emitted `spacing = 0.0` when converted to a `TextAnnotation` and rendered in RV, which collapses RV's letter spacing to illegible. This change emits `coords.DEFAULT_SPACING` (0.8, RV-neutral) instead. **User-visible effect**: xs-originated captions render with correctly-spaced (previously squashed) text in RV after this change ships — not a regression, but a visible rendering difference worth calling out in release notes.

### Reconciled: `scale` round-trip (was contradictory)

The prior draft's proposal claimed a "BREAKING scale-drop fix" while its design (D4) said `scale` is not a bug. Resolution: **xStudio→OTIO** direction has no host scale field, so emitting `scale=1.0` is correct (no change). The **RV** paint node *does* have a `scale` property, and `export_annotations` already reads it (`ori_annotations_plugin.py:263`) — so the codec preserves `scale` on the RV round-trip and drops it only where the host genuinely lacks the concept. No user-facing BREAKING change; the "silent drop" existed only in the duplicated inline copies that the codec replaces.

## Capabilities

### New Capabilities

- `annotation-coord-transform`: `otio_sync_core.coords` — canonical host-neutral geometry: aspect_half, pixel↔OTIO-norm, and shared annotation defaults. Single source of truth for the OTIO-normalized space every host converts to/from. RV/xStudio font & width *unit* conversions live in their respective codecs.
- `rv-annotation-codec`: `otio_sync_core.rv_annotation_codec` — the pure SyncEvent↔RV-paint-node structure codec (the `PaintNodeSpec` IR + a thin `apply_specs` edge). Owns which properties, node-name conventions, per-frame `order` lists, shape geometry, gauss/splat, and width for pen · erase · text · ellipse · rect · arrow. Shared by the testchart, both load-plugin directions, and the live-sync renderer.
- `testchart-text-comparison`: automated pass/fail verification of text rendering (caption anchor position + glyph size against reference PNGs), extending `compare_testchart.py` / `compare_thickness.py`.

### Modified Capabilities

- `otio-annotation-sync`: correct the "xStudio Stroke Coordinate Mapping" requirement (it wrongly states no transform is applied — the `aspect_half` transform is real). Add `TextAnnotation` font-size symmetry and the reconciled `scale` round-trip clause.
- `xstudio-annotation-export`: add the `scale` round-trip clause consistent with the reconciliation above.

## Impact

- **New files**: `python/otio_sync_core/coords.py`, `python/otio_sync_core/rv_annotation_codec.py`, `python/otio_sync_core/shapes.py` (shared shape tessellation, extracted from `xs_annotation_codec`)
- **Modified (RV call sites, onto codec)**: `testchart/batch_openrv_helper.py`, `rvplugin/ori_annotations/ori_annotations_plugin.py`, `rvplugin/ori_sync/annotation_sync.py`
- **Modified (coords wiring)**: `python/otio_sync_core/xs_annotation_codec.py`, `xstudio_plugin/ori_annotations/ori_annotations.py`, `testchart/generate_testchart.py`
- **Modified (harness)**: `testchart/compare_testchart.py` (text comparison added)
- **Dependency**: `otio_sync_core` is on RV's `PYTHONPATH` (testchart adds `python/` via `batch_openrv.py`; the RV sync plugin already imports `otio_sync_core.manager`) and is imported by both xStudio plugins — so both new modules are immediately importable everywhere.
- **Overrides the prior draft**: old D6 ("batch not restructured") and the "live sync = follow-up" non-goal are superseded — both are now in scope.
