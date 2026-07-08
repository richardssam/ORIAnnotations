## Context

`PLAYBACK_SETTINGS_1.0` already carries `view_mode` ("sequence" | "source") and `clip_guid` on every broadcast. `clip_guid`'s own field doc says it is "Authoritative in source mode; confirmation/highlight only in sequence mode (never seeked to)" — but no receive path actually implements the highlight half of that contract:

- **RV** (`rvplugin/ori_sync/playback_sync.py::_apply_playback`): when `view_mode == "sequence"`, it only calls `_switch_to_sequence_view` if `mode_changed or tl_changed`. A clip-only change (same mode, same timeline) falls through the `if`/`elif` entirely — `clip_guid` is stored into `_last_applied_clip_guid` and nothing else happens. There is no RV mechanism today that reacts to an incoming clip highlight while already in sequence view.
- **xStudio** (`xstudio_plugin/ori_sync/playback_sync.py::apply_playback_state` / `apply_selection`): the same `mode_changed or tl_changed` gate exists (`apply_playback_state` only calls `apply_selection` as a fallback when `_apply_sequence_view` can't resolve the timeline). But xStudio *does* already have partial machinery for exactly this: `apply_selection` contains an `item_selection_atom`-based highlight send (around line 1587-1626), gated behind `_ENABLE_TIMELINE_ITEM_HIGHLIGHT = False`. The gating comment explains why: sending `item_selection_atom` into a recently-rebuilt timeline races with that timeline's clip actors being torn down, and the resulting `broadcast_down_atom` is delivered to a Python event callback that segfaults (signal 11 in `execute_event_callback`) — a C++-level crash, not a raisable Python exception a `try/except` can reliably catch.

The reported symptom ("changing clip selection in xStudio, not consistently reflecting in RV") is specifically the **RV-receive-side gap**, since RV has no highlight mechanism at all. The xStudio-receive-side gap (RV → xStudio) is real but was already identified and deliberately disabled due to the crash risk above — re-enabling it is riskier and is the harder half of this change.

Separately, `docs/clip_selection_sync_status.md` documents an earlier, unrelated attempt at xStudio→RV clip *navigation* (not just highlighting) that was abandoned: it wrote `active_playhead.position = start_frame` to jump to a clip, the write silently failed (read back the old position), and a polling loop kept re-broadcasting the un-moved position back to RV, causing RV to revert and oscillate. That failure was about **navigation** (moving the playhead), which this change deliberately does not attempt — the field doc is explicit that sequence-mode `clip_guid` is "never seeked to." Avoiding navigation avoids that entire failure class by construction.

## Goals / Non-Goals

**Goals:**
- RV highlights the clip named by an incoming sequence-mode `clip_guid` without switching its view node or seeking, and without re-broadcasting an echo of that highlight back to the sender.
- xStudio does the same for incoming sequence-mode `clip_guid` from RV — but only if `_ENABLE_TIMELINE_ITEM_HIGHLIGHT`'s underlying crash risk can be mitigated well enough to re-enable safely (see D2). If it can't be mitigated with acceptable confidence, this change ships RV-side only and documents xStudio-side highlighting as still-blocked-on-the-crash, rather than re-enabling something that can segfault the app.
- Highlighting fires on every clip_guid change while remaining in sequence mode (not just when first entering the sequence, which is the only place the existing xStudio code path is reachable today).

**Non-Goals:**
- Seeking/navigating the playhead based on a sequence-mode clip_guid — the field is explicitly highlight-only; this is unchanged.
- Fixing xStudio's underlying actor-lifecycle crash at the engine/C++ level — out of scope for a plugin-only change. This design can only add a Python-level guard that reduces the crash's window, not eliminate its root cause.
- ~~The separate, pre-existing issue where each xStudio clip gets its own `Playhead` object with an inconsistent native `"Loop Mode"` default.~~ **Now partly in scope (D7):** addressed for the isolated-clip (source-mode) review case (force Loop). Not addressed for the sequence: the session keeps its own mode there.
- Re-implementing or fixing the abandoned navigation-based approach in `docs/clip_selection_sync_status.md` — that capability (seek-on-select) is not being resurrected; the doc is read purely for the echo-cascade failure pattern to avoid.

## Decisions

### D1: RV surfaces an incoming clip selection by switching to that clip's source view

**Revised after the task 1.1 spike (2026-07-06).** The original plan was an "in-place highlight" that changed RV's selection without touching `viewNode`, mirroring an assumed `rv.commands.selection()` getter. The spike disproved that premise: RV's Python-bound `commands` module (`OpenRV/src/lib/ip/IPMu/CommandsModule.cpp`, where the working `viewNode`/`setViewNode` live) exposes **no** selection command at all — no getter and no setter. The C++ `TwkApp::Document::selection()/setSelection()` exist but are not bound to Mu/Python, and the plugin's existing `on_selection_changed` call to `rv.commands.selection()` is not backed by a real command in these builds. There is therefore no non-navigating "highlight" primitive available to a plugin.

Decision (confirmed with the maintainer): RV surfaces an incoming sequence-mode `clip_guid` by **switching to that clip's source view** via the existing `_switch_to_source_view` path (resolve `clip_guid` → media path → source group → `setViewNode`). RV shows the isolated clip and loses the parent-sequence context; this is explicitly acceptable ("there are always individual clips that can be selected in RV; it just doesn't know what sequence it came from, and that's OK"). The clip identity is what syncs, not the sequence framing.

Echo is handled for free by reuse: `_switch_to_source_view` already brackets its `setViewNode` in `self.plugin._rv_updating = True` / `finally: = False`, and RV's `on_view_changed` rejects while `_rv_updating` is set — so applying the incoming selection does not re-broadcast. View-mode bookkeeping stays as-is: `_last_applied_view_mode` records the incoming `"sequence"` (so subsequent sequence-mode messages are same-mode and only `clip_changed` drives re-switching); a genuine mode change back to a real sequence view still triggers `_switch_to_sequence_view`.

*Consequence for the spec*: the earlier "without switching its active view node / playhead position unchanged" requirement is **dropped for RV** — RV does switch view and `_switch_to_source_view` resets the frame to the clip start. The non-navigating contract is retained only on the xStudio side (D2), which does have an in-timeline highlight primitive (`item_selection_atom`). The two peers are intentionally asymmetric, reflecting each app's actual capabilities.

*Alternative considered*: bind `Document::setSelection` into Mu/Python in the OpenRV C++ build to get a true in-place highlight. Rejected for this change — it's an engine/binding change, not plugin-only, and expands scope well beyond the reported symptom.

### D2: xStudio-side highlight is re-enabled only behind a "timeline stable" guard, not unconditionally

The crash is a race: `item_selection_atom` sent into a timeline whose clip actors are being torn down (recently rebuilt). Note there are **two** `_ENABLE_TIMELINE_ITEM_HIGHLIGHT`-gated sends, both of which carry the same risk and must both be gated: the specific-clip highlight send (~lines 1590-1626) and the empty-vector *clear*-selection send in the selection-clear path (~lines 1336-1341). Consequently the flag must be *replaced* by the recency guard, not merely flipped to `True` — flipping it would re-enable the clear-send unguarded. Rather than re-enabling `_ENABLE_TIMELINE_ITEM_HIGHLIGHT` unconditionally, gate the sends on a recency check — e.g., track the timestamp of the most recent structural rebuild event for the target timeline (insert/remove/reorder), and skip the highlight send (silently, no-op) if that timeline was rebuilt within some threshold window (candidate: the same ~200-300ms settle windows already used elsewhere in this file, e.g. `_pending_seek_deadline`'s 300ms). This does not guarantee the crash can't still occur (a rebuild could start *during* the send itself), so:
- Keep the `try/except` around the send (cheap insurance for the cases it does catch).
- Treat this as a risk-reduction measure, not a fix — explicitly call out in the PR/rollout that this is a best-effort mitigation of a known engine-level race, and keep a fast kill-switch (the existing `_ENABLE_TIMELINE_ITEM_HIGHLIGHT` flag) to revert to disabled if crashes recur during testing.

*Alternative considered*: leave `_ENABLE_TIMELINE_ITEM_HIGHLIGHT` off entirely and ship RV-side-only in this change. This is the fallback if the stability-guard approach doesn't hold up in testing (see Open Questions) — the proposal's "symmetric" goal is a target, not a hard requirement, given the crash risk predates this change and isn't something a plugin change can fully own.

### D3: Receiver does NOT act on sequence-mode clip changes — the field has no selection signal (final, after live testing)

The proposal assumed a sequence-mode `clip_guid` change signals an explicit clip selection that the receiver should reflect. **Live testing disproved this**, through three iterations:

1. Followed sequence-mode `clip_guid` changes → RV isolated the wrong clip and swallowed the first selection.
2. Gated the follow on `not playing` → broke real selections that carry a transient `playing: true` (playhead re-acquisition).
3. Followed regardless of playing, only "within an established sequence" → **scrubbing** (and the initial connect) still changed the receiver, because the field changes on any playhead move.

Root cause, confirmed from logs: in sequence view xStudio's `clip_guid` is emitted from the `show_atom` **media-change** event — it tracks the clip under the **playhead**, which moves on scrub, playback, and connect. There is **no distinct "user selected a clip" signal** in sequence mode: the `Timeline.selection` actor stays empty during interaction (only `Playlist.playhead_selection` — the flat-playlist case — is ever populated). So a receiver literally cannot tell "user selected clip X" from "playhead moved onto clip X."

**Decision:** the receiver does **not** act on a sequence-mode `clip_guid` change. Both RV and xStudio stay on the sequence in sequence mode; only `mode_changed`/`tl_changed` drives a sequence-view switch. Explicit clip isolation continues to flow through **source mode** (double-click in xStudio → `PSM False` → `view_mode: "source"`), which already worked and is unaffected. This is a scope reduction of the proposal's "sequence-mode highlight" goal — it is not achievable with the current send-side data and would require a send-side change to broadcast `clip_guid` from an actual selection actor rather than the playhead (out of scope; parked).

What this change still delivers: the xStudio-side timeline item-highlight (`_highlight_timeline_item`) is re-enabled behind the D2 stability guard, and now fires for **source-mode** selections applied via `apply_selection` (previously disabled entirely by `_ENABLE_TIMELINE_ITEM_HIGHLIGHT = False`). The RV receive path for sequence mode is unchanged from before the proposal. The remaining decisions (D4–D8) are the isolated-clip (source-mode) reliability/review fixes that this change actually shipped, discovered and refined through live testing.

### D4: Broadcast the isolated-clip selection from the selection event, not the show_atom

xStudio's isolated-clip (source-mode) selection was broadcast off the `show_atom` **media-change** event, which only fires when the displayed media actually changes and is sometimes suppressed. Live logs showed this missed ~5 of 21 double-clicks (re-clicking an already-shown clip fires no media-change) and lagged 1.3–1.5 s under POLL-SLOW. The **selection-actor `source_atom` event** fires reliably on every deliberate selection, and the clip is resolvable there (for a playlist via `playhead_selection.selected_sources`; the `Timeline.selection` actor stays empty). Decision: in `resolve_and_broadcast_selection`, when in single-clip mode (`PSM False`) with a resolved selected clip that differs from the last one broadcast, broadcast it as `mode=source` directly — deduped via `_cur_clip_guid` so it never double-broadcasts with the `show_atom`/PSM-transition paths, and gated on `PSM False` so it can't fire while scrubbing a sequence.

### D5: Read Pinned Source Mode fresh right after a selection

The "select a clip in the timeline" mode = `PSM False`, but the plugin only refreshes its cached `_last_pinned_source_mode` when the selection poll runs, which lags xStudio by a poll or more. The **first** selection was therefore decided against a stale `PSM=True` → broadcast as `mode=sequence` → dropped by the receiver, while the second (PSM now caught up) worked ("first fails, second works"). Decision: stamp `_last_source_atom_at` on every `source_atom` event, and in the `show_atom` single-clip decision read PSM **fresh** (`_read_pinned_source_mode_fresh`, bounded) when a selection fired within ~3 s. Gated on a recent selection so scrub/playback `show_atom`s keep using the cheap cached value.

### D6: Isolating a clip seeks both peers to the clip's first frame

The isolated clip's frame space is 0-based; each peer was restoring its own last-viewed position within a clip, so RV and xStudio showed different frames of the same clip. Decision: on a *new* isolation (source mode, clip changed) `broadcast_view_state` broadcasts `current_time.value = 0` and seeks the local playhead to `0`, so both peers land on the clip's first frame (= `source_range.start`, "the first frame as the sequence uses it"). Only fires on a new isolation; scrubbing *within* an isolated clip rides the normal position-broadcast path and is unaffected.

### D7: Isolated clips loop; guard the loop↔play-once oscillation

Isolated clips are typically short review shots; the engine's per-clip **Play Once** default flashes them past in under a second (and RV with them), and "play at end" then does nothing. Decision: force **Loop** for isolated clips. This required several reinforcing pieces because the mode was fought from multiple directions:
- On isolation, set `_last_known_playback_mode = "loop"` (so `_carry_over_playback_mode` keeps Loop on every re-acquired clip playhead instead of stomping it back to Play Once at the loop boundary) and set the playhead's `Loop Mode = "Loop"`.
- **Send side:** `_get_playback_mode()` reports `"loop"` while in single-clip mode, so a freshly-acquired clip playhead's transient Play Once default (before carry-over lands) never leaks into a broadcast.
- **Receive side:** while in single-clip mode xStudio **ignores an incoming `play-once`** — RV's isolated source view resets *its* play mode to Play Once and broadcasts it back, which otherwise reverted our Loop and re-poisoned `_last_known_playback_mode` (a loop↔play-once oscillation). In single-clip mode xStudio is authoritative for the clip's loop mode; sequence mode still respects the peer.

*Known residual (accepted):* the **first** playthrough of a freshly double-clicked clip still plays once — xStudio's native auto-play starts on the fresh Play Once playhead the instant you double-click, before any plugin code runs; Loop lands a moment later, so every pass *after* the first loops. Fully winning that race would require an intrusive stop→set-Loop→play restart (visible hitch), judged not worth it.

### D8: Bound every playhead read/write on the selection path

The frame-reset and loop-force (D6/D7) write to `active_playhead`, and `current_playback_state()` reads it. At a clip's end the playhead actor is torn down / re-acquired, so `active_playhead` goes stale, and an **unbounded** read/write on a dead actor blocks the poll thread for ~100 s (the default `request_receive` timeout, enforced in a C++ dequeue that holds the GIL — a Python-thread timeout can't interrupt it). This froze xStudio after the first clip. Decision: wrap all such reads/writes in `bounded_timeout` (the established pattern), so a stale actor raises/falls back in ~2 s and the plugin re-acquires a live playhead instead of wedging.

## Risks / Trade-offs

- **[xStudio's item_selection_atom crash could still occur despite the stability guard]** → Mitigated by D2's guard plus keeping `_ENABLE_TIMELINE_ITEM_HIGHLIGHT` as an instant kill-switch; if live testing shows the guard is insufficient, ship RV-side-only per D2's fallback rather than accept crash risk.
- **[RV echo guard (`_rv_updating`) could suppress a genuine subsequent local selection if timed wrong]** → This flag is already used for exactly this class of problem elsewhere in `_apply_playback`; reusing it (rather than inventing a new guard) keeps the suppression window consistent with all other apply-side echo handling in this file.
- **[Symmetric bidirectional highlighting could reintroduce a selection echo cascade similar to the old navigation-based one]** → Structurally different from the old failure: the old cascade was caused by a *position write* silently failing and a *poller* re-reading and re-broadcasting the stale value. This design writes no position, and the existing `_selection_broadcast_suppress_until`-style guards (already present in `apply_selection`) are reused/extended, not reinvented.

## Open Questions

- ~~What is RV's actual Python-exposed command for *writing* selection?~~ **Resolved (task 1.1 spike):** none exists — RV binds no selection command to Python at all. D1 was reworked to switch to the clip's source view instead (see D1).
- ~~Is the "timeline stable" recency window (D2) better tracked per-timeline or globally?~~ **Resolved (implementation):** global. `structure_sync.py` already advances a plugin-global `_structural_mutation_suppress_until` to `now + 1.5s` at every `load_otio(clear=True)` rebuild site, so `_timeline_recently_rebuilt()` just checks that timestamp — no new per-timeline bookkeeping. Global is conservative (any rebuild suppresses any highlight), which is the safe bias against a segfault.
- **[Still open — needs live testing]** Should D2's guard be validated against a real, repeatable crash repro before merging, or is the crash rare/timing-dependent enough that this can only be verified by extended live soak-testing? This is now the *primary remaining risk*: the code is written but the crash guard's sufficiency is unproven. Tasks 4.3–4.5 are the live tests that decide "fixed" vs. "reduced" vs. "fall back to RV-only." These require running xStudio+RV and cannot be exercised from unit tests.
