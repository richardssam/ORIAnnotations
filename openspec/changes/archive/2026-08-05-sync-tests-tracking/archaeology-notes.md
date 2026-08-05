## Orphaned `sync_test/logs/` archaeology — resolution

Per tasks.md 8.1, only directories whose recording is still referenced in
`sync_tests.yaml` needed review. Checked all 11 remaining directories
(`late_join_repro`, `late_join_repro_xs`, `manual_xs_pen_debug`,
`manual_rv_capture_test`, `calibrate_text_scale`, `reorder`,
`text_annotations`, `missing_media`, `delete_media`,
`verify_pen_replay_noregress`, `verify_text_replay_fix`) two ways:

1. Grepped every log file in each directory for a `recordings/*.jsonl` path
   — none found one, in any of the 11 (these all predate the `runner.log`
   mirroring feature, so there's no structured trace of what config drove
   them).
2. Cross-referenced by name/vintage against `recordings/` and current
   `sync_tests.yaml`: `reorder`, `text_annotations`, `missing_media`, and
   `delete_media` all have same-named `.jsonl` files still sitting in
   `recordings/`, but `sync_tests.yaml`'s active entries reference the
   newer `*_v2`/`*_notc` recordings instead (`reorder_media_v2.jsonl`,
   `text_annotations_notc.jsonl`, etc.) — none of the old names are
   referenced by any active (uncommented) entry.

**Resolution: none of the 11 need promotion.** No directory's recording is
currently referenced in `sync_tests.yaml`, so per the 8.1 criterion none
require a described entry. Directory-by-directory:

- `reorder`, `text_annotations`, `missing_media`, `delete_media` (all
  2026-06-02–06-04): superseded by `*_v2`/`*_notc` recordings already
  covered by active, now-described entries (`reorder_media`,
  `text_annotations_notc`, `delete_media_openrv_noscript`/
  `delete_media_openrv`/`delete_media_xstudio`). `missing_media` also still
  exists as a commented-out entry in `sync_tests.yaml` — left commented,
  not resurrected, since resurrecting it is a scope decision beyond this
  change.
- `manual_rv_capture_test`, `manual_xs_pen_debug` (2026-07-05): one-off
  manual debug runs (not driven by `sync_tests.yaml` at all — no
  `runner.log`, no recording reference), from the early stage of the
  pen-pressure investigation described in memory
  `project_pen_pressure_sync_investigation`.
- `calibrate_text_scale` (2026-07-08): matches the archived change
  `2026-08-04-sync-test-text-annotation-scale`'s investigation into the
  fontSize/QPainter ~10x text-size mismatch (see memory
  `project_rv_fontsize_qpainter_migration`). That investigation's outcome
  is already permanently captured as real test coverage —
  `openrv_draws_text_xstudio_verifies` / `openrv_draws_legacy_text_xstudio_verifies`
  in `sync_tests.yaml` — so this scratch directory is superseded, not lost
  knowledge.
- `late_join_repro`, `late_join_repro_xs` (2026-07-11, 14:30–14:35): repro
  scripts for a late-join reconciliation issue, same day as (and preceding)
  the verify_fix* sequence below.
- `verify_pen_replay_noregress`, `verify_text_replay_fix` (2026-07-11,
  16:19–16:51, alongside the no-longer-present `verify_fix`/`verify_fix2`/
  `verify_fix3` — excluded from this list per the narrowed 8.1 criterion):
  same day as commit `58a389b` "Fix RV pen-annotation reconcile pruning and
  node-resolution mismatch" — sequential ad hoc verification runs against
  that fix, matching memory `project_pen_pressure_sync_investigation`'s
  2026-07-11 timestamp. The fix itself landed in that commit; these were
  throwaway verification runs, not a permanent test that was ever
  intended to persist.

No directories were deleted — they're already gitignored (`sync_test/logs`)
and harmless to leave for now; deleting local files nobody asked to have
removed felt like the wrong default. This note is the recoverable record
tasks.md 8.3 asked for.
