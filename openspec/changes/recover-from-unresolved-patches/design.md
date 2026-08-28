## Context

`record_unresolved_patch` ([patcher.py:60](../../../python/otio_sync_core/patcher.py#L60)) increments a counter, appends a formatted string to a bounded history, and logs. Its docstring is explicit that this is deliberate:

> Recorded, deliberately not queued for replay. The condition it signals — a peer broadcasting a patch against an object its peers were never given — is a bug at the sender, and a queue that silently succeeded later would be the same "looks fine, is not" behaviour this record exists to remove.

That reasoning holds for the case it was written against, and this change does not undo it. The observed failure is a case it does not cover: the sender was correct and the *receiver* missed the establishing `ADD_TIMELINE` through a master handover. Nothing is wrong with the patch; the peer simply lacks the context to apply it.

The distinction matters because it selects the response. Replaying a parked patch would resurrect the "looks fine, is not" behaviour the docstring rejects — the peer would apply one child and still be missing the timeline and every other child. Asking for a snapshot repairs the whole gap at once and cannot half-succeed.

## Goals / Non-Goals

**Goals**
- A peer that cannot apply structure it was given repairs itself rather than sitting silently broken.
- No change to what "structurally diverged" means or to when it is reported.

**Non-Goals**
- Replaying or queueing unresolved patches. Rejected above.
- Fixing the master-election conflict that produced the missing `ADD_TIMELINE`. That is a real second defect — xStudio self-elects on an empty session, defers its snapshot, and is then displaced — but a peer must survive missing structure whatever the cause, and this change is what makes the failure recoverable rather than terminal. Tracked separately; see `project_master_failover_silent_departure`.
- Changing the harness's behaviour of continuing after `_wait_for_media` times out. Worth revisiting (it converts a clear "media never arrived" into a confusing `state_mismatch` three steps later) but it is a test-harness concern, not session behaviour.

## Decisions

### Decision 1: A separate trigger, not a widened `structure_diverged`

`tick()` gains a second independent branch requesting recovery, rather than `record_unresolved_patch` setting `structure_diverged`.

Reusing the flag would be a smaller diff and is the obvious first instinct, but it would overload a term the session reports to the user (`structure-divergence-recovery`: "Divergence is visible to the user") with a condition that is not divergence. A peer missing structure has not diverged — it has not caught up. Conflating them makes the UI signal untrustworthy and would contradict a requirement that is currently satisfied.

The two triggers share `request_state(reason="recovery")` and the existing `RECOVERY_COOLDOWN`, so they cannot combine to exceed the session's request rate.

### Decision 2: Resolve-or-fire, keyed by target

The record keeps the unresolved target GUID and the time it was first seen. On each `tick()`, a target that is now present in the object map is dropped; a target still absent past `UNRESOLVED_SETTLE` fires one request.

Keying by target rather than by patch is what collapses a burst — a media add arriving as several children against one missing timeline is one gap, so it should cost one request. Checking the object map at fire time rather than trusting a "resolved" callback keeps the check honest: the object map is the thing the next patch will be tested against, so asking it directly cannot drift from what actually matters.

The settle window is the load-bearing part of the design, and is what answers `structure-divergence-recovery`'s objection that this signal is ambiguous. The objection is right at the instant the patch fails; it stops being right once the peer has had time to catch up and has not.

### Decision 3: `UNRESOLVED_SETTLE` reuses `DIVERGENCE_SETTLE`'s value

Rather than introduce a second tunable, start at the same settle duration structural divergence already uses, as a named constant of its own so the two can diverge later without a second edit.

In the observed failure the peer held zero objects for ~45s, so any settle value in the low seconds separates it cleanly from a peer that is merely a beat behind. There is no evidence justifying a distinct number, and inventing one would imply a precision the data does not support.

## Risks / Trade-offs

- **A request storm if a peer is persistently unable to apply patches.** Bounded by `RECOVERY_COOLDOWN`, which is shared with the existing trigger and already sized for this.
- **A snapshot is heavier than the patch that failed.** Accepted: it happens only after the settle window, only when the peer is genuinely missing structure, and the alternative observed in the logs is a peer that stays empty indefinitely.
- **The settle window delays repair by its own duration.** Acceptable against a failure mode that currently never repairs at all, and shortening it trades directly against the false-positive case in Decision 2.
- **`0 objects held` is the strongest form of the signal but not the only one.** The design deliberately does not special-case it: a peer missing one timeline out of several is in the same position, and a rule keyed on emptiness would miss it.

## Open Questions

- Should a resync that repeatedly fails to close the gap eventually escalate to something the user sees? `structure-divergence-recovery` has "a peer that cannot recover stays diverged and says so"; there is no equivalent surface here, and the unreachable case currently only records. Deferred until there is evidence of it happening.
