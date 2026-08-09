## 1. Remote-apply provenance (observation only — D2)

- [x] 1.1 Add a remote-apply provenance span to `manager.apply_patch`
      (`python/otio_sync_core/manager.py:2410`): record the source peer and a
      monotonic start/end for the duration of each remote apply, plus a settle
      window after it. Expose a read-only query (e.g. `remote_apply_context()`)
      returning the peer and age, or `None` outside a window. Additive — no
      behaviour change, no wire change.
- [x] 1.2 Unit-test the span in `tests/otio_sync/`: inside an apply it reports
      the source peer; after the settle window it reports `None`; nested/
      overlapping applies do not leave a window permanently open.
- [x] 1.3 In xStudio's selection reaction
      (`xstudio_plugin/ori_sync/playback_sync.py`, the `show_atom` /
      `source_atom` paths around lines 348 and 624), log for every resolved
      event whether it landed inside a provenance window and which peer opened
      it. Log only — no suppression yet.
- [x] 1.4 Log the same for the `Pinned Source Mode` transition
      (`xstudio_plugin/ori_sync/playback_sync.py:1111`), since PSM `True→False`
      is what marked the host entering single-clip mode in the soak.

## 2. OpenRV records a view outcome for every path (D6)

- [x] 2.1 Widen `mirror_failure` in `rvplugin/ori_sync/playback_sync.py:75-95`
      from a bare string to a record carrying outcome, reason, and the view it
      refers to. Keep `_report_mirror_failure` working for existing callers.
- [x] 2.2 Give `_apply_playback`'s view block (~lines 194-226) exactly the four
      outcomes from design D6 — `adopted`, `already-displayed`, `declined`,
      `failed` — with every exit recording one. `declined` must cover the
      deliberately-unactioned sequence-mode `clip_guid` case (~line 204) with
      its reason.
- [x] 2.3 Clear the record only on `adopted` / `already-displayed`; leave
      `declined` / `failed` standing until the next successful adoption.
- [x] 2.4 Export the record through `python/otio_sync_core/inspection.py` and
      `sync_test/python/sync_test/openrv_hook.py` (`view_mirror_error`, line
      63) so the outcome and reason are readable without application logs.
- [x] 2.5 Extend `sync_test/python/sync_test/runner.py`'s `VIEW_MIRROR_FAILED`
      (line 242) handling to distinguish `declined` from `failed`.
- [x] 2.6 Add `otio_sync_core` module changes to `makepackage.csh`'s vendor list
      if any new module was created, then run
      `rvplugin/ori_sync/reinstall.csh` — RV loads the installed rvpkg copy,
      not the repo source.

## 3. Reproduce both defects with 1 and 2 in place (D5)

- [x] 3.0 Unblock the follower's `ADD_TIMELINE`. Three soaks produced zero of
      them: `broadcast_add_timeline` returned `SUPPRESSED` with no log whenever
      the peer lacked the structure lease, and ownership enforcement defaults on
      as of `82c0a16` — which postdates the 2026-08-06 evidence. Fixed by
      exempting `broadcast_clip_timeline` from the lease (a derived, idempotent
      announcement, not a structural mutation) and logging every suppression.
      Claiming the lease instead would not have worked: `_apply_claim` queues
      behind a *confirmed* owner. Also fixes annotation binding for a peer that
      does not hold the lease.
- [ ] 3.1 Run a two-app soak (xStudio host + OpenRV follower) repeating the
      2026-08-06 sequence: follower isolates two clips locally, host stays in
      sequence view. Capture both logs.
- [ ] 3.2 From the provenance logging (1.3/1.4), name the actual route from the
      follower's `ADD_TIMELINE` to the host's `source_atom` and PSM flip.
      Record it in `evidence.md` as observed, not inferred.
- [ ] 3.3 Confirm or refute defect 2's inferred mechanism. Design D5 argues
      `mode_changed` was `True` at `20:38:26.899`, so a silent no-op inside
      `_switch_to_sequence_view` is a live alternative. Record which branch the
      2.2 outcome names.
- [ ] 3.4 Measure the observed delay between the remote apply and the host's
      display change across the soak; use it to size the settle window in 1.1
      (design Open Question 2).
- [ ] 3.5 Sample free memory and swap alongside the soak — swap-induced latency
      has previously mimicked timing races here.

## 4. OpenRV compares against the displayed view (D4)

- [x] 4.1 Add a helper in `rvplugin/ori_sync/playback_sync.py` that reads the
      displayed view from `rv.commands.viewNode()` and resolves it to
      (view_mode, clip_guid, timeline_guid) via
      `sequence._rv_node_to_timeline_guid`, `sequence._otio_guid_to_root`, and
      the source-group maps — the reverse of what `_switch_to_sequence_view` /
      `_switch_to_source_view` already do.
- [x] 4.2 Replace the `_last_applied_*` comparison at lines 194-197 with a
      comparison against 4.1's result, and delete
      `_last_applied_view_mode` / `_last_applied_clip_guid` /
      `_last_applied_tl_guid` (lines 42-44, 219-221).
- [x] 4.3 Leave `_cur_view_mode` / `_cur_clip_guid` in place, scoped to
      composing broadcast payloads only — they are a cache and must not become
      the adoption comparison (design D4).
- [x] 4.4 Fix whatever 3.3 named. Observed 2026-08-08 21:43:47: neither the
      proposal's inferred mechanism nor a silent no-op in
      `_switch_to_sequence_view`. A switch that *failed* was still written to
      `_last_applied_*`, so the identical instruction 200 ms later reported
      `already-displayed`, cleared the standing failure, and became a permanent
      no-op. Closed by 4.2 — `_verified_view` is written only on
      adopted/already-displayed and cleared on declined/failed. Falsified
      against the pre-fix write before landing.
- [x] 4.5 Unit-test both spec scenarios: a locally isolated clip does not block
      a later sequence instruction; an instruction matching the displayed view
      is still a no-op.
- [x] 4.6 Run `rvplugin/ori_sync/reinstall.csh` before any in-RV verification.

## 5. The host does not change what it displays because of a peer (D3, D1)

- [ ] 5.1 At the site named by 3.2, classify a display change occurring inside
      a provenance window as `remote-induced`, using 1.1's query. Keep this
      distinct from `_selection_broadcast_suppress_until` (own-echo) and
      `_applied_clip_echo_guid` (delayed own-echo) — same shape, opposite
      remedy; they may share a helper but not a variable.
- [ ] 5.2 If 3.2 shows the route can be prevented outright, prevent it. Only if
      it cannot, revert the host to the view it held before the remote message
      arrived. Log every revert with the peer that opened the window.
- [ ] 5.3 Ensure the host still broadcasts its own genuine visibility changes
      unchanged — the guard must key on provenance, not on message shape
      (design D3, rejected alternative).
- [ ] 5.4 Add a test in `tests/otio_sync/` (alongside
      `test_broadcast_authority.py`) that a remote structural message applied
      on the host leaves the host's displayed view unchanged and emits no
      visibility broadcast attributable to it.

## 6. Suite coverage for a follower that changes its own view (D7)

- [ ] 6.1 Add a `sync_test` scenario where the follower isolates a clip
      locally — no existing scenario does this, which is why eleven runs missed
      both defects.
- [ ] 6.2 Assert the host's displayed clip is unchanged after 6.1, and that the
      host emitted no visibility broadcast caused by it.
- [ ] 6.3 Assert that a subsequent host sequence instruction is adopted by the
      diverged follower, and that its recorded outcome is `adopted` — not
      `already-displayed`.
- [ ] 6.4 Run the suite and confirm both new assertions fail against the
      pre-fix code (revert 4 and 5 locally, or run on a stashed tree) — a test
      that has never failed has not been shown to cover the defect.

## 7. Close out

- [ ] 7.1 Re-run the soak from 3.1 against the fixed build; confirm zero
      remote-induced host display changes and that a diverged follower is
      recoverable.
- [ ] 7.2 Update `docs/visibility_authority_guards.md`: record that the bypass
      is closed, and that the §5.1 guard-deletion decision now needs a fresh
      soak taken after this change — the zero-fire counts from 2026-08-06 are
      not evidence.
- [ ] 7.3 Unblock `openspec/changes/host-owned-visibility` §5.1 with a pointer
      to 7.1's soak as the evidence it must be re-decided on.
- [ ] 7.4 Update `evidence.md` with the confirmed mechanisms from 3.2 and 3.3,
      keeping the original inferred reading marked as superseded rather than
      deleting it.
