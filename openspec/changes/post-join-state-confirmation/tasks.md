## 1. Retain the expected value

- [ ] 1.1 Keep the `STATE_SNAPSHOT` payload as received for the duration of the join, alongside the adopted state the manager already stores (design D2).
- [ ] 1.2 Clear it once an outcome is recorded, so a later join starts from nothing.
- [ ] 1.3 Test that the retained payload is the one received, not one rebuilt from what was adopted.

## 2. Establish what this peer can actually see (gate)

- [ ] 2.1 Enumerate which fields `manager.export_state()` derives from the display and which from the manager's own record.
- [ ] 2.2 **Decide, and record in the design:** where a field comes from the record rather than the display, either source the peer's side from the plugin (`_cur_view_mode` / `_cur_clip_guid` / the playhead position) or state explicitly that the field is not confirmable.
- [ ] 2.3 **Gate:** if every compared field comes from the manager's record, the check confirms the manager against itself. Say so and re-scope before building on it (design D5, first risk).

## 3. The comparison

- [ ] 3.1 Add the confirmation entry point, comparing `project_state(retained_snapshot)` against `project_state(this peer's state)` via `diff_states` (design D1).
- [ ] 3.2 Pass `compare_frame=False` when the snapshot describes a playing session, `True` when paused — keyed on the snapshot's `playing`, not this peer's (design D4).
- [ ] 3.3 Apply `frame_tolerance`; start from `diff_states`' default of 5.
- [ ] 3.4 Record the outcome as one of confirmed / mismatched / not confirmed, with the differences when mismatched.
- [ ] 3.5 Assert the confirmation makes no state request, no broadcast, and no local change — test against doubles that fail if any is attempted (design D5).
- [ ] 3.6 Unit-test: matching states confirm; a different clip, a different frame, and a different active timeline each report a mismatch naming that field.
- [ ] 3.7 Unit-test: a playing snapshot with an advanced local frame confirms; a paused one with the same difference does not.
- [ ] 3.8 Unit-test: a frame difference inside the tolerance confirms.

## 4. Sequencing

- [ ] 4.1 Queue the confirmation from the point `apply_join_playback_state` successfully applies, so it inherits that settling rather than a timer (design D3).
- [ ] 4.2 Record "not confirmed" when the join adoption exhausts its attempts, distinct from both match and mismatch.
- [ ] 4.3 Test that no outcome is recorded while the build is incomplete.
- [ ] 4.4 Test that a peer which never settles records "not confirmed", not a mismatch.
- [ ] 4.5 Confirm the check runs on the poll thread, per the plugin's threading invariant.

## 5. Reporting

- [ ] 5.1 Log the outcome with the differing fields itemised, in a form greppable in a merged peer log (`debug/merge_sync_logs.py`).
- [ ] 5.2 Expose the outcome and the differences through `session_state_snapshot` (design D6).
- [ ] 5.3 Extend `tests/otio_sync/test_session_state_snapshot.py` for the three outcomes and the no-join case.
- [ ] 5.4 Show the outcome in xStudio's `SessionStatePanel.qml`, distinguishing confirmed / mismatched / not confirmed.
- [ ] 5.5 Show the same three states in OpenRV's session state view, read from the shared projection.
- [ ] 5.6 Present a mismatch as a fact about this peer, not as a session or peer error.

## 6. Verification

- [ ] 6.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh`; record results (see `docs/testing.md` for the rv-stub collision and the known flaky test).
- [ ] 6.2 Live: join a session whose host is on a later clip mid-shot, and confirm the check reports a match once the joiner has adopted it.
- [ ] 6.3 Live: reproduce a known-bad join — e.g. with the join adoption disabled — and confirm the check reports the mismatch and names the field.
- [ ] 6.4 Live: join a *playing* session and confirm the advancing frame is not reported as divergence (design D4, the case most likely to make the indicator wrong).
- [ ] 6.5 Live: join a session with several timelines and confirm no false mismatch from clip ordering or media differences that `project_state` deliberately ignores.
- [ ] 6.6 Sample free memory/swap alongside the live runs, per the project's standing note that swap-induced latency has twice mimicked real bugs here.
- [ ] 6.7 Collect the outcomes from a run of ordinary joins and record how often the check fires and why — the evidence any future repair change should rest on (design D5).
- [ ] 6.8 Update `docs/xstudio_constraints.md` and `docs/openrv_constraints.md` with the confirmation and its report-only boundary.
