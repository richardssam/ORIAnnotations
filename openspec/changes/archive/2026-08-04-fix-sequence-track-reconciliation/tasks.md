## 1. Establish a trustworthy before-picture

Convergence is the thing being fixed, so it has to be measurable before and after.
The numbers below are the 2026-08-03 20:05–20:08 two-xStudio baseline.

- [x] 1.1 Capture the counters from a host log as a one-liner script or documented grep, so before/after is a number rather than an impression: `INSERT_CHILD` sent, `REPLACE_TIMELINE` sent, `sequence track new media`, `build_single_sequence_otio`, log size. Baseline: **153 / 57 / 152 / 58 / 2.6 MB** for a three-clip sequence — `debug/sequence_reconciliation_counters.sh <log>`
- [x] 1.2 Record the `manager_clips=N bin_media=N` progression the same way. Baseline oscillates 0 → 2 → 5 → 8 → 5 → 3 and never settles; converged means it reaches the real clip count and stays — same script, from the consolidated `[2F] sync authority:` line

## 2. One authority per pass

- [x] 2.1 In `poll_sequence_new_media`, determine whether a wholesale rebuild is warranted for a timeline **before** running the incremental "Additions (direct track dragging)" block, rather than after it
- [x] 2.2 When a rebuild will run for that timeline, skip incremental reconciliation for that pass — the rebuild derives the whole timeline from xStudio and already contains those clips
- [x] 2.3 Log which authority a pass chose, so the interleaving is legible in future logs (the current log shows both running and gives no hint that is wrong)

## 3. Diff against what was broadcast

- [x] 3.1 Track, per timeline, the clip identities this peer has already broadcast
- [x] 3.2 Compute the incremental diff against that record instead of against the manager's live video track, so re-registering a timeline cannot make sent inserts look unsent
- [x] 3.3 Reset that record wherever the timeline is re-registered, replaced, or removed. **Under-broadcast is the dangerous direction** — a stale record silently drops a genuinely new clip — so every teardown path must clear it, and 4.1 must be able to catch it if one is missed

## 4. Make convergence observable

- [x] 4.1 Log a bounded, greppable "no changes" line when a pass reconciles without broadcasting, so a log tail answers "is it converging?" — and so a silent under-broadcast from 3.3 shows up as suspiciously quiet passes rather than as nothing at all
- [x] 4.2 Confirm the `[2F-DIAG]` volume drops back to something readable; the diagnostics were sized for a path that ran twice per session, not hundreds of times — confirmed live: 260 KB for a full two-peer session including the diagnostic track-layout line, vs. 2.6 MB baseline

## 5. Verify against a live pair

The existing sync-test suite cannot verify this — see 6.1.

- [ ] 5.1 Two xStudio peers: create a sequence from a bin, add a clip to the **end**, reorder a clip. Confirm the peer matches the host after each edit, including the appended clip (the symptom that opened this change) — create+append via direct drag confirmed converging after the multi-track fingerprint fix (see design.md addendum); **reorder is blocked** by a confirmed xStudio-native bug (drag-reorder doesn't take even with the plugin disabled, works under RV) — unrelated to this change, tracked separately, not re-testable here until xStudio-side is fixed
- [x] 5.2 Confirm the counters from 1.1 have dropped to a bounded number per edit, and that `manager_clips` settles at the real count per 1.2 — confirmed: 1 `REPLACE_TIMELINE`, 0 `INSERT_CHILD` for the one drag edit (vs. 153/57 baseline); `manager_clips` settled at 2 and stayed
- [x] 5.3 Leave both peers idle on a sequence for ~60 s. Confirm zero structural broadcasts — the pure convergence check, and the one the old code fails hardest — confirmed: client logged zero sync activity of any kind; host logged no further passes at all once settled
- [x] 5.4 Confirm the bin no longer grows clips nobody added (baseline: `bin_media` climbed 2 → 3 → 7; peer bin held 4 where 2 were expected) — confirmed: host `bin_media` held at 2 throughout; client log stayed at baseline size with no growth signal
- [x] 5.5 Confirm the stutter is gone by feel as well as by counter — it was the user-visible symptom and should not need a log to confirm — confirmed gone

## 6. Re-establish the test baseline

- [x] 6.1 Sequence structure in any sync-test recording captured while this loop was running is untrustworthy. Identify which recordings that covers (anything recorded after `fix-xs-playhead-attribute-subscription` restored event delivery, before this change lands) and re-record or discard — `fix-xs-playhead-attribute-subscription` landed 2026-08-02 22:07 (`cf6ad99`); exactly 4 recordings were touched after that (`add_media_v2.jsonl`, `add_media_xstudio_v2.jsonl`, `delete_media_v2.jsonl`, `xstudio_selects_v2.jsonl`). Inspected each for the protocol events this loop spams (`INSERT_CHILD`, `REPLACE_TIMELINE`): all four show at most one clean structural event (or none — the selects recording is pure `SET`), not the dozens/hundreds this bug produces. None exercises a sequence timeline at all — they're flat-playlist add/delete and pure selection. **No re-record needed**; none is within the affected code path.
- [x] 6.2 Consider a regression test for 5.3 specifically — "idle session broadcasts nothing" is cheap to assert, would have caught this immediately, and is the kind of property the spec's silence about convergence let slip — added `xstudio_plugin/tests/test_sequence_reconciliation_convergence.py`: mocks the xStudio-facing surface (no live session needed), asserts idle-broadcasts-nothing and that a direct-drag rebuild converges in one pass. Runs under xStudio's bundled Python (`xstudio_python`, see reference memory) via `pytest` or directly; verified to genuinely fail against the pre-fix single-track logic, not a vacuous pass
