# Task 1 findings — do the events arrive?

Run 2026-08-15 against build `e106f0f9` (headless `xstudio -e`, no live GUI
needed — `Connection(auto_connect=True)` from the embedded interpreter at
`xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3` attaches to the
headless instance's API port like any other client). Script:
`task1_investigate_events.py` in this directory.

## 1.1 — upstream fixes in the build

```
git merge-base --is-ancestor 70aaaa3f HEAD   → ancestor (present)
git merge-base --is-ancestor 3b0a0e72 HEAD   → NOT an ancestor
```

Matches design D6 exactly: the routing fix is in, the per-subscription-listener
PR is not.

## 1.2/1.3 — session group, `add_playlist_atom`

Joined the session's event group before creating anything, then
`session.create_playlist(...)`. Received on the **session** group:

```
(event_atom, add_playlist_atom, UuidActor(uuid=<new playlist uuid>, actor=<...>))
```

Confirmed, with a usable `(uuid, actor)` payload.

## 1.4 — playlist group, `create_timeline_atom` (the case that was missed)

Joined the new playlist's event group, then `pl.create_timeline(...)`.
Received on the **playlist** group (not the session group — the session
relays nothing for it):

```
(event_atom, create_timeline_atom, UuidActor(uuid=<new sequence uuid>, actor=<...>))
```

Confirmed. This is `mail()`-emitted and arrives correctly on this build, so
`70aaaa3f` is sufficient for it — the empirical question design D6 flagged as
open is answered: **yes**, sufficient.

## 1.5 — `rename_container_atom`

Two routes tested, both fire, at different levels:

- Renaming the **sequence** (child of the playlist) via
  `pl.rename_container(name, container_uuid)` → fires on the **playlist**
  group: `(event_atom, rename_container_atom, Uuid, new_name)`.
- Renaming the **playlist** itself via
  `session.rename_container(name, container_uuid)` → fires on the **session**
  group with the same shape, *and* the playlist also emits a `name_atom` on
  its own group (sibling traffic, see 1.7).

Both carry the uuid and the new name directly — no re-read needed.

**Gotcha reproduced**: the container uuid to pass is `create_playlist`'s /
`create_timeline`'s first return value, not `Playlist.uuid` /
`Timeline.uuid` (the actor's own uuid) — passing the actor uuid makes the
call return `False` silently, no event, no error. Matches the project's
existing `xstudio_container_uuid` memory; worth restating in
`docs/xstudio_constraints.md` (task 8.8) since the handler code must resolve
the same way.

## 1.6 — `remove_container_atom`

- Removing the **sequence** via `pl.remove_container(container_uuid)` → fires
  on the **playlist** group: `(event_atom, remove_container_atom, Uuid)` —
  carries the removed container's uuid, no actor needed (matches the
  proposal's "removal stops reading dead actors").
- Removing the **playlist** via `session.remove_container(container_uuid)` →
  fires on the **session** group, and fires **twice**: once with a single
  `Uuid`, once with a `VectorUuid` — the latter is presumably the bulk report
  of the playlist's own removed children. Handlers must accept both shapes
  for `remove_container_atom` at the session level, or at least not choke on
  the `VectorUuid` form.

## 1.7 — sibling-group traffic

Every structural op also produced a `last_changed_atom` on the acted-on
group (and, since a playlist's own group and its containment in the session
are both watched, sometimes on both groups for one action). Renaming a
playlist additionally produced a `name_atom` on the playlist's own group.
Volume: 1-2 extra messages per action, all cheap to discard by type check —
matches design D5/D6's "negligible volume" prediction, though the concrete
types seen are `last_changed_atom` and `name_atom`, not literally
`change_atom` as design.md's shorthand suggested. Handlers (task 4.5) must
ignore unrecognised types cheaply regardless of which type shows up.

## 1.8 — creation routes observed

- **New sequence via interactive-equivalent API** (`pl.create_timeline`):
  confirmed, reaches `create_timeline_atom` as above.
- **Duplicated sequence** (`duplicate_container_atom`): attempted directly
  against `PlaylistActor` (`playlist_actor.cpp:1052`, needs
  `(uuid, uuid_before, into)`); the call itself failed
  (`RuntimeError: broken_promise`) on this build, independent of anything in
  this change — an existing xStudio-side issue with that code path in a
  headless single-instance session, not investigated further since it isn't
  load-bearing here.
- **Session load** (structure already present when sync starts): not
  separately exercised in this script. Not load-bearing for the gate either
  way — `Requirement: The structural poll remains the backstop` already
  covers "structure present before subscription is still detected"
  regardless of which creation route produced it (D2/D4), and that scenario
  gets its own test in section 5.

## 1.9 — gate verdict

**Pass.** `mail()`-emitted playlist events (`create_timeline_atom`,
`rename_container_atom`, `remove_container_atom` at the playlist level) all
arrive on this build with `70aaaa3f` alone. This change does **not** wait for
`3b0a0e72`; proceed with implementation per design D6's "built for today's
build" branch.
