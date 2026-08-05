## Context

Two prior investigation sessions (2026-07-10/11, see project memory `project_pen_pressure_sync_investigation.md`) established:
- The RV-mints-a-new-uuid-per-partial bug is fixed and confirmed (stroke count stays at 1 across partial ticks instead of climbing to 20).
- Despite that fix, xStudio still renders a synced pen stroke at visually constant width.
- The wire payload (`PaintPoint.points.size`) is confirmed varying (~78x range across a stroke) via raw JSON in `rv_client.log`/`xstudio_client.log`.
- Locally-drawn xStudio strokes render pressure correctly, which rules out the shared renderer (`opengl_stroke_renderer.cpp`, `stroke.cpp`, the GLSL shader) — that path is exercised by both local and synced strokes and only local ones show correct taper... actually both use the same renderer, so the fault must be upstream of the C++ `Stroke` object, in how the Python sync layer builds the `pen_strokes` dict handed to `bm.set_annotation()`.
- Static review of `xs_annotation_codec.py::sync_events_to_xs_strokes` did not find an obvious bug: `thickness = max(sizes)/(2*aspect_half)`, `size_pressure = sizes[idx]/max(sizes)` is correct algebra on paper.

This leaves two candidate fault locations that only runtime data can distinguish:
1. The codec itself computes a flattened `size_pressure` despite correct-looking source (something not visible from reading the code alone — e.g. wrong `cmd` in scope, an early-exit, wrong array being read).
2. The codec's output is correct, but something between codec output and the `bm.set_annotation()` call (the throttled live-partial cache/merge logic in `annotation_sync.py`) discards or overwrites the pressure variation before it reaches the bookmark.

## Goals / Non-Goals

**Goals:**
- Add minimal, targeted logging that distinguishes "codec computed flat data" from "codec computed correct data but something downstream flattened it before `bm.set_annotation()`".
- Keep the change small enough to add, test, and read the resulting logs in one sitting.

**Non-Goals:**
- Fixing the actual flattening bug — that's a follow-up change once the logs identify the fault location.
- Any change to sync protocol, wire format, or rendering behavior.
- Permanent/production-grade logging infrastructure — this is throwaway diagnostic instrumentation, expected to be removed or left inert once the root cause is found.

## Decisions

- **Log at two boundaries, not inline everywhere**: (a) end of `sync_events_to_xs_strokes`'s per-stroke construction, and (b) immediately before each `bm.set_annotation()` call site. This brackets the only code that's unique to the sync path (vs. local drawing), which is exactly where the bug must be per the Context section. Logging inside the shared renderer/`Stroke` class was considered and rejected — that path is proven fine by local-draw behavior, so instrumenting it would just add noise.
- **Log thickness + min/max of size_pressure, not the full per-point array**: full arrays are already visible in the raw wire JSON when needed; min/max is enough to answer "is this stroke still varying by the time it reaches call site X" without flooding the log during a fast-moving live-partial stream.
- **Use the existing `_log()` helper**: both files already have a gated debug logger (`_log`) used throughout; no new logging mechanism needed. This keeps the diagnostic lines consistent with existing log format and easy to grep/remove later.

## Risks / Trade-offs

- [Live-partial updates fire frequently (throttled to ~10fps) → verbose log output during a long draw] → Acceptable for a short diagnostic session; min/max-only (not full arrays) keeps each line short.
- [Logging alone may not localize the bug if the issue is in a third location not covered by these two boundaries, e.g. inside `bm.set_annotation`'s JSON marshalling itself] → Already partially investigated (read `bookmark.py` — it passes `points` through unmodified); if both log points show correct data reaching `bm.set_annotation()`, next step is reading the C++ deserialization/native canvas update path with the same before/after mindset.

## Migration Plan

Not applicable — temporary diagnostic logging, no data migration or rollback concerns. Log lines can be deleted once the root cause is found, or left in place if judged useful for future debugging (gated by the existing `_log` debug flag, so no runtime cost in normal operation).

## Open Questions

- None blocking — ready to implement and run the next test session.
