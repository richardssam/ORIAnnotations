## Context

See proposal.md for the observed failure and the log evidence. Two mechanisms already exist and are nearly enough:

- OpenRV's `_displayed_view()` reads what RV is showing from the live view node, and `_broadcast_playback` already calls it (for the log and, since `fix-source-view-timeline-guid`, for the timeline guid). It does *not* use it for `view_mode`/`clip_guid`, which still come from `_cur_view_mode`/`_cur_clip_guid`.
- xStudio already computes the provenance of a `show_atom`, emitting `[PROVENANCE remote-induced? source=… PLAYBACK_SETTINGS_1.0/SET settling+0.05s age=0.05s]`. It is written to the log and then not consulted.

So both fixes are mostly a matter of *using* a signal that is already there.

## Goals / Non-Goals

**Goals:**
- A broadcast never describes a view the sender has already left.
- A selection caused by applying a remote message is not reported back as a local selection.

**Non-Goals:**
- Reworking the view-change handler's ordering, or making the frame-changed event wait for it. Correcting the values read at send time is smaller and does not perturb RV's event sequence.
- Widening the existing `_last_broadcast_frame` guard. It is why the bug appeared only on the first selection, but it is not the defect — see "Frame-equality is not the bug" below.
- Changing visibility or position enforcement.

## Decisions

**1. OpenRV resolves the broadcast view at send time**
- *Rationale*: `_displayed_view()` already answers "what am I showing" from the live view node, which is the property that makes it correct during a switch — a recorded value is stale precisely when it matters. `_cur_clip_guid` stays the source for the clip identity (it is the resolved clip, and `_forget_current_clip` clears it when unresolvable), but it SHALL NOT be paired with a mode the display contradicts.
- *Open point for implementation*: when the displayed mode is `source` but no clip has been resolved yet, the honest broadcast is source-with-no-clip, which the receiver already declines and records as a mirror failure. That is the existing, documented behaviour for an unresolvable isolation and should be reused rather than invented.

**2. xStudio's provenance attribution gates the broadcast**
- *Rationale*: the plugin already decides an event is `remote-induced?` and prints it. Promoting that from annotation to a gate is the smallest change that fixes the observed bounce, and it puts the decision where the evidence already is.
- *Alternative*: a blanket suppression window after any remote apply. Rejected — that is what `_selection_broadcast_suppress_until` and `_local_view_action_until` already do for narrower cases, and stacking another timer makes the interaction between them harder to reason about than the per-event attribution.

**3. Frame-equality is not the bug**
- The reason the user saw this only on the *first* `seq_B` selection is that `on_rv_frame_changed` broadcasts only when `current_frame != _last_broadcast_frame`; the second selection landed on the same frame 100 and sent nothing. That guard is doing something reasonable and is out of scope, but it means **the reproduction is order-dependent**: to see the failure again, the isolated clip's frame must differ from the last broadcast frame. Any test or manual repro has to arrange that deliberately.

## Risks / Trade-offs

- **Risk**: gating on provenance suppresses a *genuine* local selection that happens to land inside the attribution window, so the user's own click never reaches peers. → Mitigation: the attribution is per-event and carries an age; keep the window as tight as the existing one and cover the genuine-local case with a scenario. This failure is worse than the bug being fixed, because it is silent.
- **Risk**: reading the displayed view at send time costs an extra node query per broadcast, on RV's event path. → Mitigation: `_displayed_view()` is already called there for the log line; reuse the one call rather than adding a second.
- **Risk**: the two fixes are independent, and fixing only one leaves a partial improvement that looks like a full one. → Mitigation: verify with a repro where the isolated clip's frame differs from the last broadcast frame (see decision 3), so the broadcast actually fires.
