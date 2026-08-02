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

- ~~Is a source-view frame clip-local on both hosts?~~ **Answered (tasks 1.1): clip-local on both, and no new translation is needed.** Both senders already subtract their own view base, and RV's receiver re-reads `frameStart()` after the view switch. See tasks.md 1.1.

## Findings that revise this design

Diagnosis (tasks 1.1 and 3.1) materially narrows the defect. Recorded here rather than silently rewriting the proposal, because it changes what is worth building.

**1. `xstudio_selects.jsonl` replays a retired protocol.** It was recorded 2026-06-02; `d18ec21` (2026-07-01) retired `SELECTION_1.0` and folded view state into `PLAYBACK_SETTINGS_1.0`. Its playback messages carry no `view_mode`/`clip_guid`, and its `SELECTION_1.0` messages are ignored by both receivers because `SelectionSet` no longer exists. The proposal's claim that the receiver "has the clip guid from the preceding `SELECTION_1.0` message" is **false against current code** — that message is dropped.

**2. The xStudio bug is probably already fixed.** With a *current* sender, `view_mode="source"` + `clip_guid` arrive on the same message. The receiver's view block runs `apply_selection(source)` first, which sets `manager.active_timeline_guid = get_or_create_clip_timeline(clip_guid)` — the same derived guid the sender used. By the time the mismatch guard is reached the two guids are equal, so nothing is dropped. `unify-view-state-sync` appears to have closed this as a side effect.

The mismatch therefore only bites when the receiver never ran `apply_selection` for that clip — precisely what a recording with no `view_mode` produces. **The observed failure is a stale-recording artefact, not a live sync defect.**

**3. Fixing the receiver would make `xstudio_selects` pass hollowly.** Resolving the derived guid would let xStudio apply frame 3 — but in *sequence* view, since the stale recording never asks it to isolate. RV would do the same. The checkpoint asserts frame only (`timeline_name` is `None`, as the clip-timeline guid is never declared), so both land on ~frame 4 of the sequence and the test goes green while the two hosts show a different image from the one recorded. That is exactly the "sync works but shows the wrong frame" failure this design's Risks section warns against — reached by fixing the receiver rather than by neglecting to.

**Implication.** The load-bearing work is re-recording `xstudio_selects` against the current protocol, not changing receiver code. Derivation-based resolution is still defensible as belt-and-braces for a peer that misses the isolating message, but it should be justified on its own merits and verified against a recording that actually exercises source view — not adopted on the strength of a failure it does not explain.
