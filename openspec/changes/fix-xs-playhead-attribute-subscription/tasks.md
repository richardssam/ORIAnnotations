## 1. Own the playhead attribute subscription

- [x] 1.1 Add `PlaybackSyncController._remote_key` — stable string identity for a playhead's remote handle, since the raw handles never compare equal
- [x] 1.2 Add `PlaybackSyncController._adopt_playhead` — assigns `attribute_changed`, carries over playback mode, stores the playhead; returns whether it actually changed
- [x] 1.3 Route all three acquisition sites through it: `on_global_playhead_event` (Form-2), `check_and_update_active_playhead`, `_reacquire_active_playhead`
- [x] 1.4 In `on_global_playhead_event`, compare the remote key **before** constructing a `Playhead` — construction subscribes, so building one per event to discard it churns subscriptions
- [x] 1.5 Remove the `subscribe_to_playhead_events()` call from `ori_sync_plugin.connect`, recording at the call site why the base call is fatal on `develop`
- [x] 1.6 Correct the README note that recommended the `subscribe_to_playhead_events()` path

## 2. Verify

- [x] 2.1 Scrub in one xStudio and confirm the peer follows — **confirmed working by the user**
- [ ] 2.2 Confirm `queuing playback state broadcast` now appears in the plugin log, and that `PLAYBACK_SETTINGS_1.0` messages carry real frame values rather than `frame=0.0`
- [ ] 2.3 Confirm `[position_atom] active playhead updated` now fires a small number of times per session, not ~21 for a single unchanged playhead
- [ ] 2.4 Confirm position sync survives playhead replacement: switch viewport, change on-screen source, enter and leave single-clip isolation, then scrub again after each
- [ ] 2.5 Confirm play/stop (not just scrub) propagates, since `playing` rides the same callback
- [ ] 2.6 Check for an `on_screen_media_changed` regression — `PluginBase` no longer maintains a playhead, so that hook is dead. Nothing overrides it, but confirm rather than assume (design.md Risks)
- [ ] 2.7 Long-session check: adoption should now be rare, but confirm playhead wrappers are not accumulating subscriptions over an extended session

## 3. Re-establish a trustworthy baseline

- [ ] 3.1 Answer design.md's open question: did this regress with the rebase onto `develop`, or earlier? Determines whether pre-rebase recordings and test results are still usable
- [ ] 3.2 Re-record `sync_test/recordings/xstudio_selects.jsonl`. It is stale for an unrelated reason (predates `d18ec21`, which retired `SELECTION_1.0` — see the `sync-source-view-playback` change), and re-recording was not viable while position events were never emitted
- [ ] 3.3 Re-run the `sync_test` suite and record the result as the new baseline. Any run made during the broken window is void for position-dependent assertions
- [ ] 3.4 Revisit `sync-source-view-playback` against that baseline — its remaining scope may reduce to the recording refresh

## 4. Upstream follow-up

- [ ] 4.1 Feed this back to the xStudio developer as a concrete consequence of the shared-listener design: an ordinary plugin following the documented API loses playhead events entirely. It is a stronger argument for `pr/python-per-subscription-listeners` than the synthetic repro
- [ ] 4.2 If that branch lands, re-verify whether `subscribe_to_playhead_events()` becomes safe — re-verify, do not assume — and decide whether to keep this workaround
