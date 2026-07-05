## Context

Researched both apps' capture mechanisms before writing this:

- **xStudio** exposes `xstudio.api.intrinsic.viewport.OffscreenViewport.render_bookmark_with_transparency(output_path, bookmark_id, include_image=True, include_drawings=True, width=-1, height=-1)` — a single remote call (`render_viewport_to_image_atom`) that composites video + annotation drawings into one output file. `width=-1`/`height=-1` means "use on-screen image format"; explicit values are supported. This is already used internally (with `include_image=False`) by the `ori_annotations` plugin's own export feature (`xstudio-annotation-export`'s "Optional Annotation Image Export" requirement) — a different caller, unaffected by this change.
- **OpenRV**'s testchart pipeline renders via external `rvio` against a *saved session file*, with a GUI-grab fallback (`testchart/grab_frame.py`: `rv.commands.sessionGLView()` → wrap as a Qt widget → `.grab().save(path)`) for when `rvio` crashes (a known issue in this build, per project history). For a *live, already-running* sync_test instance, the in-process Qt grab is the better primary path — no save/reload round-trip, no external-process reliability risk.

## Goals / Non-Goals

**Goals:**
- Add a `capture_frame` action to both hooks that renders the live instance's current frame (video + annotations) to a PNG.
- Add a comparison helper that checks a captured frame's annotation geometry/thickness against the test's *own* known ground truth (the `draw_annotation` payload the test itself sent) — not a hardcoded reference chart.
- Make this an optional, additive check layered on top of the existing numeric round-trip check (`annotation_geometry`), not a replacement — the numeric check is fast and always available; the visual check is the stronger, slower net that would have caught the 2x rect bug automatically.

**Non-Goals:**
- Pixel-perfect / anti-aliasing-level image diffing — this measures a cross-section thickness/centroid the same way `compare_testchart.py` does, not a full image comparison.
- Replacing `testchart/`'s own reference-chart comparison workflow (`compare_testchart.py`, `compare_thickness.py`) — those stay as-is for their own (forward-direction, synthetic-chart) purpose; this is a different, live/reverse-direction check.
- Rewriting `xstudio-annotation-export`'s own image-export feature — `batch_xstudio.py`'s switch to the direct API is an implementation-only cleanup of a *different* caller.

## Decisions

### D1: xStudio capture via `render_bookmark_with_transparency`

The `capture_frame` action needs a bookmark id for the target media/frame. Reuse the existing bookmark-enumeration logic already in `xstudio_hook.py`'s state getter (`session.bookmarks.bookmarks`, matched by media/frame) rather than writing a new lookup. Call with explicit `width`/`height` (not `-1`/`-1`) so the output resolution is a known, comparable quantity across both apps — see D2's note on why this matters.

### D2: OpenRV capture via in-process Qt grab — open question on matching resolution

`view.grab()` captures the GL viewport widget at whatever pixel size it currently occupies on screen, which is **not guaranteed to equal the media's native resolution or xStudio's chosen render size** (window size, viewport fit mode, HiDPI scaling all affect it). Two options, to be resolved during implementation by checking what RV's command API actually offers:
1. Force a known window/viewport size before grabbing (if RV exposes a resize/reformat command), matching xStudio's explicit `width`/`height`.
2. Don't assume a fixed size at all — read the *actual* saved PNG's dimensions back (e.g. via `PIL.Image.open(path).size`) and project expected pixel coordinates using *that* image's real resolution, independently per captured image. This is more robust (no dependency on controlling RV's window precisely) and is the fallback if (1) isn't available.

Prefer (2) as the default unless (1) turns out to be trivial — it avoids a fragile dependency on RV window/viewport control that the sync_test harness doesn't currently exercise anywhere else.

### D3: Output path convention

Save captures into the test's existing per-test `logs_dir` (already established by `AppSpawner`), named `capture_<app_name>_<port>_<frame>.png` — consistent with existing artifacts saved there (`openrv_<port>.rv`, `xstudio_<port>.xst`).

### D4: Comparison reuses `coords.otio_to_px` + `compare_testchart.py`'s cross-section technique, driven by test-owned ground truth

Given a captured image and the *same* geometry the test told the driver app to draw (already known — it's the `draw_annotation` payload), project that OTIO-normalized geometry into pixel space via `coords.otio_to_px` against the *captured image's own actual resolution* (per D2), then sample a perpendicular cross-section across the expected border/line location and locate the annotation-colored centroid — the same technique `compare_testchart.py` already uses for its hardcoded reference geometry, just parameterized by the test's own known geometry instead. Reuse `compare_testchart.py`'s cross-section/centroid helper functions directly (import, don't reimplement) if their signatures allow a non-hardcoded geometry list to be passed in; otherwise extract the reusable core into a shared helper both scripts import.

### D5: `testchart/batch_xstudio.py` calls `render_bookmark_with_transparency` directly

Replace the `plugin.export_annotations(..., include_images=True)` + external-PIL-subprocess-compositing sequence with a direct `OffscreenViewport(conn).render_bookmark_with_transparency(output_path, bookmark_id, include_image=True, include_drawings=True)` call per annotated bookmark. Same output image *kind* (video + annotations composited), produced in one call instead of two steps — implementation-only, not a behavior change visible to `compare_testchart.py`/`compare_thickness.py`, which just consume the resulting PNGs.

## Risks / Trade-offs

- **[PIL/numpy availability in the harness's Python]** → Confirmed during earlier work this session: the repo's `.venv` lacks `PIL`, but the system/homebrew `python3.10`/`python3.11` (used elsewhere for `testchart/compare_thickness.py`-style scripts) has it. The comparison helper needs to run under whichever interpreter has `PIL`+`numpy` available — likely meaning it runs as a step invoked from `runner.py` using the same interpreter resolution pattern `testchart/`'s scripts already use, not assumed to be available in every Python that might import `sync_test`.
- **[RV viewport resolution not directly controllable]** → See D2; mitigated by projecting against the captured image's own actual dimensions rather than an assumed fixed size.
- **[Capture timing]** → the live app needs to have actually rendered the frame with the annotation applied before capture — needs a short settle/redraw wait (`rv.commands.redraw()` + a brief pause for RV; xStudio's render call is presumably synchronous/on-demand and doesn't need one, but this should be confirmed rather than assumed during implementation).
- **[Scope creep risk]** → this is real new infrastructure, not a small addition; kept as its own change (per the user's request) rather than folded into `fix-rv-shape-border-width`, so that change's fix isn't gated on this one landing.
