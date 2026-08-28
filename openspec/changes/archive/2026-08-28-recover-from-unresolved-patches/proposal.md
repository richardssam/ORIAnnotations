## Why

A peer that receives a structural patch naming an object it does not hold records the fact and then does nothing about it, forever. Observed live in `contended_position_scrub`, which has now failed in both of the last two full-suite runs and again the morning after:

```
09:26:41.833  UNRESOLVED PATCH INSERT_CHILD: target c7b6b324 not in object_map (0 objects held)
```

xStudio self-elected master on an empty session and deferred its snapshot; OpenRV then asserted `I_AM_MASTER` and took over. OpenRV's subsequent `INSERT_CHILD` referenced a timeline xStudio was never given, so the media add had nowhere to land. xStudio held **zero objects** for the remaining ~45s of the test and never asked anyone for state.

The test then reports `state_mismatch` after "peers never agreed on a position owner" — both of which are downstream symptoms. The actual defect is that `add_media` never arrived, and the harness only *warns* about that before driving the contention scenario anyway.

`structure_diverged` — the sole gate on `request_state(reason="recovery")` — is set from exactly one place, the send-side refusal branches. There is no receive-side path at all: "I changed structure I could not broadcast" recovers; "I was given structure I could not apply" does not.

## What Changes

- A structural patch that remains unresolved past a settle window SHALL cause the peer to request state from the master, reusing the existing recovery path and its rate limiting.
- The resync signal is **separate from `structure_diverged`**. `structure-divergence-recovery` deliberately declares divergence from the *attempt* rather than from observed mismatch, and explicitly names "a patch naming an object it does not hold" as a signal it will not use, because it cannot distinguish divergence from lag. That reasoning is correct and is preserved: this change does not widen what "diverged" means, does not surface a divergence to the user, and leaves that capability's requirements untouched.
- The ambiguity it identifies is resolved by *time* rather than by reinterpreting the signal. "I have not caught up yet" is self-clearing; a patch still unresolved after the settle window is, by definition, not catching up. And unlike marking a peer diverged, requesting state is idempotent and harmless when the peer was merely behind — so the two cases do not in fact demand opposite responses at this level.
- Resolution SHALL be detected, so a patch that lands normally moments later never triggers a request.

## Capabilities

### New Capabilities
- `unresolved-patch-resync`: turns a structural patch that cannot be applied, and stays that way, into a state request. The structural counterpart to `session-divergence-repair`, which does the same for view/playback mismatch.

### Modified Capabilities

None. `structure-divergence-recovery` keeps its current requirements and its send-side-only definition of divergence.

## Impact

- `python/otio_sync_core/patcher.py` — `record_unresolved_patch` gains a resolution-aware record; it currently appends a formatted string and nothing more.
- `python/otio_sync_core/manager.py` — `tick()` gains a second, independent trigger for `request_state(reason="recovery")` alongside the existing `structure_diverged` branch.
- Shared core, so both plugins inherit the fix; no rvpkg rebuild and no xStudio build are needed to test it.
- No protocol change: `request_state` and `STATE_REQUEST` already exist and are already answered by masters.
