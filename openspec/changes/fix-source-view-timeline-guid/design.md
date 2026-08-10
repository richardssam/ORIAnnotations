## Context

See proposal.md for motivation. Two existing pieces of machinery matter here:

- `_broadcast_playback()` resolves the guid it puts on the wire as `_rv_node_to_timeline_guid.get(view) or sync_manager.active_timeline_guid`. That map is populated only for sequence groups, so a source view always falls through to the second term.
- `_displayed_view()` already answers the same question correctly for the *apply* path: for an `RVSourceGroup` it reads `manager._clip_timelines`, otherwise it uses the node map and then walks `_otio_guid_to_root` for OTIO-origin timelines whose displayed node is the stack's inner sequence group.

So OpenRV has two readers of "which timeline am I showing", and only one of them is right.

Position enforcement is what makes the mislabelling reach a peer at all: a follower's `view_mode`/`clip_guid` are stripped by visibility authority, while `current_time` survives (position is any-peer). The receiving peer therefore sees a bare frame whose only context is the timeline guid.

## Goals / Non-Goals

**Goals:**
- One reader of the displayed timeline, used by both the broadcast and the apply path.
- Preserve sequence-view behaviour exactly, including the OTIO stack fallback.

**Non-Goals:**
- Changing visibility or position enforcement. That a follower's view fields are stripped is correct and stays.
- Changing xStudio. Its mismatch guard already does the right thing once the label is honest.
- Making an isolated clip's position *usable* to a peer that lacks the clip. Not sending a misleading position is the whole fix; conveying the position of an unshared view would need a protocol change.

## Decisions

**0. What implementation changed about decision 1**
- `_displayed_view()` turned out to be wrong for the very case being fixed: its source branch reads `clip_tls.get(view)` with an RV *node name*, while `_clip_timelines` is keyed by *clip guid*, so it resolves `None` for every source view. It is harmless where it stands — the apply path only reads that element inside its sequence branch — but reusing it verbatim would have broadcast `None` from every isolated clip. That stops the observed jump, yet it fails the spec's "an isolated clip is labelled with its own timeline" and reproduces the earlier attempt's blunt cut.
- Resolution: a new `_displayed_timeline_guid()` keeps decision 1's *intent* — one reader, derived from the live view, `None` when unshared — while resolving source mode from `_cur_clip_guid`. That is the clip already being broadcast as `clip_guid` in the same message, so the two fields cannot describe different things, and an unresolvable isolation (`_forget_current_clip`) yields no guid rather than a stale one.
- `_displayed_view()`'s dead source lookup is left alone: fixing it would change what the apply path sees for source views, which is a second variable this change deliberately does not move. Worth its own fix.

**1. Reuse the displayed view rather than extend the node map**
- *Rationale*: The node map is a cache keyed by RV node, maintained by the sequence controller; teaching it about source groups duplicates `_clip_timelines` and adds a second thing to invalidate on view change. `_displayed_view()` derives the answer from the live view node on every call, which is the property that made it the right reader for the apply path.
- *Alternative*: Populate `_rv_node_to_timeline_guid` for source groups too. Rejected — two caches of the same fact, and the apply path would still read the other one.

**2. `None` is a meaningful value, not a fallback failure**
- *Rationale*: A displayed clip with no shared timeline is a real state, and the honest wire representation is "no timeline guid". The peer-side guard already treats a non-matching guid as "not for me". Substituting `active_timeline_guid` is what turns an unshared position into a false claim about the sequence.
- *Consequence*: The message still goes out (the peer learns play state and that this peer is alive); only its authority over the receiver's playhead is withdrawn.

**3. Leave the position broadcast ungated**
- *Rationale*: It is tempting to also suppress the broadcast entirely when the view is unshared. That is a second behavioural change with its own failure mode (a peer that stops reporting looks disconnected), and the guid fix already removes the harm. Keep the change to one variable.

## Risks / Trade-offs

- **Risk**: A sequence view whose guid resolves differently under `_displayed_view()` than under the old node-map lookup would silently change sequence-following behaviour. → Mitigation: `_displayed_view()` consults the same node map first and only adds fallbacks; the sequence case is a superset of the old lookup, and the sequence scenario in the spec is the regression check.
- **Risk**: Peers that were (accidentally) following OpenRV's source-view positions will stop. → Accepted and intended; those positions were being applied in the wrong coordinate space.
- **Risk**: `_displayed_view()` returns `(None, None, None)` when the view cannot be read, where the old code would have substituted the active timeline. → Same treatment as an unshared view: no guid, no false claim.

## Open Questions

- ~~After the first mislabelled jump, subsequent OpenRV view changes were observed to stop affecting xStudio.~~ **Answered** by the 2026-08-10 verification logs. `on_rv_frame_changed` only broadcasts when `current_frame != _last_broadcast_frame`, so a second isolation landing on the same frame number as the first sends nothing at all. Both `seq_B` selections were `sourceGroup000004` at frame 100: the first broadcast (11:20:50.963) and the second (11:20:54.415) did not. Nothing to do with the guid; a separate consequence of the frame-equality guard.

## Verification outcome (2026-08-10)

The fix works as specified — xStudio now rejects OpenRV's source-view positions for the right reason, six times in one session:

```
11:20:56.527 RECV playback state: mismatched timeline_guid (local=b3ab387e, target=b3ab387e, incoming=6432c98b)
11:20:56.527 RECV playback state: mismatched timeline_guid — ignoring (not playing)
```

`6432c98b` is the isolated clip's own timeline; before this change `incoming` would have been `b3ab387e`, the sequence's, and it would have been applied. Unresolvable isolations (`seq_B`, `seq_C`) broadcast `tl=-` as intended.

**Two further defects surfaced, both out of scope here** — see the residual-symptom notes in the follow-up change:

1. *OpenRV*: a frame-changed broadcast can win the race against the view-change handler and describe the **previous** view. The new log field makes it plain: `11:20:50.963 SEND playback ... displayed=source mode=sequence`. Harmless on the wire for a follower (visibility fields are stripped) but it still ships the position.
2. *xStudio*: applying that position moved its playhead, which fired a `show_atom` that xStudio re-broadcast as a *local* selection — even though it had already tagged it `[PROVENANCE remote-induced? source=f9dcd756 ... age=0.05s]`. That re-broadcast is what pulled OpenRV out of the user's `seq_B` isolation at 11:20:51.121.
