# unresolved-patch-resync — live verification, 2026-08-28

## 5.1 contended_position_scrub, 10x before / 10x after

| | pass rate | failure kind |
|---|---|---|
| baseline (pre-change) | **4/10** | 6x `peers never agreed on a position owner within 15s` |
| after (post-change)   | **5/10** | 5x the same lease-split |

One run's difference is noise at n=10. **No claim is made that this change moved
the rate**, and none should be: `grep -c "UNRESOLVED PATCH"` over every log of
all 20 runs is 0. The stall this change repairs did not occur in either sample,
so neither sample measures it.

RV was reinstalled from `rvplugin/ori_sync/reinstall.csh` before the "after"
runs (RV loads the installed rvpkg copy, not repo source); the installed
`otio_sync_core/manager.py` was confirmed to contain `UNRESOLVED_SETTLE`.
xStudio reads `<repo>/python` directly via the spawner's PYTHONPATH, so it
picked the change up without a build.

Machine conditions were poor for both samples and identically so: load ~9.4,
swap 7136M used of 8192M. Per `feedback_suite_results_need_memory_check` a 15s
lease-convergence window under that much paging is not a clean measurement of
anything. The before/after comparison is still fair — same machine, same
pressure, ten minutes apart — but the absolute rate is not trustworthy.

## 5.2 The UNRESOLVED PATCH line in a live log

The condition **did** reproduce live, in `two_drivers_visibility_lease`
(passing):

```
14:40:30.246 apply_patch: ... event=I_AM_MASTER source=a6d2b7c9
14:40:30.246 apply_patch: ... event=INSERT_CHILD source=a6d2b7c9
14:40:30.246 UNRESOLVED PATCH INSERT_CHILD: target f4b05fb1 not in object_map (0 objects held)
14:40:30.246 === MQ SEND === STATE_REQUEST -> a6d2b7c9
```

An `UNRESOLVED PATCH` line followed by a state request, and the peer ends up
holding the media — but read the timestamps before crediting it to this change.
The request is the *join* request, fired in the same millisecond by the
pre-existing `WHO_IS_MASTER`/`I_AM_MASTER` handshake. This is a joiner that had
not yet been sent the snapshot: a patch arrived a beat ahead of it.

That makes it a live confirmation of the requirement it actually exercises —
**"a peer that was merely behind does not request state"**. No
`unresolved-patch resync` line appears anywhere in the suite's logs: the settle
window correctly suppressed a second, redundant request for a gap the join was
already closing, and `apply_snapshot` then cleared the retained target.

**The firing path itself has not been observed live.** It is covered by tests
(`test_unresolved_patch_resync.py`, 8 tests), not by a live log, because the
stall did not recur in ~25 live runs today. Reproducing it on demand needs the
master-election conflict the design explicitly scopes out
(`project_master_failover_silent_departure`).

## 5.3 Full suite

**35/36 passed** (977.1s). The single failure, `contended_visibility_selection`,
**passed on re-run** — its own history reads `✅❌❌✅✅`, so it is the
pre-existing flake, not a regression. `contended_position_scrub` passed in this
run.

Seven tests logged `UNRESOLVED PATCH` during the suite
(`delete_media_xstudio`, `two_drivers_visibility_lease`,
`xstudio_selects_script_*`, `xstudio_draws_pen_openrv_verifies`,
`flat_playlist_view_classification_xs_solo`) and **not one produced an
`unresolved-patch resync` line**. Every occurrence is the benign
joiner-a-beat-behind case, and every one of those tests passed. That is the
strongest evidence available that the settle window is not generating
false-positive snapshot requests across the suite.

## 5.4 Residual failure kinds

- `contended_visibility_selection` — one flake, cleared on re-run. Unrelated:
  visibility-claim hold-off, no structural patch involved.
- `contended_position_scrub` — **still fails roughly half the time in
  isolation**, always as `peers never agreed on a position owner within 15s`.
  This change does not address it and was never going to: as §5.1 records, the
  unresolved-patch stall did not occur in any of the 20 measured runs, and the
  failures that did occur are position-lease contention. The stall quoted in the
  proposal came from a master-election conflict that is explicitly out of scope
  (`project_master_failover_silent_departure`).

  Whether that lease-split is itself a real defect or an artefact of running a
  15s convergence window on a machine with 7136M of 8192M swap in use is not
  settled by these runs, and should be re-measured on an unloaded machine before
  anyone opens a change against it.

## 4.7 note

`./run_tests_core.sh` passes: 755 passed on the run taken for §4.7. A later
re-run showed `test_sync_recorder.py::test_resume_preserves_event_spacing`
failing 1 time in 4 in isolation. It is a wall-clock event-spacing test in the
recorder player, imports neither `manager` nor `patcher`, and is unrelated to
this change — noted here so it is not mistaken for one later.
