## Why

A peer that ends up on the wrong clip or frame after joining stays wrong indefinitely today, in two ways reported from real use. First, `post-join-state-confirmation` (archived 2026-08-15) detects a join-time mismatch but is deliberately report-only — "no state request or broadcast SHALL be made as a result" — so a wrong-clip join is logged and never corrected. Second, independent of join, `playback_sync.apply_playback_state` already detects when an incoming message addresses a `timeline_guid` this peer doesn't have active (`_tl_mismatch`, `playback_sync.py:2510-2532`) — but the only thing it does about it is drop that one message's seek and log; there's no accumulation, and the peer never resyncs even as the host keeps scrubbing on the timeline the peer isn't on. Both are the same underlying gap — a confidently-detected divergence with no repair — surfacing at two different moments (once at join, continuously afterward).

Both bugs share a fix: give a *confident, repeated* divergence signal an actual response, by reusing the recovery path `structure-divergence-recovery` already built for structural divergence — `manager.request_state(reason="recovery")` — rather than inventing a second repair mechanism.

**This proposal explicitly narrows a requirement `post-join-state-confirmation` just shipped two days ago** ("Confirmation reports and does not repair"), which was written for good, documented reasons: this project has twice shipped a repair that acted on an uncertain signal and made things worse (a read timeout treated as a deletion; an unreadable position treated as frame 0). The repair added here is deliberately conservative about that same failure mode — see design.md for how it avoids acting on `NOT_CONFIRMED` (an uncertain signal) and on a single live mismatch (which can be a transitional artifact, not a real divergence).

## What Changes

- A `MISMATCHED` post-join confirmation outcome (not `NOT_CONFIRMED` — that stays inert, unchanged) triggers one `request_state(reason="recovery")` call, re-adopting the session's actual view through the existing join/apply machinery.
- A live `_tl_mismatch` that persists across multiple consecutive incoming playback messages (not a single one — a single mismatch can be a view-switch-in-flight artifact, per the existing comment at `playback_sync.py:2516-2518`) triggers the same recovery request.
- Both paths funnel through one rate-limited trigger so a repair that doesn't fix the underlying cause can't loop indefinitely re-requesting state.

## Capabilities

### New Capabilities
- `session-divergence-repair`: Turns a confidently-detected view/playback divergence — at join or during live playback — into a bounded, rate-limited request to resync from the session's master.

### Modified Capabilities
- `post-join-state-confirmation`: The "Confirmation reports and does not repair" requirement is narrowed — a `MISMATCHED` outcome (never `NOT_CONFIRMED`) may now trigger a recovery request through the new capability, on the same terms `structure-divergence-recovery` already uses for structural divergence.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py` (`confirm_join_state`, `apply_playback_state`'s `_tl_mismatch` handling)
- `python/otio_sync_core/manager.py` (`confirm_join`, `record_join_confirmation`, `request_state` — read/call, not restructured)
- `python/otio_sync_core/join_confirmation.py` (no change to the comparison logic itself, only to what a host does with `MISMATCHED`)
- `openspec/specs/post-join-state-confirmation/spec.md` — one requirement narrowed
- RV side (`rvplugin/ori_sync/`) is not in scope: the user's reports are xStudio-specific ("we did fix this with RV"), and RV's join/apply path does not currently need the same change.
