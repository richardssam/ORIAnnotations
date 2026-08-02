## Context

See proposal.md — Why. The mechanics that constrain the fix:

- `PlayheadGlobalEventsActor` delegates **both** `join_broadcast_atom` and `leave_broadcast_atom` to its single `event_group_` (`playhead_global_events_actor.cpp:101-105`). Two Python subscription routes therefore collapse onto one `BroadcastActor::subscribers_` entry.
- `py_context` on `develop` uses **one** `EventToPythonThreadLockerActor` per connection, shared by every subscription. Membership is keyed by subscriber address, so all routes through that listener share a single membership.
- `ModuleMeta.__call__` (`python/src/xstudio/api/module.py:176-180`) auto-runs `setup_message_handler()`, so **constructing** a `Playhead` already subscribes it to that playhead's attribute events group. What was missing was never the subscription — it was the `attribute_changed` assignment that routes the callback to us.
- `plugin_base.__connect_to_playhead` calls `cleanup_message_handler()` on the previous playhead on every `viewport_playhead_atom` event, i.e. constantly during normal navigation.

## Goals / Non-Goals

**Goals:**

- Restore position/play broadcast on `develop`, without an xStudio rebuild.
- Keep position sync alive across playhead replacement.
- Make the failure visible in the spec, so it cannot regress silently again.

**Non-Goals:**

- Fixing `py_context`'s shared-listener design. That is `pr/python-per-subscription-listeners`, upstream, and is the real fix.
- Any protocol, message-shape or RV-side change.
- Reinstating the local `plugin_base.py` patch. Patching a vendored xStudio file is what made this regress invisibly when the tree was rebased onto `develop`; the plugin-side fix survives rebases.

## Decisions

**Own the subscription rather than patching the base class.**
The two candidate fixes were: patch `plugin_base.py` again, or stop calling into it. Patching the vendored file is how this broke — a local edit to a file the plugin does not own, silently lost on rebase, with no test covering it. Owning the wiring in `playback_sync.py` keeps the fix inside the repo that depends on it.

**Wire at adoption, not at connect.**
`subscribe_to_playhead_events()` was a connect-time call, which is the wrong lifetime: the playhead is replaced repeatedly during normal use. `_adopt_playhead` runs at each of the three acquisition sites, so the wiring's lifetime matches the playhead's.

**Compare a stable key, and compare before constructing.**
`ph.remote` yields a fresh wrapper per access, so the old `!=` test always fired. Two consequences, both fixed: change detection was meaningless (21 "playhead updated" log lines for one unchanged playhead), and `on_global_playhead_event` constructed a `Playhead` — and thus a subscription — per event before deciding it was redundant. The comparison now happens first, on `str(remote)`.

**Keep this reversible.**
The workaround is documented at the call site and in the README against `pr/python-per-subscription-listeners`. If that lands, the base call becomes safe and this can be reconsidered — but it should be re-verified, not assumed.

## Risks / Trade-offs

- **`on_screen_media_changed` no longer fires.** `PluginBase` no longer maintains its own playhead, so that hook is dead. Nothing in the plugin overrides it — media-change handling comes through `on_global_playhead_event`'s `show_atom` branch — so this should be inert. → Verify explicitly; it is the most likely place for an unnoticed regression.
- **Divergence from the documented xStudio plugin API.** The maintainer-recommended path is the one being avoided, which future readers will find surprising. → The call site carries the full reasoning, and the README's stale recommendation is corrected rather than left to mislead.
- **Subscription accumulation.** `_adopt_playhead` deliberately never calls `cleanup_message_handler()`, because that leave is half the bug. Playhead wrappers are therefore dropped without unsubscribing. → Acceptable: the stable-key comparison makes adoption rare, where it was previously happening on nearly every event. Worth re-checking under a long session.
- **Fix applies only to the shared-listener build.** Correct on both builds, but strictly necessary only on `develop`.

## Open Questions

- Did this regress when the local `plugin_base.py` patch was lost in the rebase onto `develop`, or was it broken earlier? This determines whether `sync_test` results and recordings predating the rebase are still trustworthy for position-dependent behaviour. Everything from the broken window is known-bad and must be re-run.
