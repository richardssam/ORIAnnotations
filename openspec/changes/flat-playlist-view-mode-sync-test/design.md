## Context

See proposal.md for the full motivation. The test exercises a 6-step peer convergence flow — host isolates a clip and frame, viewer joins and catches up, viewer picks a different clip locally (must not affect host), host reasserts and viewer follows back. The proposal also identifies one still-open bug: xStudio broadcasting `view_mode: "sequence"` and `playback_mode: "play-once"` for a flat playlist that has no sequence structure at all.

The test belongs in `sync_test/sync_tests.yaml` as a YAML-driven scripted scenario — same command vocabulary as `reviewer_visibility_suppression`, `two_drivers_visibility_lease`, etc. — not as a standalone Python test file. The YAML runner already has all the primitives needed: `add_media`, `set_selection`, `set_frame`, `expect_no_propagation`, `concurrent_commands`, role assignment, and the built-in convergence check.

## Goals / Non-Goals

**Goals:**
- Add two YAML test entries in `sync_tests.yaml` — one for the xStudio↔xStudio topology and one for the xStudio↔OpenRV topology — exercising the full 6-step flow, so it's repeatable rather than something only ever observed by hand.
- Use that repeatability to characterize the `view_mode`/`playback_mode` anomaly: is it deterministic for this exact scenario, or does it vary run to run? If it varies, what differs between a clean run and a failing one?
- Only once that's understood: fix the actual mechanism, and turn the finding into a spec requirement that describes the real behavior, not a guessed one.

**Non-Goals:**
- Writing a new standalone Python test script. The existing YAML runner covers this.
- Changes to sequence playback logic or the host election model.
- **Fixing the `view_mode`/`playback_mode` anomaly before it's diagnosed.** Live testing has seen this go both ways — xStudio sometimes reports the correct value for this exact scenario, sometimes not — which is not consistent with a plain "flat playlists are always misclassified" bug. Committing to a specific fix (a `_cur_view_mode` classification check, or anything else) before the new repeatable test has actually characterized *when* it happens would be exactly the failure mode this project has already paid for twice: acting on a signal that isn't understood yet.

## Decisions

**1. Test structure: YAML-driven, not a Python script**

The `reviewer_visibility_suppression` pattern is the right template. It uses `script_driven: true`, assigns roles (`driver`/`viewer`), uses `expect_no_propagation` to assert the viewer's local pick is isolated, and `set_frame`/`set_selection` for the host's reassertion. The 6-step flow maps directly:

| Proposal step | YAML command(s) |
|---|---|
| 1. Set up session with 3 clips | 3× `add_media` + `wait_for_media` |
| 2. Host picks graphic, frame 89950 | `set_selection` (host only via `concurrent_commands`) + `set_frame` |
| 3-4. Viewer joins and converges | Built-in convergence after role/media setup; no extra command needed — the runner's own settle/checkpoint handles this. Optionally a `set_frame` assertion on the viewer side. |
| 5. Viewer picks laser at 91785 — must NOT affect host | `expect_no_propagation` targeting the viewer |
| 6. Host changes frame to 89900 — viewer follows back | `set_frame` on host (or `concurrent_commands` targeting host), convergence check asserts viewer matches |

Two test entries, identical commands, differing only in `apps:`:
- `flat_playlist_convergence_xs_xs`: `["xstudio", "xstudio"]`
- `flat_playlist_convergence_xs_rv`: `["xstudio", "openrv"]`

**2. Diagnose before fixing — the anomaly is intermittent, not consistently reproduced**

Live testing has seen xStudio broadcast `view_mode: "sequence"` / `playback_mode: "play-once"` for this exact flat-playlist scenario on some runs, and the correct `"source"` / Loop on others. That rules out "flat playlists are unconditionally misclassified" as the explanation — a static check on `xs_flat_playlist` or Timeline-child presence would be wrong every time, not sometimes. Whatever's actually happening is state- or timing-dependent (a stale read at broadcast time, a race with playhead/actor acquisition, something else — genuinely unknown yet).

So this change does not ship a fix for it. It ships the two YAML test entries (goal above), which turns "sometimes wrong, only noticed by watching the UI" into something that can be run repeatedly and diffed. The plan:
1. Land the two test entries first, and confirm they pass reliably for the parts of the flow that are already understood (view convergence — the fixed dedup-guard bugs).
2. Run the new scenario enough times (manually, in a loop) to see whether the `view_mode`/`playback_mode` values vary run to run under otherwise-identical conditions.
3. If they do vary: capture full host+client logs for both a clean and a failing run, and use `debug/merge_sync_logs.py --view state` to diff them — looking specifically at what's different in the window between the host's clip pick and the assertion, e.g. timing of `Pinned Source Mode` reads, actor-acquisition races, anything resembling the `Dequeue timeout` exception already seen once during a related join.
4. Only once there's an actual mechanism identified: write the fix, and add the "flat playlist view classification" spec requirement to describe it — as a follow-up task in this change, or a separate change, depending on how large the finding turns out to be.

This decision deliberately leaves `tasks.md`'s classification-fix task unscoped beyond "diagnose" until step 3 above produces a real finding — a task that says "update `_cur_view_mode`" right now would be prescribing a fix the investigation hasn't earned.

**3. A third, single-app test — and why it's a sharper diagnostic than looping the two-peer one**

`sync_test`'s inspector (`xstudio_hook.py::get_xstudio_state`) already reads `view_mode` directly from the live playhead's `Pinned Source Mode` attribute (`ph.get_attribute("Pinned Source Mode").value()`) — this is xStudio's own actual state, read independently of anything the `ori_sync` plugin computes or broadcasts. The plugin's `PLAYBACK_SETTINGS_1.0` wire message carries its own, separately-tracked `view_mode` (`_cur_view_mode`, set by `broadcast_view_state`). These are two different signals that happen to usually agree.

A third test — single xStudio instance, `apps: ["xstudio"]`, no peer — launches, adds the same 3 clips, picks `graphic_ACES_sRGB` at frame 89950, and reads both signals: the inspector's ground-truth `fetch_state()` and the actual broadcast message. If they ever disagree, that pinpoints the bug as being in the plugin's own `_cur_view_mode` tracking, not in xStudio itself (or vice versa, if they always agree with each other but disagree with what's *expected*, pointing at xStudio's actual Pinned Source Mode state instead). It's also just faster to run repeatedly for step 2 of the diagnostic plan above — no peer, no join, no convergence wait.

This surfaced a real gap: `xstudio_hook.py` does not currently report `playback_mode`/Loop Mode at all — only `view_mode` (from Pinned Source Mode), `frame`, `playing`, and `clip`. It needs a parallel read of the playhead's `Loop Mode` attribute before this test (or the two-peer ones) can assert it.

## Risks / Trade-offs

- **Risk:** Landing this change without a fix for the `view_mode`/`playback_mode` anomaly means the new test may be flaky (fails on some runs, not others) until the anomaly is diagnosed and fixed.
  **Mitigation:** That flakiness *is* the diagnostic signal this change exists to capture — mark the assertion `status: known_broken` (per the runner's own convention) rather than skip it, so the test still runs and reports every time without failing the suite, until the follow-up fix lands and it can be promoted to a real gate.

- **Risk:** The test uses 3 clips with specific frame numbers tied to the test media. If those files change, the test breaks.
  **Mitigation:** The test media is generated by `test_media/create_test_media.sh` and already canonical for the suite. The frame numbers come from the proposal and match the existing media's frame ranges.
