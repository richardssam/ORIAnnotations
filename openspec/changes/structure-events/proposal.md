## Why

Structural changes are discovered by asking. `StructureSyncController` polls
`session.playlists` on a shared background thread to find created, renamed and
deleted containers, diffing against cached state each pass. That costs a
continuous scan, and it puts a floor under detection latency that has nothing to
do with how fast the change can be shared.

Measured on 2026-08-14: a sequence created on one peer took about a minute to
reach the other. The sync was not slow — once the host noticed, the client had it
in 70 ms:

```
21:12:37.163  host    New sequence timeline 'Sequence 1' → broadcast
21:12:37.233  client  ADD_TIMELINE: new sequence timeline=211a5dc6 name='Sequence 1'
```

The delay was entirely in noticing. The poll thread had stalled, and its passes
run in series:

```
21:12:32.980  host  [POLL-SLOW] structure.poll_deleted_playlists took 58.2s
```

So the floor is one poll cycle when healthy, and the slowest pass when not.
Over an 11-hour log, 2.5% of cycles exceeded 1 s during active use — but when
they did, every pass slowed together (255/255/253/253/253 across five unrelated
passes), which is thread scheduling, not any one read. No amount of tuning an
individual pass addresses that.

xStudio already announces these changes, and the plugin already uses exactly this
argument elsewhere: it subscribes to the bookmarks actor's event group because
"subscribing gives prompt detection for all of them instead of waiting out
ANNOTATION_SCAN_INTERVAL". Structure is the same problem with no equivalent
answer.

## What Changes

- **Subscribe to the events xStudio already emits.** New playlists
  (`add_playlist_atom`, session group), renames (`rename_container_atom`),
  container removals (`remove_container_atom`), and — the case the poll was
  losing — **new sequences within a playlist** (`create_timeline_atom`, emitted
  on the *owning playlist's* group, not the session's).
- **Subscribe at both levels, because the events live at both.** A session-only
  subscription cannot see a sequence created inside an existing playlist, which
  is precisely the failure above.
- **The poll is retained as a backstop, not removed.** It stops being the
  discovery mechanism and becomes the independent check that recovers a missed
  event, a failed subscription, or structure that predates the subscription.
- **Detection stops depending on the poll thread's health.** An event arrives on
  an actor callback, so a stalled pass delays reconciliation of what is already
  known rather than discovery of what just happened.
- **Removal stops reading dead actors.** `remove_container_atom` names the
  container that went away — the fact `poll_deleted_playlists` currently
  reconstructs by reading the identity of every tracked actor, including, in the
  deletion case, one that has just been destroyed.
- **One publishing path.** Events mark work; the existing pass publishes it.
  Neither route grows a second implementation of publishing.

## Capabilities

### Modified Capabilities

- `xstudio-plugin-module-structure`: structure detection gains an event-driven
  path at session and playlist level; the structural poll is redefined as a
  reconciliation backstop rather than the discovery mechanism.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — session-group subscription
  beside the existing bookmarks one, playlist-group lifecycle, and dispatch for
  the new commands.
- `xstudio_plugin/ori_sync/structure_sync.py` — event handlers, the dirty-set
  entry point the poll also consumes, and the retained poll passes.
- **Prerequisite, partially met.** These events are emitted with `mail(...)`, so
  the broadcast actor relays them via `send_as(current_sender(), ...)`; a Python
  subscriber keyed on the sender did not receive them before `70aaaa3f "Fix
  python actor event group callback routing"`, which **is** in the current build
  (`e106f0f9`). Whether that partial fix is sufficient for `mail()`-emitted
  *playlist* events is the first thing to establish, before anything is built.
- **Interacts with an unmerged xStudio PR.** `3b0a0e72` (branch
  `pr/python-per-subscription-listeners`) gives each subscription its own
  listener, removing crosstalk between an owner's groups and making unsubscribe
  safe. It is **not** in the build and **not** merged to develop. This change is
  designed to work without it and to simplify when it lands; see design D6.
- **Risk: per-playlist subscription was previously rejected** in this change's
  earlier revision, on the grounds that it caused SIGSEGV on teardown. That
  objection was about *leaving* groups; the plugin now joins once and never
  leaves, detaching handlers instead. The mitigation is real but must be stated,
  not assumed — see design D5.
- **Risk: handlers run on an xStudio actor callback thread.** Work done inline
  blocks that actor, not the plugin's poll thread — relocating the stall
  somewhere worse.
- **Risk: an event announces existence, not readiness.** A sequence may not be
  populated when its creation event fires; publishing on arrival would broadcast
  an empty sequence and then correct it.
- **Risk: echo loops.** Applying a peer's structural change causes local events
  that must not be re-broadcast.
- No protocol change: this alters when a peer notices a local change, not what it
  broadcasts or how peers agree.
