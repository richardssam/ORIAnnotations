## Context

See proposal.md — Why. Relevant current state:

- `SyncManager` already owns every `broadcast_*` method and the master-election state machine, and `session-roles` D1 already identifies it as the single enforcement point. This change uses that point, so the two remain compatible.
- `session-roles` defines two categories — **navigation** (playback, display, selection) and **structure**. That grouping is too coarse here: it forces "may scrub" and "may change what is on screen" to be the same permission, which is exactly the split the product needs.
- The existing `is_master` concept is a *state-sync* role (snapshot authority), not a permission. `session-roles` D8 notes the overlap; this change keeps them separate and introduces host as a distinct, explicit concept rather than overloading master.
- RV's `on_view_changed` broadcasts visibility whenever the local view node changes, including when that change was itself caused by applying a remote message. xStudio's PSM handler does the same for isolation. Both are symmetric-authority behaviours.

## Goals / Non-Goals

**Goals:**
- One peer decides what everyone looks at; everyone can still scrub, play, stop and annotate within it.
- Delete the need to infer whether a local transition was user-caused, by removing the situation in which a non-owner would act on that guess.
- Keep the enforcement in one place so the two plugins cannot drift, as they already have on hand-replicated protocol behaviour.
- Remain forward-compatible with `session-roles`: same choke point, same vocabulary, so leases can be added later without redoing this.

**Non-Goals:**
- Locking a follower's local UI. A follower may still move its own view locally; it simply does not broadcast that, and a subsequent host update will move it back. Local divergence with snap-back is accepted, consistent with `session-roles`.
- Dynamic ownership handoff, claims, leases, or contention resolution. Host is elected once per session and changes only on host departure.
- Adversarial enforcement. Send-side only; peers trust each other.
- Structure ownership. Timeline add/remove/replace stays as it is today, gated by `is_master` as now. Folding it in is `session-roles`' job.
- Roles for people (driver/reviewer/viewer). This is authority per *category*, not per participant.

## Decisions

### D1: Three categories, split where the product splits

| Category | Contents | Authority |
|---|---|---|
| **visibility** | which clip/sequence is shown, view mode (sequence vs isolated clip) | host only |
| **position** | playhead position, play/stop, playback mode | any peer |
| **annotation** | strokes, captions, shapes | any peer |

The split falls between `view_mode`/`clip_guid` and `current_time`/`playing` — fields that currently travel in one `PLAYBACK_SETTINGS_1.0` message. So enforcement is per *field group*, not per message type: a follower may send a playback message carrying position, but must not send one that asserts visibility.

*Alternative — separate the wire messages:* cleaner conceptually, but a protocol change affecting every peer and every recording. Rejected; the unified view-state message was itself a recent consolidation (`unify-view-state-sync`).

*Alternative — reuse `session-roles`' single navigation category:* rejected, it cannot express "scrub yes, change the shot no", which is the requirement.

### D2: Host is elected by capability, not hard-coded to xStudio

The requirement is "xStudio drives, unless the session is RV-only". Hard-coding the application name would make an RV-only session hostless. Election therefore prefers a peer that advertises visibility-authority capability, with xStudio ranked above RV; ties break deterministically by guid, as `session-roles` D2 does for claims.

This keeps the common case ("xStudio hosts") without special-casing it, and leaves room for a future explicit "take host" action.

*Alternative — host is always the master:* rejected. Master is the snapshot authority and is elected on liveness/timing grounds; conflating them means a master re-election silently changes who controls the view.

**Follow `fix-discovery-thread-safety`'s discipline exactly.** That change fixed the equivalent problem for master election, and its approach transfers whole:

- **One operation owns the transition.** `elect_self_as_master()` sets every field the election entails, in a documented order, and callers are forbidden from assigning `is_master` / `master_guid` / `status` directly. Host election SHALL have the same shape — an `elect_host()`-style operation, with no call site assembling the sequence itself. Half-applied election state read by another thread is precisely the bug that change removed.
- **Restore the single-writer invariant; do not add locks.** That change's explicit non-goal was "making `SyncManager` thread-safe in general". Host state therefore belongs to the same single writer (the poll thread), with other threads enqueuing rather than mutating.
- **Re-check at drain time, not at enqueue time.** Its handler re-tests `status == STATE_DISCOVERING` when the command is drained, so a master discovered during queue latency cancels the election. Host election inherits the same hazard — two peers electing simultaneously — and the same mitigation.
- **Post-election state must be identical across hosts.** That change started setting `master_guid` on RV specifically because divergent post-election state "matters for `session-roles`". It matters here for a nearer reason: every peer must reach the same host from the same inputs, which is impossible if the two applications leave themselves in different states after electing.

### D3: The follower rule — never infer local intent

**A peer that does not own a category must not infer, from any local state transition in that category, that a user caused it.**

This is the load-bearing rule, and it generalises three separate bugs found on 2026-08-05. Each was an inference that is correct locally and wrong remotely:

- a `Pinned Source Mode` `True→False` transition read as "user double-clicked", broadcasting `playing=true`;
- a new source-clip isolation read as "user isolated this clip", forcing frame 0 over a seek that had already landed;
- a view-node change read as "user switched view", broadcasting the new view.

Under symmetric authority these needed a guess, and the guesses were implemented as time windows around remote applies — the mechanism that kept failing. Under this rule the follower has no reason to guess: it does not broadcast visibility at all, so the question never arises. The host, being the only peer that broadcasts visibility, is also the only peer whose transitions are by definition user-caused.

The practical consequence is that echo suppression for visibility becomes unnecessary rather than better-implemented. Position echoes still need the existing apply-scope guards, because position remains multi-writer.

### D4: Followers mirror visibility; they do not derive it

A follower applies the host's `view_mode` and `clip_guid` directly rather than computing a locally-equivalent view. This is the difference between "RV shows what xStudio is showing" and "RV independently arrives at a comparable view", and it is what removes the divergence class where each peer reaches a different answer from the same inputs — observed as OpenRV isolating onto one clip while xStudio held another, both reporting the same timeline name.

Where a follower genuinely cannot mirror (a clip it does not have), it reports the failure rather than substituting its best local approximation. Silent substitution is what made the wrong-clip case invisible.

### D5: Enforcement returns a status; plugins do not check authority

`broadcast_*` consults the category table and returns `SENT` / `SUPPRESSED`, identical in shape to `session-roles` D1. Plugins never test "am I host" — they call broadcast and may observe the status for logging. This keeps the two hosts from drifting, and means adding leases later changes only the check, not the call sites.

## Risks / Trade-offs

- **[Risk]** A follower's local view drifts from the host's and stays there until the host next broadcasts, since followers no longer self-correct by broadcasting. → **Mitigation**: the host's visibility state is carried in `STATE_SNAPSHOT`, and a follower may request a re-sync. Snap-back on the next host update is the accepted model (as in `session-roles`).
- **[Risk]** RV users lose the ability to change what the group looks at, which is a real workflow reduction if RV is someone's working seat rather than a review seat. → **Mitigation**: scrub, play/stop and annotate all remain available; host election is not permanently fixed. Flagged as a product constraint, deliberately chosen.
- **[Risk]** Field-group enforcement (D1) is subtler than per-message enforcement and easy to get wrong — a follower could omit `view_mode` yet still carry a `clip_guid`. → **Mitigation**: strip visibility fields in one place in core, not at each call site; assert in tests that a follower's broadcasts never carry them.
- **[Risk]** This lands ahead of `session-roles` and could be seen as pre-empting it. → **Mitigation**: same enforcement point, same vocabulary, no protocol change that Phase 1 would have to undo. If `session-roles` lands later, this becomes a static policy under its lease mechanism.
- **[Trade-off]** Retiring `xstudio_selects_script_rv` removes coverage of RV-driven selection — which is precisely the behaviour being removed, so the loss is intentional, but it does reduce the suite's RV-side selection coverage until an RV-hosted equivalent exists.

## Migration Plan

1. Add the category table and `SENT`/`SUPPRESSED` status to `SyncManager.broadcast_*`, enforcement **disabled** — status always `SENT`. No behaviour change.
2. Add host election (D2) and expose the elected host in `STATE_SNAPSHOT` and in the test inspector's state, so the suite can assert who holds visibility.
3. Enable enforcement behind an env kill switch, mirroring `session-roles` D5, so a bad split can be reverted in a live session without a rebuild.
4. Follower changes: RV stops broadcasting visibility and mirrors instead (D4); xStudio's intent inferences become host-only paths.
5. Only then delete the visibility-related echo guards made unnecessary — a separate, revertible commit, as `session-roles` D5 argues.
6. Retire `xstudio_selects_script_rv`; confirm `xstudio_selects_script_xstudio` covers the intended topology.

## Open Questions

- Does "host" warrant a user-visible affordance (an explicit "take control" action), or is election sufficient for now?
- Should a follower's local visibility change be actively snapped back on the next host broadcast, or left until the host next changes something? The former is more predictable, the latter less jarring.
- `display_state` (channel, colour) is currently grouped with navigation in `session-roles`. It is neither visibility nor position — does it follow the host, or stay per-peer? Leaning per-peer, since reviewers legitimately toggle channels locally.
- Where does the annotation *frame context* sit? Annotating implies a frame, which is position — but position is multi-writer, so two peers could annotate different frames simultaneously. Probably fine; worth confirming against how bookmarks resolve.
