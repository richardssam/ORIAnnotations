# fix-xs-playhead-attribute-subscription

## Why

Scrubbing and playback in xStudio stopped reaching peers entirely. No error, no warning: selection changes still synced, so the session looked alive while position and play/stop state were silently never broadcast.

The cause was `PluginBase.subscribe_to_playhead_events()`. On `develop` that call destroys the very subscription it establishes, for two compounding reasons documented in `xstudio/scratch/python-event-routing-notes.md`:

1. It calls `subscribe_to_global_playhead_events()` a **second** time, on top of the plugin's own call. `PlayheadGlobalEventsActor` delegates both `join_broadcast_atom` **and** `leave_broadcast_atom` to its single `event_group_` (`playhead_global_events_actor.cpp:101-105`), so the two routes collapse onto one entry in `BroadcastActor::subscribers_`. This is the notes' "two callbacks reaching the same group by different routes" case, which **predates `70aaaa3f`** and is therefore live on `develop`.
2. Its `__connect_to_playhead` calls `cleanup_message_handler()` on the previous playhead at **every** `viewport_playhead_atom` event. With one shared listener actor per connection, that leave revokes the membership the plugin's own `Playhead` objects depend on.

The result is the silent-omission failure the notes warn about: the callback stays registered and simply stops firing. `on_playhead_attribute_changed` never ran, so no `PLAYBACK_SETTINGS_1.0` message ever carried a real position.

Confirmed against a two-peer session log: **zero** `queuing playback state broadcast` lines in either process, and every `PLAYBACK_SETTINGS_1.0` carrying `frame=0.0, playing=False` — all of them emitted by the selection path, which takes a different route and kept working, masking the failure.

Why now: this blocks every position-dependent behaviour — playback sync, scrub following, and any `sync_test` recording made while it is broken, which would capture no position events at all.

## What Changes

- The xStudio plugin SHALL own the playhead attribute-event subscription itself, rather than delegating it to `PluginBase.subscribe_to_playhead_events()`.
- The plugin SHALL acquire the active playhead from the **viewport**, not from `PluginBase.current_playhead()`. That accessor returns `global_active_playhead_`, which starts as a spawned `"DummyPlayhead"` and is never updated when a viewport connects to a playhead — so it can address a different playhead than the one on screen indefinitely. Wiring `attribute_changed` correctly onto the *wrong* playhead produces exactly the same silent failure.
- The plugin SHALL re-check the active playhead periodically, not only on host-emitted events. Building a sequence out of a bin changes what the viewport plays without emitting a viewport-playhead event (the host suppresses it when the playhead actor is unchanged) and without a selection change, so an event-only design has no path to notice.
- `attribute_changed` SHALL be wired at every site that adopts an active playhead, so position events survive playhead replacement (viewport switches, source changes, clip isolation).
- The plugin SHALL NOT introduce a second subscription route into a broadcast group it already listens to, and SHALL NOT issue a `leave` that can revoke a membership other callbacks rely on.
- Playhead identity SHALL be compared by a stable key. `ph.remote` returns a fresh wrapper object per access, so the previous `a.remote != b.remote` test was effectively always true — every observation looked like a change and re-subscribed. The connect-time log showed 21 "active playhead updated" lines all carrying the identical address.

Not breaking: no protocol, message-shape, or peer-visible change. This restores intended behaviour rather than altering it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `xstudio-event-sync`: its "Event-Driven Playhead Sync" requirement says the plugin subscribes to playhead events instead of polling, but says nothing about **who owns that subscription or how it survives playhead replacement**. That silence is what let the behaviour regress without failing any spec. The requirement gains an explicit ownership and survival obligation.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — the `subscribe_to_playhead_events()` call site at connect time.
- `xstudio_plugin/ori_sync/playback_sync.py` — new `_adopt_playhead` / `_remote_key` / `_viewport_playhead`; the three sites that acquire an active playhead (`on_global_playhead_event`, `check_and_update_active_playhead`, `_reacquire_active_playhead`) plus the two Pinned Source Mode readers, which are subject to the same "which playhead?" question because PSM decides `view_mode`.
- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — also a 1 Hz active-playhead re-check in `_poll_loop`, since not every playhead change is announced.
- `xstudio_plugin/ori_sync/README.md` — the note recommending the `subscribe_to_playhead_events()` path is now wrong and is corrected.
- No RV-side impact. No change to `python/otio_sync_core/`.
- **Upstream dependency**: this is a workaround for a client-side xStudio defect. `pr/python-per-subscription-listeners` fixes it structurally by giving each subscription its own listener actor; if that lands, this workaround should be re-evaluated rather than kept indefinitely.
- **Test evidence**: any `sync_test` run or recording made while this was broken captured no position events. Results from that window cannot be trusted for anything position-dependent.
