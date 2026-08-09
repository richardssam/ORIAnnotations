# Echo guards under host-owned visibility

Written as part of the `host-owned-visibility` change (§5.3). Its purpose is to
let the next person tell a **deliberate deletion** from an **oversight** when
reading a later diff — which guard was retired, what replaced it, and which ones
are still load-bearing.

## The principle

Host-owned visibility does not make echo suppression *better implemented*. For
one category it makes it **unnecessary**:

- **visibility** is now single-writer. Only the host broadcasts `view_mode` /
  `clip_guid`; a follower's are stripped at one point in
  `SyncManager.broadcast_playback_state`. A single writer cannot echo with
  itself, so the guards that existed to decide "was this view change mine or a
  peer's?" have nothing left to decide.
- **position** is still multi-writer, by design — every peer may scrub, play and
  stop. Its echoes are therefore still real, and every position guard stays.
- **annotation** is still multi-writer and was never gated.

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
question no longer arises: a follower does not act on visibility transitions, and
a host's transitions are user-caused by definition.

| Guard | Where | What it decided | What replaces it |
|---|---|---|---|
| `_playback_apply_suppress_until` read as "a peer is driving", used to withhold the auto-play claim on a Pinned Source Mode `True→False` transition | `xstudio_plugin/ori_sync/playback_sync.py` (PSM handler) | Whether a double-click was local | **Already replaced** — the branch now asks `manager.owns_visibility()`. The deadline itself is still armed and used by the position guards below, so it is *not* deletable; only this *use* of it was retired. |
| The same deadline read as "peer-driven isolation", used to keep the frame on a new source clip | `xstudio_plugin/ori_sync/playback_sync.py` (`broadcast_view_state`) | Whether an isolation was local | **Already replaced** — same predicate. |
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
| `_local_scrub_active_until` | `xstudio_plugin/ori_sync/playback_sync.py` | Stops selection-driven clip-start seeks snapping our playhead while we drive. |
| `_loop_mode_apply_suppress_until` | `xstudio_plugin/ori_sync/playback_sync.py` | `playback_mode` is position. |
| `_applying_pinned_mode` | `xstudio_plugin/ori_sync/playback_sync.py` | Apply-scope flag around a write we make ourselves — not a time window, and not an inference. |
| `_rv_updating` (apply-scope, depth-independent) | `rvplugin/ori_sync/*.py` | RV's events are synchronous, so this scope is complete and race-free. It guards position and annotation echoes too. |
| `_structural_mutation_suppress_until` | `xstudio_plugin/ori_sync/structure_sync.py` | Structure, untouched by this change. |

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
