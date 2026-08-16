## Context

See `proposal.md` — Why. This change is almost entirely a new *caller* of
existing machinery, so what matters here is the shape of what already exists.

| Component | What it gives us | Reached today from |
| --- | --- | --- |
| `request_state()` (`manager.py:2228`) | sends `STATE_REQUEST` to the master, enters `STATE_JOINING`, buffers deltas | joining only |
| `apply_snapshot()` (`manager.py:3882`) | wipes and rebuilds `_timelines`/`_object_map`, replays buffered deltas, transitions to `STATE_SYNCED` | `_h_state_snapshot` |
| `on_synced` callbacks | **rebuild the host application** — RV `rebuild_rv_session()` (`plugin.py:330`), xStudio `load_timelines` (`ori_sync_plugin.py:1161`) — but only `if not is_master` | `_set_status(STATE_SYNCED)` |
| `_role_blocks(method)` / `_role_permits(group)` | the role refusal added by `session-roles` | the `broadcast_*` sites |
| `_owns_channel(CHANNEL_STRUCTURE)` | the lease refusal from `broadcast-ownership`; returns `True` for everyone while `ownership_enforcement_enabled()` is off | same |
| `session_state_snapshot()` (`session_state.py:68`) | the plain-dict projection both panels render, already carrying nullable conditions (`join_confirmation`, `driverless`) | `ui_model.py`, both `SessionStatePanel.qml` |

Four properties of the current code shape every decision below:

- **The rebuild already exists and is already wired to `STATE_SYNCED`.** Both
  hosts do their join-time rebuild inside `on_synced`. Re-entering the join path
  mid-session gets the host rebuild for free; nothing in either plugin needs a
  second rebuild entry point.
- **The three structural sites are not uniform.** `broadcast_move_child`
  (`manager.py:3219`) and `broadcast_remove_child` (`manager.py:3255`) return a
  status and test `_is_syncing` / `network` / `status` *before* the authority
  checks. `insert_child` (`manager.py:2444`) returns `None`, has no `status`
  test at all, and applies its role check *before* the `_is_syncing` test — so
  it refuses on role even when the mutation originated from the session.
- **`insert_child` carries annotations too.** Its `group=` parameter is
  `ANNOTATION` on the annotation paths, precisely so a reviewer may draw while
  unable to reshape the timeline. Any trigger placed there sees annotation
  traffic unless it tests the group.
- **The `STATE_REQUEST` timeout is a join failure handler.** `tick()`
  (`manager.py:3721`) clears `master_guid` and drops to `STATE_DISCOVERING`
  after 5 s. Correct for a joiner that cannot find a master; wrong for a
  synchronised peer repairing itself, which would be ejected from a session it
  is still a member of.

## Goals / Non-Goals

**Goals:**

- Recover using the join path as it stands, adding a caller and a failure mode
  rather than a second rebuild implementation.
- Attribute divergence only to local user edits that the session was not told
  about — never to a remote apply, a no-op, or an annotation.
- Make every failure mode leave the peer in the session, holding its content,
  telling the truth, and eligible to try again.

**Non-Goals:**

- **Detecting divergence from evidence.** Only the refusal marks it; see D1 and
  the capability spec's rationale.
- **Timeline-level structural refusals** (`broadcast_add_timeline`,
  `broadcast_remove_timeline`, `_rename`, `_replace`). See D6.
- **Repairing a diverged master.** It has no authority to ask; see D8 and Risks.
- Any wire-format change. `STATE_REQUEST` / `STATE_SNAPSHOT` are used as they
  stand.
- Preventing the local edit. The send-side model is deliberate
  (`insert_child`'s docstring): a restricted peer keeps working.

## Decisions

### D1 — Mark divergence at the authority refusal, not at every early return

Each structural site has six ways to not broadcast. Only two are divergence:

| Early return | Divergence? | Why |
| --- | --- | --- |
| `_role_blocks(...)` | **yes** | local model changed, session not told |
| `not _owns_channel(STRUCTURE)` | **yes** | same (see D5) |
| `_is_syncing` | no | the change *came from* the session |
| `msg is None` | no | the patcher found nothing to change — the local model did not move |
| `not self.network` | no | there is no session to diverge from |
| `status != STATE_SYNCED` | no | not joined — **and a rebuild is `STATE_JOINING`** |

That last row is load-bearing. During recovery the status is `STATE_JOINING`,
and the host application's own churn while rebuilding re-enters these sites. If
`status != STATE_SYNCED` marked divergence, every rebuild would re-trigger
itself.

Implementation is one predicate plus one helper on the manager:

```python
def _structural_divergence_applies(self) -> bool:
    return (not self._is_syncing
            and bool(self.network)
            and self.status == STATE_SYNCED)

def _note_structure_diverged(self, site: str, reason: str) -> None: ...
```

The helper is called from the role and lease branches of the three sites, gated
on that predicate and — in `insert_child` — on `group == authority.STRUCTURE`.
`insert_child`'s existing check order is left alone; the predicate supplies the
`_is_syncing` and `status` tests it lacks, so no existing broadcast behaviour
moves.

*Alternative considered:* infer divergence in `apply_patch` when an incoming
patch names an object this peer does not hold. Rejected for the reason the spec
states — that signal cannot distinguish "I edited something I may not edit" from
"I have not caught up yet", and it fires on ordinary join races.

### D2 — Recovery is `request_state()` re-entered, and the rebuild is `on_synced`

```
_note_structure_diverged  →  (debounce, D4)  →  request_state()
   → STATE_JOINING → STATE_SNAPSHOT → apply_snapshot() → STATE_SYNCED
   → on_synced → rebuild_rv_session() / load_timelines
```

No new message, no new rebuild path, no plugin-side recovery entry point. This
is the whole reason the change is small: the most-exercised path in the codebase
already does exactly what recovery needs, including delta buffering and replay
(`apply_snapshot` lines 3961–3982), which the spec requires of a re-request.

Two consequences to accept deliberately rather than work around:

- **`on_synced` fires again.** Its docstring says "once"; mid-session recovery
  makes that "once per join, including a re-join in place". Both hosts' handlers
  are idempotent rebuilds already. The docstring is updated as part of the work.
- **Post-join confirmation re-runs.** `apply_snapshot` bumps `join_generation`,
  so RV's deferred `confirm_join_state` reports on the recovery snapshot. That is
  a feature: the rebuild gets confirmed by machinery that already exists.

*Alternative considered:* a dedicated `RESYNC` request and a bespoke rebuild that
diffs rather than replaces. Rejected — a second structural-apply path would drift
from the one every join exercises, and the spec explicitly chooses replacement
over diffing because the local edit carries nothing worth merging.

**Caveat, resolved as D2a and D2b below:** "the existing rebuild is enough" holds
for most of RV's rebuild — `_rebuild_rv_session()`'s single-timeline branch and
its source-loading pass both already reuse/skip what is already present. It did
not hold for xStudio as implemented (create-only), nor — found live, task 7.3 —
for one path inside RV's own rebuild: see D2b.

### D2a — xStudio needs a second, narrower reload for timelines it already has

`do_load_timelines()` (`timeline_build.py:117`) skips any timeline guid already
in `plugin._sync_playlists`:

```python
for guid, otio_tl in plugin.manager.timelines.items():
    if guid in plugin._sync_playlists:
        continue
```

That skip is deliberate and load-bearing elsewhere: `ori_sync_plugin.py`'s
`add_timeline` remote-apply handler re-invokes `load_timelines` on every new
timeline broadcast by any peer, and relies on the skip to make that "safe to
call repeatedly" — without it, one peer adding a timeline would wipe and
rebuild every other peer's already-open playlists on every occurrence.

A diverged peer's diverging timeline is, by construction, one it already has a
playlist for — that is how it made the disallowed local edit — so this skip
means recovery's `on_synced` → `load_timelines` silently does nothing for
exactly the timeline that needs to change. Confirmed against the flat and
sequence branches alike; both are reached only for guids not already known.

**Decision:** a second, narrowly-scoped method,
`StructureSyncController.reload_existing_timelines()`, reconciles every
playlist this peer already has. `on_synced`'s xStudio handler calls it, in
addition to `do_load_timelines()` (still needed for any genuinely-new guid),
**only** when this synced firing is a recovery — read from
`manager.state_request_reason == "recovery"`, which D2 arranges to stay set
through the `on_synced` firing inside `apply_snapshot` for exactly this. The
general `add_timeline` path is untouched: it never reads this flag and keeps
calling only `do_load_timelines()`, so today's create-only behaviour for a live
session is unchanged.

The proposal's own motivating incident (`broadcast_remove_child: suppressed`
immediately followed by `flat playlist deleted media: 'seq_A' removed`) is a
flat playlist, not a sequence — so both branches are in scope, not just the
sequence one D2a originally considered:

- **Sequence timelines** reload via `xs_timeline.load_otio(otio_str,
  clear=True)` against the *existing* `xs_timeline` — mirroring
  `apply_remote_remove_child`'s sequence branch call for call, including its
  `remote_structural_apply_scope()` / `arm_reload_residual()` annotation-reload
  handling. xStudio has no incremental reconciliation API for a Timeline, only
  clear-and-reload, so this is the same call `do_load_timelines()` already uses
  to build a fresh one, reused here in place rather than duplicated.
- **Flat playlists** have no bulk equivalent — bin membership is reconciled by
  computing the guid-set diff directly, then driving it through the existing
  single-item appliers (`apply_remote_clip_insert`, `apply_remote_remove_child`)
  exactly as a live INSERT_CHILD/REMOVE_CHILD delta would, so recovery inherits
  their suppress-window and annotation-scope handling rather than a second copy
  of it. `apply_flat_playlist_move` (already used by the live MOVE_CHILD path)
  reconciles order the same way.

*Alternatives considered:* removing the skip from `do_load_timelines()`
outright (rejected — it would make every `add_timeline` broadcast wipe every
peer's existing playlists, a regression far worse than the bug this change
fixes); a bespoke bulk diff-and-rebuild for flat playlists instead of reusing
the single-item appliers (rejected — the appliers already carry the exact
suppress-window and annotation-scope discipline a bespoke version would have to
duplicate, on the one platform the motivating bug was found on).

### D2b — RV's own rebuild duplicated every multi-timeline sequence on a second call

Found live (task 7.3, 2026-08-16): recovering a role-refused delete produced the
clip back, correctly — but also a duplicate `RVSequenceGroup` for every
multi-timeline sequence RV already had, and a client that no longer followed
xStudio's selection into the right one.

`_rebuild_rv_session()`'s Pass 3 (`sequence_sync.py`, the `len(timelines) > 1`
branch) calls `rv.commands.newNode("RVSequenceGroup", ...)` unconditionally for
every non-flat, non-OTIO-origin timeline. On a fresh join this is correct —
nothing exists yet. On a recovery rebuild it is not: the peer has been in the
session for a while, and every timeline it already mirrors (built by the
ordinary `ADD_TIMELINE` receive path, not by a rebuild) is still tracked in
`self._rv_node_to_timeline_guid`. Pass 3 never consulted that map before
creating, so a recovery rebuild produced a second `RVSequenceGroup` per
existing sequence — sharing a name with the original, with only the *new* one
recorded going forward, so the map now pointed away from whatever was actually
on screen. That mismatch is what broke xStudio's click-to-select: RV's
view-follow logic resolves "the" node for a guid from the same map a rebuild
had just pointed at the wrong copy.

This is the reason the `elif len(timelines) == 1` branch immediately below
Pass 3 already reuses an existing view — its own comment ("just as the
multi-timeline path does for newly created sequences") describes what Pass 3
was *supposed* to do, and evidently never did; harmless before this change,
since nothing previously called `_rebuild_rv_session()` a second time on a live
session.

**Decision:** Pass 3 now looks up `tl_guid` in `self._rv_node_to_timeline_guid`
first, confirms the mapped node is still a live `RVSequenceGroup`
(`rv.commands.nodesOfType`, the same existence check `_check_sequence_reorders`
already uses elsewhere in this file), and reuses it — `setNodeInputs` +
`_set_sequence_ui_name` — exactly as the single-timeline branch does, only
falling through to `newNode` when no live node is mapped.

*Alternative considered:* clearing `_rv_node_to_timeline_guid` at the top of
every rebuild and treating every recovery as a from-scratch join. Rejected —
that discards the identity of every sequence RV already correctly tracks (their
reorder/rename/delete-detection state in `_sequence_input_order` and friends),
which is a larger reset than the one clip this peer actually diverged on.

### D2c — A recovery rebuild re-applies playback once more before confirming

Found live (task 7.3, same session as D2b, 2026-08-16, after D2b's fix):
divergence, refusal, and recovery all worked, and the duplicate-sequence
symptom was gone — but the peer settled on the wrong frame after the rebuild,
twice, with a different wrong value each time (`expected ~163.0, got 100.0`,
then next cycle `expected ~131.0, got 0.0`). A fixed value would point at
arithmetic; a different one each time points at a race.

`on_synced`'s existing comment already anticipated *some* settling need
("Deferred one event-loop turn so any RV-internal graph update still in flight
from the build lands first") — that is what the single `QTimer.singleShot(0,
...)` before `confirm_join_state` is for. It is not enough here: D2b's reused
`RVSequenceGroup` is the one already on screen, and re-inputting it
(`setNodeInputs`, to restore or reorder its membership) is a new situation a
join never created — nothing was already displaying the node being rebuilt.
The evidence points to RV resetting that sequence's own frame as a side effect
of the EDL change, landing after `_apply_playback`'s explicit `setFrame` call
but within the single deferred tick already budgeted for settling.

**Decision:** confined to a recovery-triggered synced firing
(`manager.state_request_reason == "recovery"`, held live through
`on_synced` per D2), re-apply `playback_state` once more inside the existing
deferred callback, then defer confirmation by one further tick so the
reapply itself has a chance to settle before it is checked. Re-applying an
already-correct frame is a no-op, so this costs nothing when the race does not
occur. The plain join path is left exactly as `post-join-state-confirmation`
verified it — no evidence ties it to this mechanism, and widening the fix
there risks a working path on an inferred, not directly observed, cause.

*Open, not resolved here:* the plain join also showed an unrelated confirmation
mismatch in the same session (`unexpected timeline`, not a frame race) — noted
in tasks.md as a candidate for its own investigation, not folded into this fix.

### D2d — xStudio's own sequence-creation path never subscribed to its item events

Found live (task 7.3, a third attempt, 2026-08-16, this time with xStudio —
not RV — as the restricted client): the deletion in `Sequence 1` was refused
correctly by role at the xStudio *host* end of the test before, but on this
xStudio *client* nothing was refused, nothing diverged, nothing recovered — the
client's own log carries no `remove_child`, no `diverg`, no `STATE_REQUEST` at
all for the whole session.

Traced to `TimelineBuildController.do_load_timelines()`'s sequence-creation
branch (`timeline_build.py`, the nested `else:` building `xs_timeline =
playlist.create_timeline(...)`): unlike its sibling at `timeline_build.py:405`
(a different creation path, reached when *this* peer discovers a native
sequence) and unlike the two flat-playlist branches directly above it in the
same function, it never calls `subscribe_timeline_item_events(guid,
xs_timeline)`. Confirmed by absence: the client's log never once shows `[2F]
subscribed to item_atom events for timeline ...`, which that method logs
unconditionally on success, and its own `[2F-DIAG] timeline event tl=...` line
never appears either — only the unrelated *viewed-container* subscription's
`[2F-DIAG] viewed-container event` fired, six times, for the same delete.

Without that subscription, xStudio's own `item_atom` notifications for the
sequence never reach `on_timeline_item_event`, so `execute_sync_container` is
never queued, so `poll_sequence_track_deletions`/`_new_media`/`_reorders` never
run for it — the peer does not merely fail to *broadcast* its local edit, it
never *notices* one happened. There is no backstop for this specific gap
either: `join_known_playlist_groups`'s periodic self-heal (`design D5`/task
3.5 of the archived `structure-events` change) only re-attempts the
**playlist**-level join, not this per-**timeline** one.

This is a pre-existing gap in `structure-events` (archived 2026-08-16, shortly
before this session), not something structure-divergence-recovery introduced —
but every one of this change's three structural sites depends on the peer
detecting its own local edit in the first place, so a peer that loaded a
sequence through this specific path can never diverge, never mind recover.
`do_load_timelines()` is exactly the path every non-master peer's *join*
snapshot goes through, which is why this was reachable at all.

**Decision:** add the missing `subscribe_timeline_item_events(guid,
xs_timeline)` call, in the same place its sibling branches already call it.

*Open, not resolved here:* `apply_remote_remove_child`'s and `apply_sequence_
insert`'s own `load_otio(clear=True)` reload paths (and now
`reload_existing_timelines`'s D2a sequence branch) rebuild `xs_timeline` via
`load_otio`, not `create_timeline` — worth confirming a subsequent reload does
not silently invalidate this subscription's underlying actor handle. Not
observed as broken live; flagged for the next round of live verification.

### D3 — One request path, two failure handlers

`request_state()` is shared; only the timeout differs. A field records why the
request was sent (`_state_request_reason`, `"join"` or `"recovery"`), and the
timeout branch in `tick()` splits on it:

| | join request | recovery request |
| --- | --- | --- |
| on timeout | clear `master_guid`, → `STATE_DISCOVERING` (unchanged) | keep `master_guid`, → `STATE_SYNCED` |
| divergence | n/a | stays set; peer becomes *unrecoverable* until the next attempt |
| app event | `state_request_timeout` | `structure_recovery_failed` |

A recovering peer that fell into `STATE_DISCOVERING` would lose its host
election standing and its leases, and would reappear to every peer as a
departure and a rejoin — which the `otio-sync-core` spec forbids in as many
words. Nothing is sent on the timeout, so no peer observes the attempt at all.

*Alternative considered:* a separate `request_state_for_recovery()`. Rejected —
the send is byte-identical; duplicating it to vary one timeout branch is how the
two paths drift.

### D4 — Coalesce on the poll thread, not at the refusal

`_note_structure_diverged` sets state and returns. It never starts a rebuild.
`tick()` starts recovery when the divergence has been quiet for
`DIVERGENCE_SETTLE` (0.5 s) and no request is already in flight.

Deferring to `tick()` is not only about coalescing. The refusals arrive inside
the host application's own callback — an xStudio event handler, an RV
graph-change callback — and re-entering a full snapshot apply from inside one is
exactly the reentrancy both plugins guard against elsewhere. `tick()` is already
where the manager does its other deferred work (master failover, peer ageing,
the request timeout).

0.5 s is chosen against the two rates involved: a multi-clip delete refuses one
broadcast per child within a few milliseconds, while a person's second
deliberate delete is an order of magnitude slower. A divergence arriving while
`status != STATE_SYNCED` — i.e. during a rebuild — leaves the flag set and is
picked up on the tick after that rebuild completes, which is the spec's
"divergence during a rebuild is not lost".

### D5 — Lease refusals share the trigger, with a cooldown

This settles the proposal's open scope note. **They share it.**

A lease-refused structural mutation leaves the peer exactly as diverged as a
role-refused one: the local model changed and the session was not told. Kind is
identical; only *rate* differs, because two contending peers can refuse
repeatedly where a role refusal is a stable property of the session. Excluding
the lease case would leave a silent permanent divergence in the precise
situation this change exists to remove.

Rate is handled where rate belongs — in D4's debounce, plus a
`RECOVERY_COOLDOWN` (5 s) minimum between successive rebuilds. Under sustained
contention the peer degrades to a slow periodic re-check that stays truthful in
the panel, rather than a rebuild loop.

Note this path is dormant on landing: `ownership_enforcement_enabled()` is off
by default, so `_owns_channel` returns `True` for every peer. It arrives with
that switch, already covered by tests.

*Alternatives considered:* exclude the lease case (rejected — silent divergence);
defer recovery until the lease is observed released (rejected — needs a third
"diverged, waiting" state, and the peer reports itself healthy in the meantime,
which is the failure mode the change is about).

### D6 — Scope stays at the three child sites

Timeline-level structural refusals are excluded, matching the proposal and the
spec scenarios. The reason is not symmetry but provenance: the child sites are
reached from user edits, while `broadcast_add_timeline` is also driven by each
plugin's own discovery scan (`sequence_sync.py:402,445`;
`structure_sync.py:1353,1375`). There a refusal frequently means "this peer
noticed a local timeline it may not publish", not "the user changed something" —
and since every rebuild wipes and re-adds `_timelines`, treating those as
divergence risks a scan/rebuild loop on any restricted peer at startup. Recorded
as follow-up work, not as an oversight.

### D7 — One projected condition, derived once, rendered twice

The manager holds the minimum:

- `structure_diverged: bool` — set by `_note_structure_diverged`, cleared **only**
  by a successful `apply_snapshot` that a recovery requested.
- `_recovery_in_flight` / `_last_recovery_failure` — the bookkeeping D3 and D4
  need.

`session_state_snapshot()` derives a single nullable string,
`structure_divergence` ∈ `None | "recovering" | "unrecoverable"`, so both panels
switch on one value and cannot disagree about what "being repaired" means. This
follows `driverless` (`session_state.py:151`), derived centrally for that exact
reason, rather than exporting three booleans and asking two QML files to combine
them identically.

`ui_model.py` exposes it as a notifying `Property`, and both
`SessionStatePanel.qml` files render the two conditions distinguishably — an
in-progress resynchronisation needs no user action, while an unrecoverable
divergence means this peer must not be reviewed from.

### D8 — Every failure keeps the peer, the session, and the content

- **No master** (`master_guid` unset, or this peer *is* master): send nothing,
  report `unrecoverable`, stay diverged, retry from `tick()` when a master
  appears.
- **Timeout**: D3's recovery branch. Diverged, in the session, `unrecoverable`
  until the next attempt, retried after `RECOVERY_COOLDOWN`.
- **Never** `close()`, and never clear `_timelines` outside an `apply_snapshot`
  driven by a snapshot that actually arrived.

The asymmetry is deliberate: over-reporting divergence costs a needless rebuild
of state the peer could have kept, while under-reporting it hands the user a
session they believe is shared and is not.

## Risks / Trade-offs

- **A rebuild restores what the user just deleted; they delete again.** →
  D7's panel condition is why the UI surfacing is a requirement rather than a
  nicety, and D5's cooldown keeps the cycle slow rather than tight. Not fully
  solvable here: the correct outcome for a synchronised session is a surprising
  one for a person.
- **xStudio reads its own rebuild back as fresh user edits and re-diverges.**
  `structure_sync.py`'s `_structural_mutation_suppress_until` (1.5 s) windows
  exist for exactly this and are opened by every other apply path
  (`structure_sync.py:1474,2273,2345,2396,2531`). → The recovery rebuild must
  open one too; without it the change is self-sustaining. This is the single
  most likely way the work fails in a live session, and it is a task, not a
  note.
- **The `STATE_JOINING` window silences this peer.** Every `broadcast_*`
  early-returns on `status != STATE_SYNCED`, so an annotation drawn during the
  round-trip is dropped (as `SUPPRESSED`, correctly *not* marked diverged).
  → Accepted: the window is one snapshot round-trip, and the alternative is
  broadcasting from a peer whose structure is known wrong.
- **A diverged master serves its divergence to every future joiner.** It cannot
  request state from itself, so it reports `unrecoverable` and stops there. →
  Out of scope; the spec's "no peer can serve state" scenario covers the
  behaviour, and handing mastership away on divergence is a separate change.
- **False divergence from annotation traffic.** `insert_child` carries both. →
  D1's `group == STRUCTURE` gate, with a spec scenario and a test that fails if
  the gate is removed.
- **`on_synced` firing mid-session surprises a future handler.** → Its contract
  is documented as part of this change rather than left as a docstring that has
  quietly become false.

## Migration Plan

No wire change and no migration step. A peer running older code answers a
mid-session `STATE_REQUEST` exactly as it answers a joining one — `_h_state_request`
does not inspect the requester's status — so a mixed session recovers as soon as
one peer runs the new code.

The behaviour is reachable only where a refusal already happens: a session whose
peers are all drivers, with lease enforcement off, is byte-identical to today.
Rollback is a revert; nothing persists to disk and no peer's state depends on
another peer having the change.

## Open Questions

- ~~`DIVERGENCE_SETTLE` (0.5 s) and `RECOVERY_COOLDOWN` (5 s) are reasoned from
  the refusal rates above, not measured.~~ **Resolved (task 7.9, 2026-08-16):**
  measured refusal→`STATE_REQUEST` gaps of 0.510s and 0.522s across two live
  peers (RV and xStudio) — both landing one tick-loop pass past the coded 0.5s
  deadline, exactly as designed. No live case called for widening (a burst
  triggering twice) or narrowing (perceptible lag). `RECOVERY_COOLDOWN` wasn't
  independently stressed live, but showed no retry storm in the no-master path
  (task 7.6); backed otherwise by `test_sustained_contention_is_paced_by_the_cooldown`.
  Both values kept as originally chosen.
