## Why

> **Correction (same day, after further investigation).** This change was opened believing it explained `xstudio_selects_script_rv`'s failures — "the driver seeks to 61, moments later both apps sit at frame 0". It does not. Those readings were taken from apps that were **on different clips**, both reporting the same timeline name, so the frame numbers being compared were unrelated quantities (see `host-owned-visibility`). The echo loop described below is real and its fixes are verified — the stale-position burst is gone from the MQ traces — but it was not the cause of that test's failures. The scope below is therefore correct; the credit claimed for it originally was not.

xStudio broadcasts its own playhead position back while a peer is driving playback, and the driver applies it. Measured directly: peer seeks to 63, xStudio drops the message on a timeline mismatch, then broadcasts its stale `frame=0` twelve times over ~800 ms. The existing echo guard (`_playback_apply_suppress_until`) was designed to prevent exactly this, but it is armed **only in the branch that successfully applies a seek**. Every path that receives a message and declines to apply it — a mismatched `timeline_guid` while paused, a play-state-only update — leaves no guard armed at all, while the local playhead can still move for reasons of its own. Observed directly: peer seeks to 63, xStudio drops the message on a timeline mismatch, then broadcasts its stale `frame=0` twelve times over ~800 ms, and the driver snaps back to 0.

Two further faults compound it. A `Pinned Source Mode` `True→False` transition broadcasts `playing=True` on the assumption that it means a local double-click, but a peer-driven view switch produces the identical transition — so both peers start playing when nobody asked. And `broadcast_view_state` never consults the echo guard at all: a view switch re-acquires the playhead, the fresh playhead reads position 0, and that genuine-but-meaningless 0 goes out 182 ms after a seek landed.

Separately, the test harness could not see any of this, and in one case actively hid it. A failed container read left xStudio reporting `frame=None`, and because `validate_checkpoint` skips an app reporting no frame, a whole run of green seeks meant xStudio was never checked at all.

## What Changes

- Arm the echo guard on **receipt** of any remote playback message, ahead of every early return, rather than only where a seek is applied. What it tracks is "a peer is driving playback", which is true even for messages deliberately dropped.
- Re-check the guard when flushing a throttled scrub broadcast: a position sampled before a peer started driving must not be released after.
- Do not assert `playing=True` on a `Pinned Source Mode` transition that occurred while the guard was armed — that transition was peer-driven, not a local double-click.
- Make `broadcast_view_state` respect the guard for the **position** it carries, while still delivering the view change.
- Harness: verify every `set_frame`, not only the last; never compare a frame against a playing playhead; report observed values (frame, timeline, playing) on both pass and fail; make xStudio report real `playing`; stop a failed container read from wiping `frame`/`playing`; millisecond log timestamps; announce polling retries.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `xstudio-clip-selection-sync`: the echo guard SHALL cover the whole period a peer is driving playback, not only successful seek applies; a peer-driven view transition SHALL NOT be reported as a local play action.
- `sync-test-state-validation`: frame assertions SHALL be made only against a parked playhead, every commanded seek SHALL be verified, and an unreadable state SHALL NOT be reported as a pass.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py`: guard arming in `apply_playback_state`, `flush_pending_scrub_broadcast`, the PSM `True→False` branch, `broadcast_view_state`.
- `sync_test/python/sync_test/runner.py`: `validate_checkpoint`, new `_verify_frame_sync`, `_poll_until`, per-command seek checks, observed-value reporting.
- `sync_test/python/sync_test/xstudio_hook.py`: real `playing`, playhead read no longer nested in the container read.
- `sync_test/python/sync_test/cli.py`, `run_xstudio_inspector.py`: millisecond timestamps.
- No protocol change: no new message, field, or handshake. Behaviour changes are confined to when a peer chooses to broadcast.
