## 1. Own the playhead attribute subscription

- [x] 1.1 Add `PlaybackSyncController._remote_key` — stable string identity for a playhead's remote handle, since the raw handles never compare equal
- [x] 1.2 Add `PlaybackSyncController._adopt_playhead` — assigns `attribute_changed`, carries over playback mode, stores the playhead; returns whether it actually changed
- [x] 1.3 Route all three acquisition sites through it: `on_global_playhead_event` (Form-2), `check_and_update_active_playhead`, `_reacquire_active_playhead`
- [x] 1.4 In `on_global_playhead_event`, compare the remote key **before** constructing a `Playhead` — construction subscribes, so building one per event to discard it churns subscriptions
- [x] 1.5 Remove the `subscribe_to_playhead_events()` call from `ori_sync_plugin.connect`, recording at the call site why the base call is fatal on `develop`
- [x] 1.6 Correct the README note that recommended the `subscribe_to_playhead_events()` path

## 1a. Acquire the playhead from the viewport, not the global

Found 2026-08-03 from a two-xStudio host/client session: scrubbing in the **host**
reached the client not at all, while the reverse worked. Host log had **zero**
`queuing playback state broadcast` lines against the client's 46.

Root cause: `PluginBase.current_playhead()` sends a bare `viewport_playhead_atom`
to `PlayheadGlobalEventsActor`, which answers with `global_active_playhead_` —
**not** the viewport's playhead. That member is initialised to a spawned
`"DummyPlayhead"` (`playhead_global_events_actor.cpp:31-33`), is only reassigned
by the explicit "set the global playhead" handler (`:182`), and is untouched by
the handler that runs when a viewport connects to a playhead (`:189-243`). So it
lags the viewport indefinitely — the host logged the *same* playhead address at
`17:51:33` (bin only) and at `17:52:12` (with `Sequence 1` on screen).

Task 1.3's three sites split two ways: `on_global_playhead_event` Form-2 adopts
`event[3]` (correct — the real viewport playhead), while
`check_and_update_active_playhead` and `_reacquire_active_playhead` both went via
`current_playhead()` (wrong whenever it lags). The host was briefly correct at
`17:51:57` — only because the *client's* view-state drove `_apply_sequence_view`
— then `apply_playback_state` found that playhead stale, re-acquired via
`current_playhead()`, and was wrong again for the rest of the session.

- [x] 1a.1 Add `PlaybackSyncController._viewport_playhead()` — query the viewport actor directly (active viewport, else the first that exists). Do not build a `Viewport` wrapper: it is a `ModuleBase`, so constructing one subscribes it to the viewport's attribute events, and it memoises `self.__playhead` and would return a stale playhead after the first call
- [x] 1a.2 Route `check_and_update_active_playhead` and `_reacquire_active_playhead` through it
- [x] 1a.3 Route the two Pinned Source Mode readers (`_read_pinned_source_mode_fresh`, the selection-poll PSM check) through it too — PSM decides `view_mode`, so reading a different playhead's PSM mislabels source vs sequence. Keep the existing `bounded_timeout`s: the reason those avoided the cached `active_playhead` (a destroyed actor hangs the poll thread ~100 s) still stands, and `_viewport_playhead()` satisfies it
- [x] 1a.4 Add a 1 Hz active-playhead re-check to `_poll_loop`. Not every replacement is announced — building a sequence from a bin fires no `viewport_playhead_atom` (the C++ handler early-returns when the viewport's playhead is unchanged) and no selection event, and connect / `on_selection_event` / `apply_selection` were the only re-check triggers. `_adopt_playhead` no-ops on an unchanged remote key, so steady-state cost is one bounded read per second and no re-subscription
- [x] 1a.5 Log the playhead address in the Form-2 branch, not just the viewport name — the original log could not answer *which* playhead was adopted, which is the only question that matters when diagnosing this
- [x] 1a.6 Record in the README and at the `connect` call site that **if `pr/python-per-subscription-listeners` lands this must be redone**, not assumed to still hold: both reasons for avoiding `subscribe_to_playhead_events()` dissolve; `Playhead` construction stops being free, making the remote-key guards and the 1 Hz re-check load-bearing for resource use rather than churn; and the fix removes events currently arriving via crosstalk between a `PlayheadActor`'s groups, which is a silent-omission change (diff an event trace, don't eyeball behaviour)
- [ ] 1a.7 Verify against the same two-xStudio flow: start with a bin, build a sequence from its clips, scrub the host, confirm the client follows **and** that the host log now shows `queuing playback state broadcast`

### First test of 1a, 2026-08-03 19:22–19:26 — worked, then two follow-on defects

Host went 0 → 49 `queuing playback state broadcast`. But all 49 fall in
`19:23:12–19:23:16`, i.e. **before** the sequence was created at `19:23:34.9`;
after that the host broadcast nothing and the client's sequence stayed at one clip.
Two separate causes, one mine and one pre-existing:

- [x] 1a.8 **Regression in 1a.4** — `_viewport_playhead()` was not resolving a
  stable viewport. It asked for the "active" viewport and otherwise took
  whichever came back first, so consecutive polls answered with two different
  playheads (`588` ↔ `3983`, alternating at `19:23:38/40/44/45/55` with **no**
  `RECV`/`apply view-state` nearby, so not the peers disagreeing). Each flip
  re-adopts and re-wires `attribute_changed`; that churn is exactly what kills a
  shared-listener subscription, so the 1 Hz safety net destroyed what it was
  added to protect. Fix: remember the viewport name from Form-2 events and
  resolve that same viewport every time; and adopt only a reading seen on two
  consecutive scans, so a transient answer can never churn the subscription

## 2. Never join one event group twice

Pre-existing, found by the same session and previously masked by position sync
already being dead. `subscribe_timeline_item_events` and
`subscribe_viewed_container_events` both join a sequence Timeline's event group —
the first because it is a tracked timeline, the second because it is the viewed
container — and with one shared listener per connection the second silences the
first. This is failure mode 3 in `xstudio/scratch/python-event-routing-notes.md`.

Evidence (host):

```
19:23:34.951  [2F] subscribed to item_atom events for timeline 51cadd5c
19:23:35.449  [2F-DIAG] timeline event tl=51cadd5c t1=item_atom      ← last one, ever
19:23:38.359  [2F] (re)subscribed to viewed-container events (type=Timeline uuid=51cadd5c)
```

Zero timeline events and zero structural broadcasts for the following 2.5 min,
while the user added the second clip and reordered the track — so the peer kept
the one-clip sequence broadcast at `19:23:34.952`.

- [x] 2.1 Track every joined event group, keyed by the group-owning actor's address (string form, since raw handles never compare equal)
- [x] 2.2 Handle `add_media_atom` in `on_timeline_item_event` too, so one subscription can serve both roles

#### First attempt was wrong, and worse than the bug — 2026-08-03 19:47

Resolving the collision by *taking the group over* — unsubscribing the first
subscriber, then subscribing the second — violated this change's own second
obligation ("SHALL NOT issue a `leave` on a group whose membership other live
callbacks share"). With one shared listener per connection, that leave revoked
the membership every callback in the process depends on:

```
19:47:14.980  [2F] timeline 7787cd41 shares its event group ... — taking it over
19:47:14.980  [2F] subscribed to item_atom events for timeline 7787cd41
              ← after this: zero [SEL] show_atom, zero Form-2, zero [2F-DIAG],
                for the remaining 53 s. Host broadcasts: 0.
```

The sequence structure did sync correctly (the join-once half worked), but the
host went deaf to *everything* local. Both incidents so far — this one and
`19:23:38` in the previous session — have an `unsubscribe_from_event_group` at
the exact moment delivery stops. Treat that call as unusable on this build.

- [x] 2.3 Replace takeover with **multiplexing**: join each group once and fan out to multiple handlers in Python (`join_event_group` / `detach_event_group_handler` / `_dispatch_event_group`), so a second interested path never needs its own subscription
- [x] 2.4 Never leave a group while connected. Handlers detach from the fan-out; the join is kept for the life of the connection. A stale join costs one no-op dispatch, which is far cheaper than the failure it avoids
- [x] 2.5 Put the machinery on the plugin, not in one controller, and route **every** subscription through it — the container-selection subscription in `playback_sync` had the same unsubscribe-on-container-change hazard (it fired at `19:23:38`, the moment that session went deaf), and the bookmarks subscription had no dedupe at all
- [x] 2.6 Remove the dead `_sequence_playlist_sub_ids` unsubscribe — that path is a no-op placeholder so the dict is always empty, but the call was a landmine if it were ever repopulated
- [ ] 2.7 Verify: create a sequence, add a clip, reorder the track, scrub the host. Confirm the peer follows each edit, `[2F-DIAG] timeline event` keeps firing past the point the viewed container becomes that timeline, **and** `[SEL]`/Form-2 events keep arriving after any container change

### Still unexplained

- [ ] 2.7 The client's **bin** held 3 clips where 2 were expected. Plausibly downstream of structural sync going silent at `19:23:38`, but not established — re-check once 2.6 passes and the log actually contains the edits

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
- [x] 3.2 Re-record `sync_test/recordings/xstudio_selects.jsonl`. It is stale for an unrelated reason (predates `d18ec21`, which retired `SELECTION_1.0` — see the `sync-source-view-playback` change), and re-recording was not viable while position events were never emitted — done 2026-08-02 as `xstudio_selects_v2.jsonl`; `xstudio_selects` now passes
- [ ] 3.2a Re-record the three remaining position-dependent recordings, all still dated 2026-07-06 and therefore captured inside the broken window: `add_media_notc.jsonl`, `delete_media_notc.jsonl`, `reorder_media_v2.jsonl`
- [ ] 3.3 Re-run the `sync_test` suite and record the result as the new baseline. Any run made during the broken window is void for position-dependent assertions
- [ ] 3.4 Revisit `sync-source-view-playback` against that baseline — its remaining scope may reduce to the recording refresh

### Measured 2026-08-03 (run during `xstudio-controller-encapsulation`)

Suite result: 5 pass, 3 fail. The failures are exactly the tests whose recordings predate
the fix, and every failure is a position assertion — nothing structural:

| test | recording | result |
|---|---|---|
| `add_media` | 2026-07-06 | FAIL — `expected frame ~137, got 100` |
| `reorder_media` | 2026-07-06 | FAIL — `expected frame ~486, got 423` |
| `delete_media_openrv_noscript` | 2026-07-06 | FAIL — `expected frame ~137, got 15` |
| `xstudio_selects` | **2026-08-02** | PASS |
| `text_annotations_notc`, `xstudio_vs_openrv_basic_annotation_notc` | 2026-07-06 | PASS (annotation assertions, not position) |
| `delete_media_openrv`, `delete_media_xstudio` | none (scripted) | PASS |

Mechanism in `add_media`: both clips are 101 frames. The seek to 136 lands in a session
whose broadcast carries `view_mode: "source"` on a single clip, so it clamps to 100
(RV reports 101, its 1-based twin). The expected 137 only exists in the concatenated
202-frame sequence view. This is the `sync-source-view-playback` question in 3.4.

Confirmed unrelated to `xstudio-controller-encapsulation` by re-running with that change
stashed — identical failures.

## 4. Upstream follow-up

- [ ] 4.1 Feed this back to the xStudio developer as a concrete consequence of the shared-listener design: an ordinary plugin following the documented API loses playhead events entirely. It is a stronger argument for `pr/python-per-subscription-listeners` than the synthetic repro
- [ ] 4.2 If that branch lands, re-verify whether `subscribe_to_playhead_events()` becomes safe — re-verify, do not assume — and decide whether to keep this workaround
