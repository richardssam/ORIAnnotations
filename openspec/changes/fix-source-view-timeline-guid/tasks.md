## 1. Implementation

- [x] 1.1 Add `_displayed_timeline_guid()` and resolve `_broadcast_playback`'s `timeline_guid` through it instead of `_rv_node_to_timeline_guid.get(view) or sync_manager.active_timeline_guid`. Source mode resolves via `_cur_clip_guid` → `_clip_timelines`, not via `_displayed_view()`'s third element (see design.md "What implementation changed").
- [x] 1.2 Keep the resolved guid on the wire as-is when it is `None` — do not substitute `active_timeline_guid`, and do not drop the message.
- [x] 1.3 Log the resolved guid and the *displayed* mode alongside the broadcast mode in `SEND playback`, so a mislabelled position is visible in a log rather than only in a peer's playhead.
- [x] 1.4 Point the Session State panel's `_local_view` at the same helper, so the panel judges divergence on the identity peers are actually told about.

## 2. Tests

- [x] 2.1 Add a test that a broadcast from an isolated clip carries that clip's timeline guid, not the sequence's — plus one asserting `clip_guid` and `timeline_guid` describe the same clip.
- [x] 2.2 Add tests that a view with no shared timeline carries no timeline guid (unresolvable isolation, and a clip with no registered timeline), and that a position is still sent from such a view.
- [x] 2.3 Add a test that a sequence-view broadcast still carries the sequence guid, covering both the node-map hit and the OTIO stack inner-sequence fallback.
- [x] 2.4 Run the core suite (`./run_tests_core.sh`) and confirm no regression. Re-run the new tests against the old resolution to prove they fail without the fix (6 of 8).

## 3. Live verification

- [x] 3.1 Rebuild and reinstall the rvpkg (`rvplugin/ori_sync/reinstall.csh`) — OpenRV loads the installed package, not the repo source.
- [x] 3.2 Run the reproduction: xStudio hosts a sequence of several shots and picks a frame in the second clip; OpenRV isolates a clip that *is* in that sequence. Confirmed: xStudio's playhead no longer jumps — it logs `mismatched timeline_guid — ignoring` six times against `incoming=6432c98b` (the clip's own timeline).
- [x] 3.3 Confirm sequence-following still works: the host's sequence instruction is still adopted (`11:20:51.121 apply view-state: sequence → Sequence (b3ab387e)`).
- [x] 3.4 Capture `rv_client.log` and `xstudio_host.log` and check the "subsequent changes stop propagating" symptom. Answered: it is the `on_rv_frame_changed` frame-equality guard, unrelated to the guid (design.md → Open Questions).
- [ ] 3.5 Follow up on the two defects the verification surfaced — OpenRV broadcasting a stale `view_mode` during a view switch, and xStudio re-broadcasting a remote-induced `show_atom` as a local selection (design.md → Verification outcome). Out of scope here; needs its own change.
