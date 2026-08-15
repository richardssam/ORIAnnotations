## Why

Visibility is the last broadcast category still gated by a **fixed elected
seat**. Position, display and structure are all leased — whoever acts takes the
category, holds it briefly, and hands it on when they stop. Visibility alone
asks who was *elected*, and the answer does not change when someone starts
working.

The consequence is that two drivers do not behave like two drivers. Both can
scrub, both can annotate, both can reorder — but only one can change the shot,
and the other's selections vanish with no error at either end. Observed
2026-08-13 16:36 with two xStudio peers, both `driver`:

```
16:36:45.278  elect_host: cd424a82 → 933a7c1c (self=follower, peers=2)
16:36:49.428  broadcast_playback_state: visibility stripped (not host) mode='sequence' clip=-
```

The user was driving `cd424a82`. Their scrub propagated — position is a lease,
and the active user wins it — so the session followed their playhead **onto a
shot they could not change**. Position and visibility disagreeing about who is
driving is not a corner case; it is the normal state whenever the person working
is not the elected host.

`session-roles` sharpened this rather than causing it. A session can now declare
two drivers and mean it, and the visibility seat is the one place that
declaration has no effect. The election tie-break was fixed separately (the
master now breaks ties, so the seat stops moving on a GUID coin flip), but a
deterministic seat on the wrong machine is still the wrong machine.

## What Changes

- **Visibility becomes a leased category**, alongside position, display and
  structure. Selecting a clip or changing view mode claims it; the claim expires
  the way every other lease does. The category stays single-writer at any
  instant — leasing changes *who* the writer is, not *how many*.
- **Election is retained, with a narrower job**: it decides who holds visibility
  when nobody has claimed it — session start, and after a holder leaves — and it
  keeps supplying the eligibility filter (`session-roles` D4) and the driverless
  indicator. It stops being the standing answer to "who may change the shot".
- **A claim by an eligible peer takes the category from an idle holder.** This
  is the point of the change and the one place visibility must differ from
  position's ordering rule: position prefers the *earlier* claim, which is
  correct for a category where two peers scrubbing at once is a conflict to
  settle. For visibility, the later claim is the user who just acted, and
  refusing it is what leaves someone unable to change the shot.
- **Role remains the ceiling.** A reviewer or viewer may not claim visibility at
  all, exactly as they may not claim structure — checked before the lease, per
  the composition rule `session-roles` D8 already establishes.
- **The holder is visible in both session panels.** A category that moves
  between users silently is worse than one that never moves: the failure this
  change fixes was invisible precisely because nothing reported who held the
  view.
- **Backward compatible by construction.** A peer running code that predates the
  visibility lease keeps deferring to the elected host, and never claims. Its
  claims are absent rather than contradictory, so a mixed session degrades to
  today's behaviour rather than fighting.

## Capabilities

### Modified Capabilities

- `session-visibility-authority`: authority over visibility becomes a lease held
  by the peer that most recently asserted a view, rather than a fixed elected
  seat; election's remit narrows to the unclaimed case; the "non-owner never
  infers local user intent" rule keys on holding the lease rather than on being
  host.
- `broadcast-ownership`: `visibility` joins the leased channels, with a claim
  resolution rule that deliberately differs from position's — the later claim
  wins, because the later claimant is the user who just acted.
- `otio-sync-core`: `_enforce_visibility` gates on the lease rather than
  `is_host`; the visibility claim is refused when the peer's role forbids the
  category.
- `session-state-ui`: the session panel reports who currently holds the view.

## Impact

- **Depends on `session-roles`** (51/51, unarchived at time of writing) for the
  role gate on claims, and on `broadcast-ownership` for the lease machinery.
- `python/otio_sync_core/authority.py` — `CHANNEL_VISIBILITY`, its addition to
  the leased channels, and a resolution rule that is *not* shared with position.
- `python/otio_sync_core/manager.py` — `_enforce_visibility`, `claim_category`,
  `elect_host`, and the `owns_visibility` predicate both plugins call.
- Both plugins' `playback_sync.py` — every site that claims on a local view
  change, and the follower-side apply.
- **Risk: view thrash.** Two users selecting alternately would swap the shot
  back and forth, and a visibility change is far more disruptive than a frame
  seek — it reloads media. Position tolerates rapid hand-off because a wrong
  frame is corrected by the next message; a wrong *shot* costs a reload at both
  ends. The lease duration and any hold-off are the substance of the design, not
  a tuning detail.
- **Risk: `is_host` is load-bearing in more places than visibility.** It is read
  by the driverless indicator, the "Become Controller" gate, and several plugin
  paths. Separating "elected host" from "currently holds the view" needs those
  call sites audited individually rather than renamed in bulk.
- No new message type: `CLAIM_OWNERSHIP` already carries a category.
