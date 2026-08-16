## Context

See `proposal.md` — Why. What the investigation established, since the design
rests on it. All of it is read from the pinned xStudio build, not observed
running — which is why task 1 is a gate.

**The events exist, and interactive creation reaches them.**
`PlaylistActor::create_container` (`playlist_actor.cpp:2152`) is called by every
interactive `create_timeline_atom` handler and emits:

```cpp
mail(utility::event_atom_v, create_timeline_atom_v, UuidActor(detail.uuid_, actor))
    .send(base_.event_group());
```

**The sequence event is on the playlist's group, and the session does not relay
it.** `session_actor.cpp` has no `event_atom, create_timeline_atom` handler at
all; its handlers for child container events are empty sinks (`:1762`). This is
the correction to this change's earlier revision, which subscribed only to the
session and would therefore not have detected the failure in the proposal:
`New sequence timeline 'Sequence 1' (playlist='Added Media')`.

**The payload carries a live actor handle.** `UuidActor(uuid, actor)` — a handler
can address the new container directly instead of re-enumerating
`session.playlists` to find it.

**Session-level events are on the session's group.** `add_playlist_atom`
(`session_actor.cpp:2168, 2282`), `rename_container_atom` (`:1561`),
`remove_container_atom` (`:1538`, carrying the container uuid).

**Constraint — the poll is not merely slow, it is shared.** `_poll_loop` runs
every pass in series on one thread. Over an 11-hour log, 2.5% of cycles exceeded
1 s during active use, but the per-pass counts were near-identical
(255/255/253/253/253), so whole cycles slow together. That is thread scheduling,
not a slow call, and tuning a pass does not address it.

**Constraint — Python event subscription is not yet isolated.** See D5/D6: on the
current build, subscriptions collapse onto one listener keyed by group *owner*.

## Goals / Non-Goals

**Goals:**

- Discovery latency that does not depend on the poll thread.
- Coverage of the case the poll was losing: a sequence created inside a playlist.
- Exactly one publishing path, reached by both discovery routes.
- A system that still works with every subscription absent.

**Non-Goals:**

- Removing the poll. Reversed from this change's earlier revision; see D2.
- Subscribing to every event xStudio emits — only those answering "what structure
  exists".
- Media-within-a-sequence detection (`add_media_atom`). Same technique, different
  capability.
- Fixing the poll thread's throttling, or the wire protocol. Neither changes.

## Decisions

### D1 — Events discover, the poll reconciles; both call one publish path

The existing poll passes already compute "what does xStudio have that the manager
does not" and publish the difference. That logic stays where it is and remains
the only place a structural change is published.

An event does not publish. It marks a container **dirty** and enqueues a wake-up;
the poll thread then runs the existing pass for that container.

```
create_timeline / add_playlist event ──▶ mark dirty ──┐
                                                      ├──▶ existing publish pass
structural poll tick ─────────────────────────────────┘
```

This satisfies "published once, by one path" with nothing to deduplicate: the
event is not a publication. An event for a change the poll already published
costs one no-op pass, which is the cheap direction to be wrong in.

*Alternative rejected:* the handler publishes and the poll skips what it sees
already published. That is two implementations of publishing, the second reached
only when the poll wins a race — rare, and therefore the one that rots. It also
puts manager access on an xStudio callback thread (D3).

### D2 — The poll is kept as the backstop (reversal from the earlier revision)

The earlier revision of this change removed `poll_new_playlists`,
`poll_playlist_renames` and `poll_deleted_playlists` outright. It should not.

A subscription can be missed, fail, or never be established — for a playlist that
appeared while a join failed, for a session already populated when this peer
connected. With no independent check, each becomes a silently unsynced session
that nothing reports, which is the failure mode this project keeps paying for.
`structure-divergence-recovery` exists because peers diverge silently; it triggers
on broadcast refusal and so catches divergence caused by refusals and nothing
else — not a missed event.

The poll's *interval* may be relaxed, since it no longer bounds discovery
latency. That is the saving, rather than deleting it.

### D3 — The handler enqueues; it never reads, publishes, or touches the manager

Structural events arrive on an xStudio actor's callback thread. The threading
invariant already forbids manager access there, and here it protects something
further: work done inline blocks **xStudio's actor**, not the plugin's poll
thread. Publishing inline would relocate the very stall this change removes into
a worse place.

The handler does two cheap things — record the identity in a dirty set, enqueue a
command — and returns. Every content read happens on the poll thread. This is why
the event's actor handle is *stored*, not *used*, by the handler.

### D4 — Readiness is settled by the existing pass, not by the handler

An event says a container exists, not that it is populated. Rather than have the
handler wait or retry — on a callback thread, where it must not — the dirty mark
persists until the existing pass can describe the container.

The pass already decides whether a sequence has something publishable; an
unreadable or empty-so-far one leaves the mark set and is retried next tick. A
container that never becomes readable costs one cheap check per tick, which
satisfies "does not block others" without a timeout policy to tune.

*Alternative rejected:* publish on arrival and correct later. Peers apply that as
two structural changes — a worse artefact than the latency it replaces, landing
on every peer rather than one.

### D5 — Per-playlist subscription, joined once and never left

Subscriptions are needed at two levels, because the events are:

| Group | Event | Tells us |
| --- | --- | --- |
| session | `add_playlist_atom` | a new playlist exists — join its group |
| session | `rename_container_atom` | a container was renamed |
| session | `remove_container_atom` | a container went away |
| playlist | `create_timeline_atom` | a new sequence exists in it |

**This change's earlier revision rejected per-playlist subscription**, recording
that it "causes SIGSEGV crashes on tear-down". That objection was about *leaving*
groups, and it is now mitigated by an established pattern rather than by
optimism: `join_event_group` joins a group once and **never leaves**, and
`detach_event_group_handler` removes a callback from the fan-out while keeping
the membership. The plugin already subscribes per-object this way to timeline
items, viewed containers and selection actors — all dynamic, all torn down during
a session.

The cost is one stale join per deleted playlist: a no-op dispatch each. The
plugin already accepts and counts exactly this trade for playheads, logging the
group total on every new join so an unbounded run is visible.

On the current build, subscribing to a playlist's `event_group()` also delivers
its siblings' traffic (D6). `PlaylistActor` owns three groups; the other two
carry 2 and 3 send sites, all `change_atom`-shaped. Handlers must therefore act
only on the message types they recognise — cheaply, per the threading requirement
— and the volume is negligible. Had it been scrub-rate, this design would be
waiting for D6 instead.

### D6 — Built for today's build; simplified when `3b0a0e72` lands

Two upstream fixes matter, and only one is present:

| Commit | What it fixes | In build `e106f0f9`? |
| --- | --- | --- |
| `70aaaa3f` | Python event-group callback routing | **yes** |
| `3b0a0e72` (`pr/python-per-subscription-listeners`) | one listener per subscription | **no**, and not on `develop` |

Without `3b0a0e72`, one listener is shared per connection and events are
dispatched by matching `current_sender()`. Because a `BroadcastActor` forwards
with `send_as(current_sender(), ...)`, events arrive attributed to the group's
**owner**, so an owner's groups collapse onto one key — hence the crosstalk in
D5. It also means `mail()`-emitted events key on the owner while `anon_mail()`
ones key on the group, and that removing one callback can revoke a membership
others rely on — the root of both the never-leave pattern and the SIGSEGV
objection.

`create_timeline_atom` is `mail()`-emitted, so it sits in the affected class.
`70aaaa3f` is present and addresses routing, but whether it is *sufficient* for
`mail()`-emitted playlist events on this build is an empirical question, not a
readable one. Task 1 answers it before anything is built.

**This change does not wait for the PR.** It is unmerged with no timeline, the
never-leave pattern already mitigates the unsubscribe hazard, and the crosstalk
volume is negligible. When it lands, two simplifications become available and
should be taken then, not designed for now:

- leave a playlist's group when the playlist is deleted, instead of accumulating
  stale joins;
- drop defensive filtering that exists only to discard another group's traffic.

If task 1 shows the events do not arrive on this build, that inverts: the PR
becomes a prerequisite and this change waits for it.

### D7 — Echo suppression is reused, not reinvented

Applying a peer's structural change mutates local xStudio structure and emits the
same events a local action would. The existing `_structural_mutation_suppress_until`
guard and the `_sync_playlists` registry check already exist for this and cover
the new route unchanged — a marked-dirty container that the registry says came
from a remote apply is dropped before it reaches the publish pass.

The guard is time-boxed, so it must be set around the apply and not merely before
it; a mark made after it expires is indistinguishable from a local action, and is
then correctly published.

### D8 — Off switch

Subscription is behind an environment switch, defaulting on, in the style of the
existing enforcement switches (`ORI_VISIBILITY_AUTHORITY`,
`ORI_BROADCAST_OWNERSHIP`): read per call, not cached at import.

Disabled means no subscriptions and no dirty marks — the poll alone detects
structure, which is exactly today's behaviour. Because D2 keeps the poll, the
rollback needs no rebuild and restores nothing, since nothing was removed.

## Risks / Trade-offs

- **The events may not arrive on this build** → task 1 establishes it before any
  implementation, and inverts the D6 decision if it fails.

- **A silently missed subscription reverts a playlist to poll speed** → the join
  is idempotent per group key and the poll re-attempts it for every playlist it
  enumerates, so a failed join self-heals within one cycle. Persistent failure is
  logged per playlist, since the failure is per-container.

- **Stale joins accumulate, one per deleted playlist** → a no-op dispatch each,
  visible in the existing per-join group-count log, and removable once
  `3b0a0e72` lands. Preferred to leaving groups, which on this build can deafen
  unrelated subscriptions.

- **Events arrive on an actor callback thread** → D3 confines the handler to a
  set insert and an enqueue. The risk is not that this is hard, but that a later
  change adds "just one read"; the threading rule is in the spec, not only in a
  comment.

- **A dirty mark for a container that never becomes readable is retried forever**
  → one cheap check per tick. Preferred to a timeout, which needs a value and
  turns a stuck container into a silently dropped one.

- **Event storms during bulk operations** (session load, bulk import) → the dirty
  set collapses duplicates by construction; N events for one container cost one
  pass.

- **This does not fix the poll thread's throttling** → ~50% of cycles over 1 s
  while idle, 2.5% in active use. Discovery stops depending on it; reconciliation
  still does, and anything only the poll detects keeps today's latency.

## Migration Plan

1. **Prove the events arrive** (task 1, a gate). A throwaway subscription that
   logs what is received in a live session. If they do not arrive, stop and
   record why — the answer decides whether this change waits for `3b0a0e72`.
2. Subscriptions and handlers, marking dirty, with the poll unchanged and the
   dirty set unread. Dark: behaviour identical, logs show whether marks track
   what the poll then finds.
3. The poll consumes the dirty set — the step that changes latency.
4. Removal and rename move onto their events, with the bounded poll reads
   retained as backstop.
5. Relax the poll interval once discovery no longer depends on it.

**Rollback:** the switch (D8) at any point; the poll alone is today's behaviour.

## Open Questions

- Whether `add_media_atom` on the playlist group should later replace the
  sequence-media polls. Deferrable: same technique, separate capability, nothing
  here changes if the answer is yes.
- What the relaxed poll interval should be (step 5). Deferrable: it is one
  constant, and any value from "unchanged" upward is correct once discovery is
  event-driven.
- Whether a sequence created by duplication or by session load reaches
  `create_container` or only `notify_tree` (`playlist_actor.cpp:2282`). Both emit
  the same event, so the subscription is unaffected; task 1.5 records which paths
  are actually seen.
