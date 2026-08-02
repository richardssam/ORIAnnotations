## Context

See proposal.md — Why. The mechanics that constrain the fix:

- `SyncManager.get_or_create_clip_timeline` derives a clip timeline's guid as `uuid5(NAMESPACE_OID, f"clip_timeline:{clip_guid}")`, explicitly so peers agree without coordination. It also keeps `_clip_timelines` (clip guid → clip timeline guid) for timelines this peer created.
- `SELECTION_1.0` already tells a receiver which clip is isolated (`clip_guid` + `view_mode: "source"`), and it arrives before the playback messages that follow.
- xStudio's receiver compares the incoming `timeline_guid` against `manager.active_timeline_guid` and the locally-viewed container guid, and on mismatch drops the message unless `playing` is true.
- RV's `_apply_playback` has no timeline guard at all — it applies frame and play state unconditionally — yet in the same failing run RV was also at frame ~184 instead of ~4. **RV's half is not diagnosed.** It may be frame-base translation (`_frame_base` is sequence-relative while a source-view frame is clip-local), or it may be that RV never received the message. Guessing here would be how the wrong fix gets written.

## Goals / Non-Goals

**Goals:**

- Make a clip-timeline `timeline_guid` resolvable by any peer that knows the clip.
- Keep the guard for genuinely unknown timelines.
- Land the same image on both hosts, which means being explicit about whether a source-view frame is clip-local or sequence-relative.

**Non-Goals:**

- New protocol fields. The derivation already gives every peer the answer; adding `clip_guid` to the playback payload would paper over a resolution bug with redundant data.
- Announcing clip timelines to peers (`broadcast_clip_timeline`). It exists for annotation bookkeeping; requiring it for playback would make correctness depend on message ordering and would spawn peer-side containers for every clip anyone looks at.
- Changing sequence-view playback, or the deliberate decision that a sequence-mode `clip_guid` is not actioned.

## Decisions

**Resolve by derivation, not by lookup.**
`_clip_timelines` only holds entries this peer created, so a lookup fails exactly in the case that matters — a peer following someone else into a clip it has not itself isolated. Deriving from the clip guid works regardless. A reverse map may still be worth keeping as a cache, but it cannot be the source of truth.

**Resolve against the clips the receiver knows, not just the selected one.**
Keying only off the last `SELECTION_1.0` would leave playback dropped whenever the two messages race or a selection is missed. Since the derivation is a pure function of the clip guid, a receiver can check the incoming guid against the clips in its own state. Worth confirming the cost is trivial at realistic clip counts; a reverse map built as clips are registered avoids hashing per message if not.

**Diagnose RV before touching it.**
The xStudio failure is understood and reproducible. RV's is not. The task list therefore starts RV with an investigation, and the fix follows from what it finds.

## Risks / Trade-offs

- **Frame-base mismatch between hosts.** A clip-local frame applied with a sequence-relative base lands on the wrong image, which would look like "sync works but shows the wrong frame" — worse than today's visible failure. → Assert the resulting image, not just that a frame was applied; `xstudio_selects` checks frame values on both peers, and the visual comparison tests exist for the stronger check.
- **Echo loops.** Applying an incoming clip-timeline playback could rebroadcast it, and the existing echo guards are keyed on the sequence timeline. → The capability already requires that applying an incoming clip change does not echo back; extend that check to this path and verify with a two-peer run rather than by reading the code.
- **A resolvable guid is not always a followable one.** The receiver may know the clip but have no way to show it (media missing, clip not in its local view). → Falling back to the existing ignore path is correct; it must not throw or half-apply.

## Open Questions

- Is a source-view frame clip-local on both hosts, or does either send sequence-relative values? The recording shows `frame=3` immediately after isolating a clip, which reads as clip-local, but this needs confirming on both senders before the receiver's translation is written.
