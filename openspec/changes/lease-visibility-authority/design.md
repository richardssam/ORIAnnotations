## Context

See `proposal.md` — Why. The mechanism this change reuses already exists and
its shape constrains the approach:

- `authority.LEASE_CHANNELS` is `(position, display, structure)`, each with a
  duration in `LEASE_DURATIONS`. `visibility` is deliberately absent, documented
  as "a static single writer (no contention to resolve)".
- `SyncManager._leases[channel]` holds `owner_guid`, `claim_ts`, `deadline`,
  `confirmed`, `pending_claimant`. Expiry is lazy (`_settle_lease_expiry`),
  applied on access.
- `authority.resolve_claim(current, incoming)` is `min()` over `(claim_ts, guid)`
  — one shared rule for all three channels, called from `_apply_claim` for both
  local and received claims.
- `_apply_claim` protects a **confirmed** owner: an incoming claim is queued as
  `pending_claimant` rather than granted, and promoted at expiry. `confirmed` is
  set by `_refresh_lease_confirmed`, called when a broadcast in that category
  actually goes out.
- `_enforce_visibility` gates on `self.is_host` and strips `VISIBILITY_FIELDS`
  as a group, returning `SUPPRESSED`.
- `claim_category` already refuses when `_role_permits(channel)` is false, and
  already no-ops under `ORI_BROADCAST_OWNERSHIP=0`.
- `_lease_wire_section` / `adopt_ownership` iterate `LEASE_CHANNELS`, so a new
  channel joins the snapshot and its adoption without new wire code.
- `session_state_snapshot` already emits `holds_position_lease` /
  `holds_display_lease` / `holds_structure_lease` per peer, and `is_host`.

Two properties of the existing machinery are load-bearing for what follows.
First, a claim must resolve **identically on the claiming peer and on every
receiver**, or the session gets two writers. Second, `confirmed` is *not* such a
property: it is set locally when a broadcast leaves, so a remote owner is
`confirmed` on its own machine and unconfirmed everywhere else. Today that
divergence is harmless because `min()` makes the incumbent's earlier `claim_ts`
win on the receivers anyway. It stops being harmless the moment a category
prefers the later claim.

## Goals / Non-Goals

**Goals:**

- Visibility resolution that is a **pure function of the two claim tuples**, so
  the claimer and every receiver reach the same holder without depending on
  `confirmed`.
- A hand-off rule that is damped: deliberate re-selection wins, a burst does not.
- Election retained unchanged as the unclaimed-case fallback, so a session with
  no claims behaves exactly as it does today.
- A two-stage rollback: revert to the elected seat, or revert to symmetric,
  without a rebuild.

**Non-Goals:**

- No change to how a view is *applied* on the receiving side. This change moves
  who may assert a view, not what happens when one arrives.
- No new message type, no new wire section, no change to `CLAIM_OWNERSHIP`.
- No bulk rename of `is_host`. Elected host and visibility holder become two
  distinct things and both keep their own name.
- Not making the mixed-version case correct. It is made *no worse*; see Risks.

## Decisions

### D1 — `visibility` joins `LEASE_CHANNELS`; election becomes the unclaimed fallback

`_enforce_visibility` resolves in this order:

```
visibility kill switch off        -> SENT           (symmetric, unchanged)
ownership kill switch off         -> is_host ? SENT : STRIP   (pre-change behaviour)
lease owner is self               -> SENT
lease owner is None and is_host   -> SENT
otherwise                         -> STRIP, SUPPRESSED
```

The fourth line is the whole backward-compatibility story: a peer that never
claims leaves the channel free, the free channel falls to the elected host, and
the session behaves as it did before. It also covers session start and the
window after a holder's lease expires.

*Alternative rejected:* have `elect_host` claim the lease on election. That
makes the seat sticky again — an elected host would hold a lease it never
earned, and every other driver would have to preempt an incumbent rather than
take a free channel. Falling back on absence keeps election a pure function and
keeps the lease meaning "someone is actually driving the view".

### D2 — Resolution is per-category; visibility prefers the later claim

`resolve_claim` gains a `channel` argument and dispatches through a table rather
than growing a branch, so the two rules cannot be edited into each other:

```python
CLAIM_RESOLVERS = {
    CHANNEL_POSITION:   _resolve_earlier,
    CHANNEL_DISPLAY:    _resolve_earlier,
    CHANNEL_STRUCTURE:  _resolve_earlier,
    CHANNEL_VISIBILITY: _resolve_visibility,
}
```

The existing two-argument call shape keeps working and keeps meaning
earlier-wins, so no current call site changes behaviour by omission.

*Alternative rejected:* a boolean `prefer_later=` flag. It reads as a tuning
knob on one shared rule, which is precisely the coupling the `broadcast-ownership`
spec asks to avoid ("a change to one category's rule cannot silently alter
another's").

### D3 — Ordering is later-wins and nothing else; the hold-off moves to the claim site

Resolution is a plain total order, the mirror of earlier-wins:

```python
def _resolve_visibility(current, incoming):
    if current is None:
        return incoming
    return min(current, incoming, key=lambda c: (-c[0], c[1]))
```

Damping lives at the claim site instead, as a decision one peer makes about
whether to emit at all:

```python
def claim_withheld_by_holdoff(channel, owner_guid, owner_claim_ts, self_guid, now):
    holdoff = CLAIM_HOLDOFFS.get(channel)          # visibility only
    if not holdoff or owner_guid in (None, self_guid) or owner_claim_ts is None:
        return False
    return now < owner_claim_ts + holdoff
```

**A tolerance comparison cannot live inside resolution, because it is not
transitive.** `_apply_claim` resolves each claim against whatever the lease
currently holds, so a peer *folds* a sequence of claims — and peers fold in
different orders, because `claim_category` applies this peer's own claim before
broadcasting it while every other peer sees it arrive among the rest. A fold
converges only if the operation is associative. An earlier draft of this design
put the hold-off in the resolver, and three claims at 0.0/1.0/2.0 s resolved to
a different winner under each rotation:

```
[0.0:aaa, 1.0:bbb, 2.0:ccc] -> ccc
[1.0:bbb, 2.0:ccc, 0.0:aaa] -> aaa
[0.0:aaa, 2.0:ccc, 1.0:bbb] -> bbb
```

Two peers landing on different owners both believe they hold the category and
both broadcast a view — the single-writer requirement the capability opens with,
gone. Pairwise purity is necessary but not sufficient; the operation has to be a
total order. `test_resolve_claim_is_order_independent` asserts this for every
channel.

Moving the hold-off to the claim site keeps all three hand-off scenarios:

- *A new selection takes the view from an idle holder* — the incumbent's claim
  is older than the hold-off, so the challenger emits, and later-wins grants it.
- *An active holder is not interrupted mid-action* — an active holder re-claims
  on each local view change, pushing `owner_claim_ts` forward, so a challenger
  finds itself inside the window and does not claim. The spec's "'most recent'
  cannot be read as 'whoever spoke last in a burst'" is satisfied a fortiori: a
  claim that is never made cannot be honoured.
- *Hand-off does not oscillate* — a swap still costs at least
  `VISIBILITY_HOLDOFF` per direction.

It also confines the clock-skew exposure. A skewed challenger withholds a claim
it could have made, or makes one it could have withheld; either way every peer
then folds the identical set of claims that were actually sent, so skew costs at
most a delayed hand-off rather than a divergence.

Resolution reading only timestamps is what lets visibility **bypass the
`confirmed` branch** in `_apply_claim`. `confirmed` is set locally when a
broadcast leaves, so a remote owner is confirmed on its own machine and
unconfirmed everywhere else; under earlier-wins that divergence is masked
(the incumbent's earlier stamp wins on the receivers anyway) and under
later-wins it would produce two writers. Position/display/structure keep
`confirmed` exactly as it is.

*Alternative rejected:* bucketing timestamps into hold-off-wide quantiles to
recover associativity. It is a total order, but the boundary phase decides how
much protection an active holder gets — anywhere from zero to the full
`VISIBILITY_HOLDOFF`, at random.

Note both resolvers break an exact-timestamp tie to the lower GUID; the key
`(-claim_ts, guid)` inverts the ordering without inverting the tie-break, which
keeps the house convention used everywhere else in `authority.py`.

### D4 — Only an input-driven claim refreshes the visibility lease; a broadcast does not

`_enforce_visibility` must **not** call `_refresh_lease_confirmed`, unlike
`_enforce_position`.

Position can confirm from its own traffic because a position message is caused
by the user moving. A playback message carries `view_mode`/`clip_guid` on *every*
send — both plugins fill them in from `_cur_view_mode` / the current clip as
context, not as an assertion of change. A lease refreshed by that heartbeat
would never expire, no challenger would ever find an idle holder, and the elected-
seat failure would return wearing a lease.

So the visibility lease is extended by exactly one thing: `claim_category(
CHANNEL_VISIBILITY)` from a path caused by local user input. Idle means "this
user has not changed what they are looking at", which is the question the
capability actually asks.

### D5 — Durations

```python
LEASE_DURATIONS[CHANNEL_VISIBILITY] = 3.0
VISIBILITY_HOLDOFF = 1.5
```

`VISIBILITY_HOLDOFF` is bounded below by a user's own multi-step selection —
picking a clip and then switching to source mode is two claims from one
intention, and they must not be treated as a contest — and bounded above by how
long a user will watch their own selection do nothing before concluding it is
broken. 1.5 s sits between those.

The lease is the longest of the four (structure is 2.0 s) because reverting
visibility is the most expensive thing a lease expiry can cause: a wrong frame is
corrected by the next message, a wrong shot costs a media reload at both ends.
`LEASE_DURATIONS[visibility] > VISIBILITY_HOLDOFF` is required, or a lease could
expire while its holder was still protected.

### D6 — Claim sites are the existing single funnels

Each plugin already funnels a local view assertion through one method —
`PlaybackSync.broadcast_view_state(clip_guid, view_mode)` in both
`rvplugin/ori_sync/playback_sync.py` and
`xstudio_plugin/ori_sync/playback_sync.py`. Claim there, on the input-driven
branch only (a claim from an applied remote message is the feedback loop
`claim_category`'s contract forbids).

The audit of those callers found two things worth recording:

- **Both plugins' callers are already guarded.** Each of xStudio's four call
  sites tests `_is_remote` from `_provenance()` first; RV's all sit behind
  `_rv_updating`. Provenance is a settle-window heuristic rather than a proof,
  so the second line below is worth keeping.
- **Not every caller of the funnel is asserting a view.** RV's
  `on_selection_changed` reports a highlight, which its own comment calls "a
  loop/highlight concept, not a view switch". It passes `asserts_view=False` and
  rides whatever lease this peer already holds. The parameter, not the call
  site, is where the distinction is recorded, so a new caller has to answer it.

xStudio's `broadcast_view_state` asks **nothing** about whether the call was a
local user action, and must not. Provenance is settled at the call sites, all
four of which gate on `_is_remote`.

An earlier revision of this change kept an `owns_visibility()` sample there as
defence in depth, taken before the claim so it did not merely report the claim
just made. That was wrong, and instructively so. The isolation block resets the
local playhead to 0 unconditionally, so gating only the *announcement* on a
guess breaks the one invariant that block has to hold — the frame you move to
and the frame you announce are the same frame. The guess also answered "not
local" for the first clip any peer isolates, since a peer cannot already hold a
lease it is in the act of taking. Observed 2026-08-14 20:38:57: a peer isolated a
clip, reset itself to 0, announced `frame=75`, and sat 75 frames from the session
with nothing to re-align them.

The general rule this leaves: a guard that fires on the most ordinary case it
will ever see is not defence in depth, it is a second failure mode. Where
provenance is already established upstream, re-deriving it downstream from
authority state is not a cheap extra check — the two answer different questions
and will disagree.

### D7 — `is_host` is audited, not renamed

Elected host and visibility holder are now different questions, and every
existing reader wants one or the other:

| Site | Wants |
| --- | --- |
| `owns_visibility()` — both plugins' local-intent branches | **holder** |
| `_enforce_visibility` | **holder** (with host as fallback, D1) |
| `has_eligible_driver` / driverless indicator | elected host — unchanged |
| "Become Controller" gate (both plugins) | role, not host — unchanged |
| `elect_host` / `adopt_host` / host callbacks / logs | elected host — unchanged |
| `session_state_snapshot["is_host"]` and per-peer `is_host` | elected host — unchanged |

`session_state_snapshot` gains `holds_visibility` per peer and
`self_holds_visibility` / `may_hold_visibility` at the top level, beside the
existing `holds_*_lease` flags. Reported as the **effective** holder — lease
owner if any, else the elected host — so the panel never shows "nobody" for a
session that has a working answer. `is_host` stays what it is; the panel gets a
new fact rather than a redefined one, which is what `session-state-ui`'s "three
separate things" rule requires.

### D8 — Role gate needs no new code, only a test

`claim_category` already refuses when `_role_permits(channel)` is false, and
`CHANNEL_VISIBILITY == "visibility" == authority.VISIBILITY`, the key
`ROLE_PERMISSIONS` is already keyed on. The refusal is already a refusal rather
than a release. The work here is proving it, not writing it.

## Risks / Trade-offs

- **View thrash between two active drivers** → `VISIBILITY_HOLDOFF` bounds the
  swap rate to one hand-off per 1.5 s per direction, and a hand-off costs a
  reload only when the new holder actually broadcasts a *different* view.
  Residual: two users deliberately fighting will still fight. That is a session
  problem, and the panel from `session-state-ui` is what makes it visible.

- **A local event that is not a view change claims the category** → `clip_guid`
  is a visibility field, so any path that reaches a claim starts asserting what
  the session looks at. RV's native selection is a highlight, not a view switch,
  and claiming there let a highlight pull every peer onto that shot.
  `broadcast_view_state(..., asserts_view=False)` marks the paths that report a
  view without asserting one. Each new claim site needs the same question asked
  of it: is this the user changing the shot, or merely touching something?

- **Clock skew decides claims** → `claim_ts` is wall-clock `time.time()`. Under
  `min()` a slow clock wins; under later-wins a fast clock wins. The problem is
  symmetric and pre-existing, and skew large enough to matter here already
  breaks position sync. What ordering does still guarantee under skew — and what
  convergence actually needs — is that every peer reaches the *same* winner from
  the same set of claims. Per D3, the hold-off's own skew exposure costs at most
  a delayed hand-off. Not otherwise addressed here.

- **A departed peer's lease outlives it** → election only decides visibility
  while no peer has claimed, so a holder that leaves would freeze the view for
  the full lease duration — the failure `drop_peer`'s re-election exists to
  prevent, arriving by another route. `drop_peer` therefore releases every lease
  the departing peer held, through the normal expiry path so a queued claimant is
  granted by the usual rule.

- **A mixed session where the *old* peer is the elected host** → it never claims,
  never honours a `visibility` claim (`_h_claim_ownership` filters on
  `LEASE_CHANNELS`), and keeps broadcasting visibility as host. A new peer that
  takes the lease then broadcasts too: two writers, settling last-write-wins on
  the view. This is not a regression — it is what the old peer does today — but
  it is not fixed either. Upgrade both peers; the honest statement is that
  `visibility` is single-writer among peers that understand the channel.

- **`_apply_claim` grows a per-channel branch** → the `confirmed` protection now
  applies to three channels and not the fourth. Mitigation: the branch is
  expressed as "this channel resolves on timestamps alone", named and documented
  at the `CLAIM_RESOLVERS` table, not as an `if channel == "visibility"` buried
  in the method.

- **Idle expiry visibly returns the view to the host** → after 3 s of no view
  changes the lease frees and the panel shows the elected host holding it again.
  Correct per spec, but it will read as flapping to a user watching the panel.
  Mitigation: the panel reports the effective holder, so the transition is
  host→user→host rather than host→user→nobody→host.

## Migration Plan

1. Core first, dark: add `CHANNEL_VISIBILITY`, the resolver table, the duration
   and hold-off. Nothing claims yet, so the channel is free everywhere and D1's
   fallback means behaviour is unchanged.
2. Switch `_enforce_visibility` and `owns_visibility()` to the D1 predicate.
   Still no behaviour change while nothing claims.
3. Wire `claim_category(CHANNEL_VISIBILITY)` into both plugins' view-change
   paths. This is the step that changes behaviour.
4. Session-state projection and both panels.

**Rollback**, in order of bluntness, neither needing a rebuild:
`ORI_BROADCAST_OWNERSHIP=0` reverts visibility to the elected seat (and the
other three channels to unconditional, as it does today);
`ORI_VISIBILITY_AUTHORITY=0` reverts to symmetric visibility.

## Open Questions

- Whether 3.0 s / 1.5 s survive a two-driver soak, or want to be 2.0 s / 1.0 s.
  Deferrable: the values are two constants in `authority.py` and no spec,
  interface, or task depends on which one is right.
