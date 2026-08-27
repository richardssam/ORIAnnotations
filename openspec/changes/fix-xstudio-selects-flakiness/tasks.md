## 1. Baseline

- [x] 1.1 Record a pre-fix baseline: run `xstudio_selects_script_xstudio` 10x, logging pass/fail and, for each failure, whether `POLL-SLOW cmd:resolve_selection` and `discarding position N` (non-zero N) appear. Expected ~5/10 failing, all with the signature.
      → Batch A: 5/10 failed, all `checkpoint_timeout`, all with `POLL-SLOW cmd:resolve_selection took 2.1–2.2s` + non-zero `discarding position`. Batch B (6 runs): 1 failure, same signature. Logs archived per failure.
- [x] 1.2 Confirm the two scoped-out failure kinds (`playback still active`, `structural_consensus`) do not appear in the baseline batch, so any later appearance is attributable.
      → Zero occurrences of either across all 16 measured runs.

## 2. Cheap playhead identity (design Decision 2)

- [x] 2.1 Add a handle-only resolver alongside `_viewport_playhead()` that returns the viewport's playhead actor handle without constructing the `Playhead` wrapper, reusing the handle already computed inside `_viewport_playhead()`.
- [x] 2.2 Ensure the handle's identity key is normalised the same way `_remote_key` normalises a wrapper's, so unchanged playheads never read as changed (see design Risks — re-adoption churn).
- [x] 2.3 Rewrite `check_and_update_active_playhead` to compare handles and construct the wrapper only on the path that actually adopts.
- [x] 2.4 Hoist the `b73b60c` dedup guard in the `_carry_over_playback_mode` call site above the wrapper construction: resolve the handle, apply the (key, time) guard, and construct the wrapper only if the guard passes.
- [x] 2.5 Verify the remaining `_viewport_playhead()` call sites (PSM block, `broadcast_view_state` position/loop write, `_reacquire_active_playhead`) still receive a real wrapper, since they read or write attributes.

## 3. Isolation must not discard a newer position (design Decision 1) — REVERTED

Implemented, measured to never fire, and reverted. Kept as a record so it is not
re-attempted the same way. See 5.5 for the evidence.

- [~] 3.1 Compare the selection timestamp against `_last_position_change_at` to gate the frame-0 reset. → **Reverted.** The precondition never holds: the plugin does not observe a seek that lands on a not-yet-adopted playhead, so `_last_position_change_at` is never updated and the gate cannot fire.
- [~] 3.2 Skip the `ph.position = 0` write when a newer position exists. → Reverted with 3.1.
- [~] 3.3 Preserve the moved-to == announced invariant in both branches. → Reverted with 3.1 (single branch again).
- [~] 3.4 Keep Loop-mode application unconditional. → Unchanged from before this change; nothing to do once 3.2 reverted.
- [x] 3.5 Record why in the code. → `_new_source_clip` now carries a KNOWN GAP note stating the gap, the measurement, and why the timestamp comparison alone must not be re-attempted.

## 4. Tests

- [~] 4.1 Unit test: an isolation with a later position keeps and broadcasts it. → Removed with §3.
- [~] 4.2 Companion: no intervening change still resets to 0. → Removed with §3.
- [x] 4.3 Unit test that identity-only playhead checks construct no wrapper, so the cost fix cannot silently regress. → `tests/xstudio_plugin/test_playhead_identity_cost.py` (3 tests).
- [x] 4.4 Run `./run_tests_xstudio.sh` and confirm no regressions. → 133 passed.

## 5. Verification

- [x] 5.1 Re-run `xstudio_selects_script_xstudio` 10x; confirm zero failures carrying the `POLL-SLOW` + `discarding position N` signature.
      → **MET. 9/10 pass** (baseline 5/10). Zero `checkpoint_timeout` failures; the one failure is `structural_consensus` (scoped out) and carries no `POLL-SLOW` at all.
- [x] 5.2 Confirm `POLL-SLOW cmd:resolve_selection` no longer appears at all in a passing run's plugin log.
      → **MET.** 0 occurrences. (Non-zero `discarding position` lines remain and are correct: they discard the *previous* clip's stale position, which is what an isolation should do.)
- [x] 5.3 Run the sibling selection tests to confirm no regression in isolation behaviour.
      → All 4 pass: `xstudio_selects`, `openrv_hosts_selection`, `contended_visibility_selection` (previously `last 5: ❌❌❌❌❌`), `contended_visibility_selection_rv`.
- [x] 5.4 Record the post-fix pass rate and residual failure kinds.
      → 9/10. Residual: `structural_consensus` only (1/10). The auto-play failure did not recur.
- [x] 5.5 (added during apply) Design Decision 1 is invalidated by evidence.
      → The gate compares `_last_position_change_at` against the selection timestamp, but `on_playhead_attribute_changed` reads `self.plugin.active_playhead` (the *adopted* playhead) while a seek lands on the *viewport* playhead. Between a clip switch and adoption the plugin never observes the seek. No `frame=28` event appears in any failing log; the gate fired 0 times in 10 runs.
      → Second shape (no stall at all): the isolation fires **twice** for the same clip — reliable-selection path, then the delayed `show_atom` path with `force_new_source_clip=True` ~2.2s later — and the second reset discards the seek. Suggests deduping the reset *per clip*.
      → The §4 isolation tests passed only by setting `_last_position_change_at` directly, so they verified the gate's logic but not its precondition. Removed rather than left as false assurance.
- [x] 5.6 (added during apply) Test whether `pr/python-per-subscription-listeners` supersedes this work.
      → **It does not — it is a breaking change for this plugin.** Built and measured: **0/10 pass** (vs 9/10 on develop), new failure mode "apps are showing different media". `parent_atom` warnings 6 → 0 confirms the PR was live. xStudio stops receiving most `show_atom media-change` events (3 selections → 2 events; one run 4 → 1), so the view broadcast never fires and OpenRV never follows.
      → Exactly as [ori_sync_plugin.py:440-460](../../../xstudio_plugin/ori_sync/ori_sync_plugin.py) already predicted: "the fix REMOVES events callbacks used to receive via crosstalk … if anything here depends on that overlap it will go quiet silently."
      → Also note that block's second warning: under that PR, Playhead construction stops being free (a listener actor per subscription), which makes this change's §2 work load-bearing for resource use, not just latency.

## 6. Follow-ups (not implemented here)

- [ ] 6.1 File or note the auto-play race (`playback still active … a seek must park the playhead`) with the `set_frame`-does-not-pause and `playing_override`-without-local-play hypotheses from design.md.
- [x] 6.2 File or note the `structural_consensus` mismatch after `add_media`, capturing the projection diff detail that was not recorded in the observed failure.
      → **Captured and root-caused 2026-08-27.** 10x on a quiet host (AC, load ~4-5, 26 GB free) = **9 pass / 1 fail**, matching the recorded baseline; the one failure is this. The projection diff, recorded for the first time:
      ```
      xstudio vs openrv: timeline 2291c69f track 'videotrack':
        missing clip ec8dbbfd ('/users/.../test_media/source/encoded/car_aces_srgb.mov')
      ```
      A **single missing clip**, not a timeline pair — so this is *not* the timeline-guid-adoption bug. xStudio's `Default Sequence` holds 9 clips with `car_ACES_sRGB.mov` present **twice**: `ec8dbbfd` (random v4, full-path name, xStudio-minted) and `e66187fe` (deterministic v5, basename, `target_url` a plain path — RV-minted). Every other media appears once. OpenRV holds only its own `e66187fe`, so the diff reports xStudio's as missing.

      Mechanism, from the captured logs:
      - `22:23:46.128 _create_rv_sequence_for_timeline: no media in 'Default Sequence'` / `22:23:46.414 Flat-playlist rebuild: 0 source(s) loaded` — RV joins on a snapshot whose sequence is still **empty**; the host's clips arrive afterwards as `INSERT_CHILD`.
      - RV meanwhile registers its own already-loaded `car` source under a derived guid and broadcasts it: `22:23:47.387 apply_remote_clip_insert: clip=e66187fe name='car_ACES_sRGB.mov'` on the xStudio side, which appends it as a 9th clip.
      - `22:23:47.431 MIRROR FAILED: host's clip ec8dbbfd is in neither the object map nor the bin cache` — RV's object map is keyed by *its own* guid for that media, so the host's isolate cannot resolve. (Note `ec8dbbfd` *does* match in the clip table by normalised URL at `46.933`; the two lookups disagree, which is the observable symptom.)

      Root cause is [sequence_sync.py:1019](../../../rvplugin/ori_sync/sequence_sync.py#L1019) in `_make_clip`:
      ```python
      _guid = (self.plugin.playback._clip_guid_for_media_path(_mp)
               or self.plugin.sync_manager._derive_guid(f"rv_clip:{_mp}"))
      ```
      `_clip_guid_for_media_path` scans only `sync_manager._object_map`, which does not yet hold the host's clips at build time, so the deterministic fallback fires. Its comment — *"the derivation makes both peers agree"* — is **the same false premise the timeline-level fix already superseded**: xStudio mints random v4 guids and does not participate in RV's derivation scheme, so a derived guid can never converge with it. Cf. `_session_timeline_guid_for_name` ([sequence_sync.py:139](../../../rvplugin/ori_sync/sequence_sync.py#L139)), which fixed exactly this at timeline level by *adopting* the peer's guid.

      Intermittency is whether the host's clip reaches RV's object map before RV builds its own clip — hence ~1/10.

      **Not fixed here** (out of scope for this change). The clip-level analogue of the timeline fix is the candidate: before minting a derived guid, adopt any session clip whose normalised media path matches. Needs its own change — `_make_clip` is bundled RV code, so it also needs an rvpkg rebuild+reinstall to test.
