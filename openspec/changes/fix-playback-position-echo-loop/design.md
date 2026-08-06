## Context

Evidence gathered from `xstudio_selects_script_rv` runs on 2026-08-05, correlating `sync_test/logs/.../xstudio_plugin_*.log` MQ traffic with `runner.log`.

The recording drives OpenRV; xStudio follows. Every `set_frame` is a discrete seek with `playing: false` — nothing should drift, so a wrong frame is always a real fault rather than a sampling artefact.

Current-state facts, each verified in the logs:

- The echo guard `_playback_apply_suppress_until` is armed at exactly one place: inside `if (not playing or playing_changed) and not _tl_mismatch:` in `apply_playback_state`. Two earlier returns bypass it entirely, including `if _tl_mismatch and not state.get("playing", False): return`.
- With the guard unarmed, `on_playhead_attribute_changed` sees a frame matching neither `_last_applied_frame` nor an active window and broadcasts. Trace: `RECV frame=63.0` at `16:31:34.277`, then `SEND frame=0.0` ×6 starting `16:31:34.382` (105 ms later); and after a logged `mismatched timeline_guid — ignoring (not playing)` at `16:31:35.233`, `SEND frame=0.0` ×12 from `16:31:35.327` over ~800 ms.
- `flush_pending_scrub_broadcast` sends `_pending_scrub_state` — captured earlier — with no guard re-check at flush time.
- The PSM `True→False` branch calls `broadcast_view_state(_cg, "source", playing_override=True)`, commented "xStudio auto-plays on double-click". Trace: `RECV frame=61.0 playing=False` at `16:47:36.045`, then `SEND frame=0.0 playing=True` at `16:47:36.203` — 158 ms later, with no user input at all.
- `broadcast_view_state` consults no guard. Trace: `RECV frame=61.0` at `16:56:08.003`; `[position_atom] re-acquired live playhead` at `16:56:08.122`; `SEND frame=0.0` at `16:56:08.185`. The adjacent log line at the same millisecond reads `[SEL] → suppressed (echo guard)`, so a sibling path checks the guard and this one does not.
- Harness: `validate_checkpoint` skips an app whose `frame` is `None`. `get_xstudio_state` nested the playhead read inside the container read, so one `Could not read container: invalid_argument` produced `frame=None, playing=False` — and a run of four green seeks in which xStudio was never actually compared.

## Goals / Non-Goals

**Goals:**
- A peer's seek survives. While one peer drives playback, no other peer broadcasts a position that overrides it.
- Every distinct broadcast path honours the same rule, rather than each rediscovering it.
- The harness can tell the difference between "converged", "wrong", "still moving" and "could not be read", and says which.

**Non-Goals:**
- Changing the protocol. No new message, field, or handshake.
- Removing double-click auto-play. It is deliberate; this change only stops it being *inferred* from a peer-driven transition.
- Fixing the deliberate frame-0 of a new source-clip isolation (`_new_source_clip`), which is correct behaviour — starting a freshly isolated clip at its first frame.
- Verifying `set_selection`. Neither hook exposes selection state, so it is untestable today; tracked separately.

## Decisions

### The guard tracks "a peer is driving", not "a seek was applied"
This is the reframing the whole change rests on. The guard was written as a companion to one assignment — suppress the async `attribute_changed` that `ph.position = frame` will fire. That is too narrow: a message we *decline* to apply still tells us a peer is driving, and our own playhead can still move meanwhile (a selection change resetting it to a clip start). Arming on receipt makes the guard mean what its name implies.

Arming stays rolling and short (`_PLAYBACK_ECHO_GUARD_S`, 0.4 s, refreshed per message), so it expires as soon as the peer stops and genuine local scrubs resume. The apply site re-arms as well, because the view switch, Loop Mode set and bounded reads between receipt and `ph.position` can consume much of the window.

Alternative considered: a boolean "remote apply in progress" flag around the apply. Rejected — it cannot cover the async callback that arrives after the apply returns, which is the original race.

### Suppress the *inference*, not the feature
`playing_override=True` on a PSM `True→False` transition encodes a real xStudio behaviour: double-click isolates a clip and starts playing. The fault is attributing that transition to a user when a peer caused it. So the override is suppressed only while the guard is armed, and passing `playing_override=None` falls through to `broadcast_view_state`'s own default of `playing=False` — "do not assert play" — leaving play state to whoever is actually driving.

### `broadcast_view_state` withholds the position, not the message
This path carries two things: the view (mode/clip) and a position. Only the position is unsafe while a peer drives. Dropping the whole broadcast would delay a legitimate view change, so the view still goes out and the position is withheld.

### Harness: unreadable is not a pass
Two silent-skip paths turned missing data into green. `validate_checkpoint` skips an app reporting `frame=None`, and a playing playhead has no frame worth comparing — so both must be *visible* rather than quietly tolerated. A playing playhead is reported as "no frame assertion is possible" instead of a fabricated diff; the xStudio playhead read is decoupled from the container read so a container failure cannot silently disable every frame assertion.

## Risks / Trade-offs

- **[Risk]** Arming on receipt suppresses more local broadcasts than before, so a genuine local scrub *during* a peer's drive may be dropped. → **Mitigation**: the window is 0.4 s and expires on its own; two peers scrubbing simultaneously is already undefined, and following the driver is the better resolution. This is the same trade the guard already made for applied seeks.
- **[Risk]** Withholding the position from `broadcast_view_state` could leave a peer on the correct view at a stale frame if no position update follows. → **Mitigation**: the driver's own position messages continue to flow and are authoritative; the withheld value was the *receiver's* position, which was never the right answer.
- **[Risk]** None of this is unit-tested: `xstudio_plugin/ori_sync/playback_sync.py` imports the `xstudio` API and cannot be imported standalone, so all four plugin changes rest on log evidence from a single test. → **Mitigation**: each fix was verified by the disappearance of its specific signature in the MQ trace, not by the test passing. A stub harness (as `tests/otio_sync/test_playback_view_dispatch.py` does for the RV side) is the right follow-up and is listed in tasks.
- **[Trade-off]** The harness changes make previously-green runs fail. That is the intent — they were false passes — but it means run-history comparison across this change is not like-for-like.

## Migration Plan

1. Plugin: arm on receipt; re-check at scrub flush; suppress the PSM play inference; withhold position in `broadcast_view_state`.
2. Harness: per-seek verification, playing-aware assertions, observed-value reporting, xStudio `playing`, container/playhead decoupling, millisecond timestamps.
3. Verify each plugin fix by its signature in the MQ trace, not by overall pass/fail — the failures are independent and one masks the next.
4. Only once the trace is clean, judge `xstudio_selects_script_rv` end to end.
5. Rollback is per-fix: each is independent and individually revertable.
