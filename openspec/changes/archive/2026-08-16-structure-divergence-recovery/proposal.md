## Why

`session-roles` stops a non-driver from *broadcasting* structure. It does not,
and by design cannot, stop that peer from *making* the change locally — the
model is send-side only, a cooperative trust boundary rather than a lock on the
host application's own UI. The gap that leaves was measured live on 2026-08-13
(`session-roles` task 11.3): an xStudio viewer deleted a clip, the broadcast was
correctly refused, and the deletion still took effect two milliseconds later.

```
12:11:01.975  broadcast_remove_child: suppressed — role 'viewer' may not emit structure
12:11:01.977  flat playlist deleted media: 'seq_A' removed
```

From that moment the two peers held different sessions and **nothing brought
them back**. Eight seconds later the driver selected into material the viewer no
longer had, and the viewer logged ten `RECV playback state: mismatched
timeline_guid — ignoring (not playing)` in two seconds. Refusing to guess is the
right local behaviour; having no repair path is not. A reorder produces the same
split more quietly — no error at all, just two peers whose clip order disagrees
until someone notices on a screening call.

The session already owns the primitive that fixes this. `request_state()` /
`apply_snapshot()` is how every joining peer builds its world from the master,
and it is the most-exercised path in the codebase. This change wires it to the
one moment we know for certain that a peer's structure diverged.

## What Changes

- A **suppressed structural mutation becomes a divergence signal**, not just a
  refusal. The sites that already refuse to broadcast structure —
  `insert_child`, `broadcast_move_child`, `broadcast_remove_child` — are the
  exact and complete set of points at which this peer changed structure it was
  not permitted to change. No new detection machinery is introduced; the
  existing refusals gain a second consequence.
- The diverged peer **rebuilds from the master's snapshot** rather than
  attempting a diff or an inverse patch. Whatever the local edit was, the
  master's state is authoritative and re-applying it is already a proven
  operation.
- Recovery is **coalesced and debounced**. Deleting a multi-clip selection fires
  one refusal per child; that must produce one rebuild, not one per clip.
- Recovery **degrades rather than fails** when there is no master to ask
  (a driverless or masterless session, or a snapshot request that times out).
  The peer stays diverged, says so, and remains eligible to recover later — it
  does not wedge, and it does not silently pretend to be in sync.
- The peer's divergence state becomes **observable in the session UI**, for the
  same reason `session-roles` surfaced the role: a user who cannot see that
  their session is diverged has no way to tell a stale view from a live one.
- **Scope note, to be settled in design:** a structure broadcast blocked by a
  *lease* (`broadcast-ownership`) rather than a *role* produces byte-identical
  divergence. The trigger is specified over "could not be broadcast" rather than
  "was refused by role", which covers both — but the lease case is a transient
  race between two peers who may both legitimately write, and rebuilding on it
  may be the wrong reflex. Design decides whether the lease case shares the
  trigger, is debounced far harder, or is excluded.

## Capabilities

### New Capabilities

- `structure-divergence-recovery`: a peer that mutates structure it may not
  broadcast SHALL detect that it has diverged, rebuild from the master's
  snapshot, and surface the condition when it cannot.

### Modified Capabilities

- `otio-sync-core`: the structural broadcast refusals gain a divergence
  consequence, and snapshot re-request becomes reachable from the synchronised
  state rather than only from joining.
- `session-state-ui`: the session state projection gains a diverged/recovering
  condition, and both host panels surface it.

## Impact

- **Depends on `session-roles`** landing (unarchived at time of writing): the
  role refusals it added at the three structural sites are the primary trigger.
  The change is implementable against lease refusals alone if that ordering
  changes, but the motivating case is the role one.
- `python/otio_sync_core/manager.py` — the three structural broadcast sites, a
  coalescing recovery trigger, and re-entry into `request_state()` from
  `STATE_SYNCED`.
- `python/otio_sync_core/session_state.py`, `ui_model.py`, and both
  `SessionStatePanel.qml` files — the diverged condition.
- Both plugins' `structure_sync.py` — a rebuild arriving while the local
  application is mid-edit is the disruptive case, and the existing
  `_structural_mutation_suppress_until` settle windows interact with it.
- No wire-format change. `STATE_REQUEST` / `STATE_SNAPSHOT` are used as they
  stand; a peer running older code answers a mid-session request exactly as it
  answers a joining one.
- **Risk to weigh in design:** a rebuild restores what the user just deleted, so
  a user who deletes twice sees their edit undone twice. That is the correct
  outcome for a synchronised session and a surprising one for a person, which
  makes the UI surfacing part of the requirement rather than a nicety.
