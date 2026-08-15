# Echo guards under host-owned visibility

Written as part of the `host-owned-visibility` change (§5.3). Its purpose is to
let the next person tell a **deliberate deletion** from an **oversight** when
reading a later diff — which guard was retired, what replaced it, and which ones
are still load-bearing.

## The principle

Authority does not make echo suppression *better implemented*. What it does is
replace a **guess** with an **answer**:

- **visibility** is single-writer *at any one moment*, but the writer is
  whichever peer holds the visibility lease — not a fixed seat. `view_mode` /
  `clip_guid` are stripped from every non-holder's message at one point in
  `SyncManager.broadcast_playback_state`. The guards that existed to decide "was
  this view change mine or a peer's?" are answered by `manager.owns_visibility()`
  rather than by a time window, but they are **not** vacuous: ownership changes
  hands during a session, so a peer can hold the lease now and have been applying
  a peer's message moments ago.
- **position** is still multi-writer, by design — every peer may scrub, play and
  stop. Its echoes are therefore still real, and every position guard stays.
- **annotation** is still multi-writer and was never gated.

> **Amended 2026-08-15 (`lease-visibility-authority`).** Until this change the
> bullet above read: *"**visibility** is now single-writer. Only the host
> broadcasts `view_mode` / `clip_guid` … A single writer cannot echo with
> itself, so the guards … have nothing left to decide."* Visibility is no longer
> the elected host's seat; it is a leased broadcast category alongside position,
> display and structure, claimable by any peer whose role permits it. The
> stronger claim — *nothing left to decide* — was the part worth removing: it
> licensed reasoning that a peer's own visibility transitions are user-caused
> **by definition**, which is true of a permanent seat and false of a lease. See
> the 2026-08-15 section below for the two guards that were wrong for exactly
> this reason.

The corollary matters as much: a guard that looks like a visibility guard but
actually protects a *position* field is **not** retired by this change. The
frame-0 reset on a new isolation is the clearest example — it is triggered by a
visibility transition but writes `current_time`.

## Status: soaked 2026-08-06 — do not delete

§5.1's precondition was met: a live two-app session, xStudio host+master, OpenRV
follower. **The answer came back "do not delete", and the table below must not be
acted on as it stands.** Every guard is still present in the code.

The three candidates fired **0 times** on both peers. Read alone that says
"inert, safe to remove". It is not, because a guard cannot be shown unnecessary
by a session in which the behaviour it guards is broken by another route — and
it was:

> A follower isolated two clips and correctly broadcast **no visibility at all**
> (284 strips, zero `view_mode` sends). Registering the follower's `ADD_TIMELINE`
> fired the host's own selection machinery, and the host isolated the same two
> clips, in the same order, seconds later — then broadcast that as visibility,
> legitimately, because it is the host.

The enforcement this change delivered does work — the field-level rule held
throughout, and the host correctly rejected the follower's clip-timeline
position messages.

### Retracted 2026-08-09 — the reading above does not hold

The paragraph quoted above was read as falsifying the justification stated
below (*"a host's transitions are user-caused by definition"*), on the grounds
that the host isolated the follower's two clips in the follower's order. That
inference is **wrong**, and the guard-deletion question is no longer blocked
by it.

`graphic` then `laser` is simply the order those clips appear on the Video
Track. Reproduced 2026-08-09 15:04 with the **follower completely idle**: the
host pressed play, crossed two edits, and emitted the same two
`container=timeline` `show_atom`s in the same order. Nothing structural was
received. The host's transition was its own sequence scan-through.

Registering a follower's clip-timeline `ADD_TIMELINE` cannot move the host's
display at all: `SyncManager._h_add_timeline` returns `None` for any timeline
carrying `clip_timeline_for`, so the host application is never notified. That
branch has been in place since 2026-05-25 — it was already closed when the
20:38 session was recorded, so it cannot be what changed either.

What actually happened on 08-06 is reconstructed in
`openspec/changes/archive/2026-08-09-fix-visibility-authority-bypass/evidence.md`:
the follower's position messages drove the *host's play state*, which is the
only thing that sets `_playing_started_at`, which opened the scan-through
guard's 0.3 s exemption, which let the next edit crossing broadcast as a
deliberate isolation. A position echo, surfacing as an apparent clip-follow.

**What this does and does not license.** It removes *this* objection to §5.1.
It is not itself a reason to delete anything: the original zero-fire counts
were gathered in a session whose behaviour we now understand differently, so
they are still not evidence. Deleting these guards needs its own soak, taken
deliberately, against the current code.

## Superseded by host-owned visibility — candidates for §5.1

**Not blocked, not approved — see the retraction above (2026-08-09).** The
objection that blocked this section has itself been withdrawn: the host
transition it cited was sequence scan-through, not a follower's structural
message. What remains is that the zero-fire counts were gathered before that
was understood, so they are not yet evidence for deletion. Take a fresh soak
against current code and decide on that.

These exist only to answer "did a user cause this local visibility transition, or
did applying a peer's message cause it?". Under the follower rule (D3) that
question was held not to arise: a follower does not act on visibility
transitions, and a host's transitions are user-caused by definition.

**The second half of that no longer holds** (2026-08-15). Holding the visibility
lease says a peer *may broadcast*; it does not say the transition in front of it
was user-caused. A peer that has just applied a remote view change may hold the
lease a moment later, and — separately from any peer — a paused scrub across a
sequence produces the same `show_atom` stream as a deliberate isolation. The
question these guards answer is still live; only its *first* half was retired.

| Guard | Where | What it decided | What replaces it |
|---|---|---|---|
| `_playback_apply_suppress_until` read as "a peer is driving", used to withhold the auto-play claim on a Pinned Source Mode `True→False` transition | `xstudio_plugin/ori_sync/playback_sync.py` (PSM handler) | Whether a double-click was local | **Already replaced** — the branch now asks `manager.owns_visibility()`. The deadline itself is still armed and used by the position guards below, so it is *not* deletable; only this *use* of it was retired. |
| The same deadline read as "peer-driven isolation", used to keep the frame on a new source clip | `xstudio_plugin/ori_sync/playback_sync.py` (`broadcast_view_state`) | Whether an isolation was local | **Replaced twice.** It became `manager.owns_visibility()`, which was wrong — see 2026-08-15 below — and is now `_playhead_moving`, which asks the question the frame reset actually depends on: is this an isolation, or a scrub crossing an edit? Ownership was never the right predicate here. |
| `_selection_broadcast_suppress_until` (0.5 s after `_apply_selection`) | `xstudio_plugin/ori_sync/playback_sync.py` | Whether a `show_atom` burst was the echo of an applied remote selection | Follower selection broadcasts no longer assert visibility, so the burst is inert on the wire. Still guards local bookkeeping; review before deleting. |
| `_applied_clip_echo_guid` / `_applied_clip_echo_until` (the extended clip-specific window added because a delayed `show_atom` outlived the window above) | `xstudio_plugin/ori_sync/playback_sync.py` | Same question, longer horizon | Same as above. This is the guard that was added *because* the first one kept failing — the pair is the clearest evidence for the change's premise. |
| `_local_view_action_until` (1 s after a local view action) | `xstudio_plugin/ori_sync/playback_sync.py` | Whether to let an in-flight remote view message override a local one | On a follower there is no contest: the host's view wins. On the host there is no competing broadcaster. |

## Retained — position is still multi-writer (§5.2)

Do not delete these while position remains open to every peer. Each one guards a
*position* field, whatever transition triggers it.

| Guard | Where | Why it stays |
|---|---|---|
| `_playback_apply_suppress_until` (armed on receipt of every remote playback message) | `xstudio_plugin/ori_sync/playback_sync.py` | Two peers may legitimately drive position. `ph.position` fires `attribute_changed` asynchronously, so an exact `_last_applied_frame` match loses the race during rapid scrubbing. |
| `_last_applied_frame` / `_last_polled_frame` exact-match check | `xstudio_plugin/ori_sync/playback_sync.py` | Position echo detection in the poll loop. |
| `_last_received_frame` (the driver's position, echoed back rather than our own) | `xstudio_plugin/ori_sync/playback_sync.py` | Keeps a view-change message from clobbering a peer's seek with a freshly-acquired playhead's 0. |
| Throttled scrub flush re-checking the guard at flush time | `xstudio_plugin/ori_sync/playback_sync.py` | A position captured before a peer began driving must not be released afterwards. |
| ~~`_local_scrub_active_until`~~ | `xstudio_plugin/ori_sync/playback_sync.py` | **Deleted 2026-08-10** — turned out to be dead code (armed, never read) by the time `broadcast-ownership` went looking for it. Not a lease-retirement; something else had already stopped consulting it. |
| `_loop_mode_apply_suppress_until` | `xstudio_plugin/ori_sync/playback_sync.py` | `playback_mode` is position, but **not solely for that reason** — see 2026-08-10 note below. |
| `_applying_pinned_mode` | `xstudio_plugin/ori_sync/playback_sync.py` | Apply-scope flag around a write we make ourselves — not a time window, and not an inference. |
| `_rv_updating` (apply-scope, depth-independent) | `rvplugin/ori_sync/*.py` | RV's events are synchronous, so this scope is complete and race-free. It guards position and annotation echoes too. |
| `_structural_mutation_suppress_until` | `xstudio_plugin/ori_sync/structure_sync.py` | Structure — see 2026-08-10 note below for why leasing doesn't fully cover this one either. |

### 2026-08-10 — `broadcast-ownership` Group 3 findings

`broadcast-ownership` (position/structure write leases) landed and was soak-tested
live, including two deliberately-contended two-peer test cases
(`contended_position_scrub`, `contended_structure_add_media` in
`sync_test/sync_tests.yaml`) — both converge cleanly and repeatably. That
satisfies the D5 exit criterion (a positive demonstration under contention) for
the *lease mechanism itself*. It does **not** mean every guard in the table
above is now retirable, and re-reading each one found most are not:

- **`_local_scrub_active_until`** was deleted — dead code, unrelated to leasing.
- **`_playback_apply_suppress_until`** and its close relatives
  (`_last_applied_frame`/`_last_polled_frame`, `_last_received_frame`, the
  throttled-flush recheck) are plausibly retirable but were **not** deleted: the
  guard's 0.4s window is longer than the newer claim-horizon's 0.3s, leaving a
  gap where a peer that already holds the lease (from an unrelated, earlier
  claim) could broadcast a stale echo of a *different* peer's just-applied frame.
  The contended soak checked eventual convergence, not this narrower
  during-handover window — an absence of failure here is not yet the positive
  demonstration D5 asks for.
- **`_loop_mode_apply_suppress_until`** is not a pure echo guard at all: two of
  its three arm sites suppress echo from local self-writes (carrying loop mode
  onto a newly-acquired playhead; forcing loop on an isolated clip), unrelated
  to any peer. Only the third is a remote-apply case, and all three share one
  read site that can't tell them apart.
- **`_structural_mutation_suppress_until`**'s five arm sites are cleanly
  remote-apply-only, but the guard's own surrounding comments describe a second
  job — giving xStudio's actor model time to settle after a `load_otio` /
  `remove_container` call before local structural polls re-scan it. That is an
  actor-model consistency concern, not a broadcast-authority one, and leasing
  doesn't touch it.

None of this reopens the earlier "zero firings is not evidence" warning — it's
a different problem: these guards had picked up second jobs since this table
was written, and retiring them cleanly would need splitting the mixed-purpose
ones into separate single-purpose mechanisms first, deliberately left as a
future pass rather than folded into a "pure removal" commit. See
`openspec/changes/archive/2026-08-10-broadcast-ownership/tasks.md` Group 3 for
the full per-guard reasoning, and `openspec/changes/retire-position-structure-echo-guards`
(queued behind `session-roles`) for the follow-on that owns actually retiring
them.

### 2026-08-15 — `lease-visibility-authority` findings

Visibility became a leased category. Two of the guards this table describes were
found wrong during live two-peer testing, both for the same reason: **a peer's
authority to broadcast was being read as evidence about what caused the
transition in front of it.**

- **The pre-claim ownership sample in `broadcast_view_state`** — the isolation
  frame reset was gated on `owns_visibility()` sampled *before* the claim, while
  the broadcast that followed claimed the lease and announced the new clip
  regardless. A peer that did not yet own visibility therefore reset its own view
  to frame 0 locally while announcing a different frame to everyone else. The
  sample was deleted. Gating one half of a two-part action on a predicate is the
  defect: **the frame you move to and the frame you announce must be the same
  frame.**
- **`_new_source_clip` treating every clip change as an isolation** — a paused
  scrub across a sequence emits one `show_atom` per edit crossing, identical to
  a deliberate isolation, so each crossing forced frame 0. Observed 2026-08-14
  20:50:44-47: a peer received `0, 0, 208, 0, 0, 0, … 227, 0` and jumped back to
  frame 1 repeatedly mid-scrub. Now gated on `_playhead_moving`
  (`_SCRUB_SETTLE_S`), which asks about the playhead rather than about authority.

**The pattern worth carrying forward.** Four of the five defects found in live
testing were a wall-clock window standing in for "who is driving" — a question
the lease now answers directly. Where a guard's real question is *authority*,
ask the lease. Where it is *what the user just did* (an isolation vs. a scrub, a
local write vs. an applied one), the lease cannot answer it and a window is not
made correct by the lease existing alongside it. The remaining
`_playback_apply_suppress_until` readers in the retained table are the last place
this distinction has not been made deliberately; `lease-visibility-authority`
task 9.14 owns deciding it.

## Known residual: `timeline_guid` is not a visibility field

`PLAYBACK_SETTINGS_1.0` carries `timeline_guid`, and `design.md` D1 puts the
visibility/position split precisely between `view_mode`/`clip_guid` and
`current_time`/`playing`/`playback_mode`. `timeline_guid` is on neither list, and
it is **not** stripped from a follower's message.

This is deliberate rather than an oversight. `timeline_guid` addresses the
position — a frame means nothing without the timeline it indexes — and stripping
it would leave a follower unable to say which timeline it scrubbed. The residual
is that a receiving peer's `_h_playback_set` updates its own
`active_timeline_guid` from it, so a follower can still move a peer's *bookkeeping*
notion of the active timeline. It cannot move another peer's **view**: both
applications gate the view switch on `view_mode`, which a follower no longer
sends. Worth revisiting if `session-roles` re-cuts the categories.
