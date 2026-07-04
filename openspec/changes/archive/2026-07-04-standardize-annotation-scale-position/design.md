## Context

The OTIO-SyncEvent ⇄ RV-paint-node mapping lives inline in four places (see proposal). The xStudio equivalent was already collapsed into one pure module, `otio_sync_core.xs_annotation_codec`, and both the xStudio batch harness and live sync call *through the plugin* into it. RV has no such module, so `testchart/batch_openrv_helper.py` reimplements the whole mapping and has drifted ahead of the real plugin (it grew shapes, gauss/splat, and height-awareness the plugin never got), while the live renderer `rvplugin/ori_sync/annotation_sync.py` is a third full copy.

Beneath the structural duplication is math duplication: `aspect_half` (`0.8889`), `font_size↔rv_size` (`5000.0`), width normalization (`w*0.6`), spacing defaults (0.0 / 0.8 / 1.0), and pixel↔OTIO-norm. This is the same problem the prior draft of this change targeted with a `coords` module; that module is still the right foundation and is retained here as Layer 1.

## Goals / Non-Goals

**Goals:**
- One authoritative math module (`otio_sync_core.coords`) and one authoritative RV structure codec (`otio_sync_core.rv_annotation_codec`).
- All four RV call sites render/parse through the codec; no site reaches around it to set paint properties directly.
- The pure codec is unit-testable **outside RV** by asserting on the `PaintNodeSpec` IR — where most of these bugs actually live.
- xStudio codec + load plugins adopt `coords` constants.
- Testchart gains automated text comparison.
- **Design the codec seams for future hosts** (Nuke Studio, other RV-layered paint tools) so a new host is added as one new spoke — a single codec module conforming to a shared contract — without editing existing codecs or `coords`. See D8–D10.

**Non-Goals:**
- xStudio-native per-caption `scale` (host has no such field; `scale=1.0` for xs→OTIO is correct — see D4).
- Font matching between RV and xStudio (out of scope for structure/coordinate unification).
- Changing the RV paint-node wire format or the SyncEvent schema — the codec centralizes the *existing* mapping, it does not redesign it.
- **Implementing any additional host** (Nuke, etc.) in this change — we define and prove the seams with two hosts (RV, xStudio); we do not build a third. No abstract framework beyond what two hosts justify (avoid speculative generality).

## Decisions

### D1: Two modules in `otio_sync_core`, layered

`coords.py` (host-neutral geometry) and `rv_annotation_codec.py` (structure + RV units) both live in `otio_sync_core`, beside `xs_annotation_codec.py`. The codec imports `coords`; nothing imports the codec except RV call sites. Both are already importable everywhere (`otio_sync_core` is on RV's `PYTHONPATH` and imported by both xStudio plugins).

**Scope boundary — `coords` is host-neutral, not RV-flavored.** `coords` owns only the OTIO-normalized geometry that *every* host and the pixel/testchart ground truth share. Host-specific *unit* conversions live in that host's codec — exactly as `xs_annotation_codec` already keeps xStudio's `font_size * 2.5` factor inline. So RV's font/width scaling (`5000.0`, `0.6`) lives in `rv_annotation_codec`, **not** in `coords`. This keeps the `coords` name accurate (xStudio depends on it too) and makes the two codecs symmetric — each owns its host's units.

`coords.py` (host-neutral geometry only) exports:
```python
DEFAULT_ASPECT_HALF: float = 8 / 9      # 1920×1080 fallback
DEFAULT_SPACING: float = 0.8            # RV-neutral spacing (xs has none)
DEFAULT_FONT_SIZE: float = 50.0

def aspect_half(width: int, height: int) -> float: ...   # W/(2H), guard H>0
def px_to_otio(px, py, W, H) -> tuple[float, float]: ... # pixel → H-norm, Y-up
def otio_to_px(x, y, W, H) -> tuple[float, float]: ...   # H-norm → pixel
```

`rv_annotation_codec.py` owns RV's unit conversions (parallel to xStudio's inline `2.5`):
```python
RV_FONT_SCALE: float = 5000.0           # OTIO font_size → RV .size property
RV_WIDTH_SCALE: float = 0.6             # SyncEvent width → RV pen .width

def font_size_to_rv(font_size: float) -> float: ...      # / RV_FONT_SCALE
def rv_to_font_size(rv_size: float) -> float: ...        # * RV_FONT_SCALE
```

### D2: The `PaintNodeSpec` IR (design A — pure IR + thin applier)

`sync_events_to_rv_specs` returns an ordered list of pure dicts. Each spec fully describes one RV paint child node without touching `rv.commands`:

```python
PaintNodeSpec = {
  "kind":  "pen" | "erase" | "text" | "ellipse" | "rect" | "arrow",
  "uuid":  str,                     # stable id for reconcile/dedupe
  "props": [                        # ordered; RV type is explicit
      ("brush",  "string", ["gauss"]),
      ("color",  "float",  [r, g, b, a]),
      ("splat",  "int",    [1]),
      ("width",  "float",  [...]),
      ("points", "float",  [...]),
      # ...
  ],
}
```

Property sets per kind are the **superset already present in `batch_openrv_helper.py`** (the richest copy), enumerated authoritatively in the spec:
- **pen/erase** — `brush, color, debug, join, cap, splat, [mode=1 if erase], width, points`
- **text** — `position, color, spacing, size, font, text, scale, rotation, origin, debug, startFrame, duration, mode, uuid, softDeleted`
- **ellipse/rect** — `min, max, borderColor, innerColor, borderWidth, startFrame, duration, eye, uuid, softDeleted`
- **arrow** — `startPos, endPos, borderColor, innerColor, borderWidth, thickness, startFrame, duration, eye, uuid, softDeleted`

The spec carries the node *kind* and *uuid*; it does **not** carry the final RV node name (`{rv_node}.pen:{id}:{frame}:{user}`) or the `frame_node.order` list. Those depend on the target paint node, the assigned strokeid, and the frame — runtime concerns owned by the applier (D3). The IR is thus position/site-independent and testable in isolation.

Rejected alternative (design B — inject `commands` into the codec): keeps one source of truth but makes the whole codec untestable without a fake `commands` and blurs the pure/impure line. A is chosen for symmetry with `xs_annotation_codec` (pure core, host does the write) and unit-testability.

### D3: `apply_specs(specs, commands, *, rv_node, frame, user, mode)` — the only RV-touching function

Small (~40 lines). Iterates specs, allocates strokeids, builds node names, calls `newProperty`/`set*Property`, and maintains the per-frame `order` list. Two modes cover all four call sites:

- `mode="append"` — batch import / plugin import / `_apply_annotation_render`: create fresh nodes, append to `order`. Idempotent re-render deletes+recreates the frame `order` (as the real batch helper already does).
- `mode="reconcile"` — live `_apply_annotation_replace` / `_partial`: match existing nodes by `uuid` within `order`; update-in-place when found, add when not; prune managed nodes whose uuid is absent from the incoming set. This preserves the exact behavior at `annotation_sync.py:664-759`.

`apply_specs` is the single place `rv.commands` is imported for writing. The `set_prop(node, name, type, val, dim)` helper duplicated in the batch helper and plugins collapses into it.

### D4: `scale` reconciliation (resolves prior proposal↔design contradiction)

- **xStudio → OTIO**: xStudio captions have no scale field ⇒ `scale=1.0` is emitted and is correct. No change. (This is the old D4, retained.)
- **RV round-trip**: the RV text node *has* a `scale` property; `export_annotations` reads it and `import` writes it. The codec preserves `scale` through `sync_events_to_rv_specs` / `rv_specs_to_sync_events`. The "silent scale drop" the old proposal called BREAKING existed only in inline copies that diverged; centralizing removes it with **no** user-facing breaking behavior. The spec's `TextAnnotation` requirement states: `scale` round-trips wherever the host has a scale concept (RV), and defaults to `1.0` where it does not (xStudio).

### D5: `schema_name()` matching, not `isinstance`

The codec dispatches on `ev.schema_name()` (`"PaintStart"`, `"PaintPoint"`/`"PaintPoints"`, `"PaintEnd"`, `"TextAnnotation"`, `"EllipseAnnotation"`, `"RectangleAnnotation"`, `"ArrowAnnotation"`), never `isinstance(ev, otio.schemadef.SyncEvent.X)`. Per the known schemadef double-load defect, `isinstance` silently returns `False` when the schemadef is registered twice, dropping annotations. The batch helper already does this; the plugin (copies 2 & 4) does not — migrating them onto the codec fixes the bug.

### D6: Spacing default `0.8`; height-normalized coordinate comment fixed

Retained from the prior draft. `xs_captions_to_sync_events` emits `coords.DEFAULT_SPACING` (0.8, RV-neutral) instead of `0.0` (which collapses letter spacing in RV). The stale "RV normalises by image width" comment in `generate_testchart.py` is corrected — the formula divides by **height** (`x ∈ [−W/(2H), +W/(2H)]`), which is correct.

### D7: Text comparison via per-label bounding-box colour match

Retained from the prior draft. Sample a ~20×20 px window centred on each expected pixel position (via `coords.otio_to_px` from the TextAnnotation position), match dominant colour to annotation colour, report centroid offset. Pass/fail at ±5 px anchor tolerance (font rendering varies across platforms); coarser than stroke arch-profiling by design.

### D8: Hub-and-spoke around OTIO SyncEvent — adding a host is one new spoke

OTIO SyncEvent is the interlingua. Every host codec is a spoke that converts only between its host-native representation and SyncEvent; **no host-to-host adapters exist**. This keeps integration cost linear (N hosts = N codecs) instead of quadratic (N² pairwise converters), and means a new host — Nuke Studio, or another paint tool layered on RV — is added by writing one new codec module against the D9 contract, touching neither existing codecs nor `coords`.

```
   xStudio ─┐                     ┌─ RV (PaintNodeSpec IR + applier)
            ├──►  SyncEvent  ◄────┤
  Nuke ─────┘   (hub / interlingua)  └─ future RV-layered tool
            each spoke: host-native ⇄ SyncEvent, uses coords for geometry
```

A tool *layered on RV* is a special case: it may reuse RV's `apply_specs` and `PaintNodeSpec` IR wholesale and only differ in how it discovers the target paint node — so the RV codec's IR/applier split (D2/D3) is the reuse seam for that family, not just an RV internal detail.

### D9: A lightweight common codec contract

Each host codec conforms to a small, uniform surface so host-agnostic tooling (batch harnesses, `compare_*`, a future dispatch registry) works without special-casing:

```python
# every codec module provides:
HOST_ID: str                                    # "rv", "xstudio", "nuke", ...
SUPPORTED_KINDS: frozenset[str]                 # native SyncEvent kinds (see D10)

def to_sync_events(native, ctx) -> list[SyncEvent]: ...     # host → hub
def from_sync_events(events, ctx) -> NativeIR: ...          # hub → host IR

# imperative-write hosts (RV) additionally expose:
def apply(native_ir, host_handle, *, mode) -> None: ...     # IR → SDK writes
# single-handoff hosts (xStudio: bookmark.set_annotation) omit apply().
```

This is a **convention/`typing.Protocol`, not a base class or plugin framework** — two hosts don't justify machinery, and D8 keeps them independent. Existing `xs_*` / `rv_*` function names stay (no churn); the contract entry points are thin, consistently-named wrappers. The `ctx` object carries per-media resolution (for `coords.aspect_half`), frame, and user — the same data all codecs already need.

### D10: Shared shape tessellation + per-host capability declaration (graceful degradation)

Hosts differ in native capability: RV renders ellipse/rect/arrow as first-class paint nodes; xStudio has no shape primitives and **tessellates them into stroke polylines**. That tessellation (shape geometry → point list, `xs_annotation_codec.py:227-284`) is host-neutral geometry, not xStudio-specific — a future host lacking native shapes would otherwise re-derive it. It moves into a shared helper (`otio_sync_core.shapes`, or a `coords` submodule) consumed by any codec whose `SUPPORTED_KINDS` excludes shapes.

Each codec declares `SUPPORTED_KINDS`; `from_sync_events` routes any kind **not** in that set through the shared tessellation fallback before handing off. So a new host gets shapes for free (as strokes) on day one and can later promote specific kinds to native rendering by adding them to `SUPPORTED_KINDS`. This makes "new host, partial capability" a first-class, low-effort path rather than a rewrite.

### D11: `hold`/`ghost`/`ghost_before`/`ghost_after` removed from the sync path (decided during Group 7 implementation)

These four `PaintStart` fields (RV-native "hold frame" / onion-skin display toggles) were previously read from live RV pen properties into outbound broadcasts, and read from inbound SyncEvents back into RV pen properties — round-tripping through the sync schema despite having no cross-host meaning (xStudio has no equivalent concept). Per explicit user direction, they are legacy and should not be supported in the sync schema at all; they are local RV display properties, not synced data.

Applied narrowly, without touching the shared `SyncEvent.py` schemadef (out of scope — a separate, more invasive decision with cross-repo compatibility implications):
- **Send** (`_construct_annotation_events`): never reads these off the RV node into the broadcast event.
- **Receive** (`_apply_annotation`): still creates `.hold`/`.ghost`/`.ghostBefore`/`.ghostAfter` on new pen nodes (RV's own paint tooling may expect them to exist), but always writes the fixed default `0` — never derived from network data.

This is a behavior change (previously a peer's local hold/ghost state could leak into a received stroke's initial values); accepted because that leakage was never a deliberate feature and the fields are now explicitly out of the schema's scope.

## Risks / Trade-offs

- **Live renderer is the riskiest migration** (1514 lines, real-time, UUID reconcile). Mitigation: the `mode="reconcile"` applier is written to reproduce the existing `annotation_sync.py:664-759` semantics exactly; validate against a live two-host session before archiving.
- **IR completeness** — if any site needs a property not in the enumerated superset, it would reach around the codec. Mitigation: D2 sourced the superset from all four copies (batch + live); the spec enumerates every prop, and `apply_specs` rejects unknown kinds loudly.
- **Spacing change is visible** on xs-originated captions rendered in RV (0.0 → 0.8). Previous value was wrong (illegible). Document in release notes.
- **Text comparison is platform-sensitive** (±1–2 px font variance). Mitigation: ±5 px threshold, references generated on the same platform.

## Migration Plan

Strictly bottom-up so each layer is testable before the next consumes it:

1. `coords.py` — no callers, zero risk.
2. `shapes.py` — extract shape tessellation from `xs_annotation_codec.py:227-284` into the shared helper; re-point the xStudio codec at it (behavior-preserving, unit-tested against the current point output).
3. `rv_annotation_codec.py` pure functions (`sync_events_to_rv_specs`, `rv_specs_to_sync_events`) + the D9 contract entry points + `SUPPORTED_KINDS` — unit-test against the IR **outside RV**.
4. `apply_specs` (append + reconcile) — the thin RV edge.
5. Migrate `batch_openrv_helper.py` → codec; run testchart batch, confirm `compare_testchart` + `compare_thickness` PASS (baseline parity gate before touching plugins).
6. Migrate `ori_annotations_plugin.py` import **and** export → codec.
7. Migrate `rvplugin/ori_sync/annotation_sync.py` (render/replace/partial/import) → codec; validate live two-host session.
8. Wire `xs_annotation_codec.py` + `xstudio_plugin/ori_annotations/ori_annotations.py` + `generate_testchart.py` to `coords`; add xStudio's `SUPPORTED_KINDS` + contract entry points (retrofit to prove the D9 surface holds for a single-handoff host).
9. Add text comparison to `compare_testchart.py`.
10. Full testchart batch (RV + xStudio) — all comparisons PASS.

No data migration; all changes are Python source. Rollback = revert per-site.

## Open Questions

- Should `apply_specs` own strokeid allocation, or should callers pass a starting id? (Leaning: applier owns it via the paint node's `nextId`, matching current behavior.)
- Does the live `_apply_partial_annotation` (incremental in-progress stroke) fit the same IR, or does it need a partial-stroke variant of the pen spec? (Investigate during step 7; may add a `partial=True` flag on pen specs.)
- Deferred to the third host (not this change): promote the flat `otio_sync_core/*_annotation_codec.py` files into a `codecs/` subpackage + a `HOST_ID`→module dispatch registry. Two hosts don't warrant it; revisit when a Nuke/other codec is actually added.
- `coords.otio_to_px` return type — float (precise) vs rounded int (comparison rounds anyway). Leaning float.
