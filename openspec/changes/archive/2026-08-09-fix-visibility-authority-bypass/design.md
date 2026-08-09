## Context

See `proposal.md` — Why, and `evidence.md` for the log extracts. Two defects,
with different confidence: defect 1 (a follower's structure moves the host's
view) is directly evidenced; defect 2's symptom is evidenced but its mechanism
is inferred.

What the code already provides, and what this design builds on:

- **Enforcement point.** `manager.claim_category` / the `CHANNEL_*` leases strip
  `view_mode`/`clip_guid` from a follower's broadcast and return `SUPPRESSED`.
  This worked — 284 strips, zero `view_mode` sent. Nothing here is being undone.
- **Host selection reaction.** `xstudio_plugin/ori_sync/playback_sync.py` turns
  xStudio `show_atom` / `source_atom` events into a view-state broadcast, via a
  chain of suppressions: an echo guard (`_selection_broadcast_suppress_until`),
  a delayed-clip-echo guard (`_applied_clip_echo_guid`), and a
  "playing through sequence" guard. All three key off the host's *own* prior
  actions. None of them knows a remote message was just applied.
- **Follower view adoption.** `rvplugin/ori_sync/playback_sync.py::_apply_playback`
  gates the view switch on `_last_applied_view_mode` / `_last_applied_clip_guid`
  / `_last_applied_tl_guid` (~line 195), then switches via
  `_switch_to_sequence_view` / `_switch_to_source_view`.
- **Non-adoption reporting.** `_report_mirror_failure` → `controller.mirror_failure`
  → `otio_sync_core/inspection.py` → `sync_test/.../openrv_hook.py`
  (`view_mirror_error`) → `runner.py::VIEW_MIRROR_FAILED`. A reporting surface
  that satisfies "observable without reading application logs" already exists;
  it is reached only from hard failures, never from declining to act.

Two constraints shape everything below:

1. **The host's display actually changed.** The host did not merely broadcast
   something wrong — it entered single-clip mode (`Pinned Source Mode: True → False`)
   and showed the follower's clips. Any fix that only touches the broadcast
   leaves the host visibly wrong and now silent about it.
2. **The route from ADD_TIMELINE to the host's selection event is not yet
   established.** `manager._h_add_timeline` returns `None` for a clip timeline —
   it notifies no host application code — yet 3.3 s later the host's
   `source_atom` fired. Reading the code does not close this gap.

## Goals / Non-Goals

**Goals:**

- Name the actual causal route from a follower's structural message to the
  host's display change, from a trace rather than from inference.
- Make a remote-induced host display change impossible, at the point where the
  display changes.
- Make a follower's view comparison read what it is displaying, so a local
  change cannot make a later remote instruction look redundant.
- Make every non-adoption — declined as well as failed — visible on the
  existing `mirror_failure` surface.
- Cover both defects in `sync_test`, which needs a follower that changes its own
  view for the first time.

**Non-Goals:**

- Revisiting field stripping or the lease model. They hold.
- Re-deciding the `host-owned-visibility` §5.1 guard deletion. It stays blocked
  until the bypass is closed and a fresh soak is taken — see Risks.
- Removing xStudio's existing echo/scan-through suppressions. They address a
  different problem (the host's own echoes) and are unaffected.
- Any wire-format or schema change. Nothing in this design alters a message.

## Decisions

### D1 — Fix defect 1 where the display changes, not where the broadcast leaves

The host's broadcast at `20:38:24.256` was *correct*: it reported what the host
was displaying, and the host is entitled to broadcast that. The defect is one
step earlier — the host should not have been displaying it.

*Alternative considered:* extend suppression so the host does not broadcast
visibility while a remote apply is in flight. Rejected. It restores wire
agreement between two peers that are looking at different things, which is the
precise failure mode the `session-visibility-authority` spec exists to remove.
It would also have made this soak *harder* to diagnose, not easier.

The spec scenario is explicit that "what the host displays SHALL be unchanged",
not "no broadcast SHALL be emitted", and this decision follows it.

### D2 — Establish the route by instrumentation before choosing the guard site

`_h_add_timeline` stores the clip timeline, calls `_note_session_guids` and
`_traverse_and_map_preserve`, and returns `None`. No host-app callback fires.
The candidate routes to the host's `source_atom` 3.3 s later are: the structure
poll reacting to newly-mapped objects; `get_or_create_clip_timeline` bookkeeping
on the host side; or an xStudio-internal reaction to media becoming addressable.
Guessing between these and guarding the wrong one produces a fix that passes
review and fails the next soak.

So the first work item is a **remote-apply provenance span**: `manager.apply_patch`
records the source peer and a monotonic timestamp for the duration of each
remote apply and for a short settle window after it, and the host's selection
reaction logs, for every event it resolves, whether it landed inside such a
window and which peer opened it.

This is deliberately not throwaway instrumentation — the same span is the
mechanism D3 uses. The instrumentation task ships the state; the guard task
consumes it.

### D3 — The guard is provenance-based, and corrects rather than suppresses

xStudio's selection reaction currently classifies events by *what they look
like* (is the media in a sequence? is playback running? does the guid match one
we just applied?). Every one of those is a proxy for "did the user do this",
and defect 1 is a case where all the proxies say yes and the answer is no.

Add the missing input directly: an event resolved inside a remote-apply
provenance window is classified `remote-induced`. A `remote-induced` display
change on the host is **reverted** — the host re-asserts the view it held before
the remote message arrived — and the revert is logged. It is not merely left
unbroadcast (D1).

*Alternative considered:* a whitelist of remote message types permitted to touch
the display. Rejected — that is enforcement over message shape, which is the
form of enforcement defect 1 already defeated.

*Alternative considered:* fold this into the existing
`_selection_broadcast_suppress_until` timer. Rejected as conflation: that timer
means "we caused this echo ourselves, ignore it", and this means "a peer caused
this, undo it". Same shape, opposite remedy; sharing one variable would make
both harder to reason about. They may share a helper.

### D4 — OpenRV compares against the view it is displaying, read from OpenRV

Delete `_last_applied_view_mode` / `_last_applied_clip_guid` /
`_last_applied_tl_guid`. Derive the comparison from `rv.commands.viewNode()`
resolved through `sequence._rv_node_to_timeline_guid` / the source-group maps
— the same maps `_switch_to_sequence_view` / `_switch_to_source_view` already
use to go the other way.

`_cur_view_mode` / `_cur_clip_guid` are **not** the fix, even though
`broadcast_view_state` keeps them current on local changes. They are a second
cache of the same fact, kept in step by hand at each call site, and would
acquire the same class of staleness at the next site that forgets to update
them. They stay, scoped to composing broadcast payloads.

*Alternative considered:* also update `_last_applied_*` from the local-change
path. Rejected for the same reason — two variables that must agree, where one
read of the application would do.

### D5 — Reproduce defect 2 before changing its behaviour, because the inferred mechanism does not fully fit

The proposal flags the mechanism as inferred and asks for confirmation. Code
reading actively weakens it: at `20:38:26.899` the last applied mode was
`source` and the incoming mode was `sequence`, so `mode_changed` was `True` and
the switch *should* have been attempted. The `RECV playback …` log line is
emitted after the view block, and it appeared — so the block ran.

A silent no-op inside `_switch_to_sequence_view` is therefore at least as likely
as the stale-cache reading: the `len(candidates) == 1` substitution, or a
`setViewNode` that lands and is then displaced. Both exit without a
`MIRROR FAILED`.

This does not change D4 — comparing against a cache is wrong on its own terms
and the spec requires the change regardless. It changes the *ordering*: D6
(making non-adoption visible) lands first, so the reproduction produces a named
outcome instead of another six seconds of silence.

### D6 — Every exit from the view block records an outcome, on the existing surface

`_apply_playback`'s view block gets exactly four outcomes, each recorded:

| outcome | meaning | clears `mirror_failure` |
|---|---|---|
| `adopted` | a view switch was performed | yes |
| `already-displayed` | the instruction matches the displayed view | yes |
| `declined` | deliberately not actioned, with a reason | no |
| `failed` | attempted and could not complete, with a reason | no |

`declined` covers the sequence-mode `clip_guid` case that is deliberately not
actioned today (playhead tracking, ~line 204) and anything else that returns
without switching. It carries the reason, so a test distinguishes "correctly
declined" from "silently did nothing" — the distinction the soak could not make.

The surface is the existing one: `controller.mirror_failure` →
`inspection.py` → `openrv_hook.py` → runner. Widen it to a small record
(outcome + reason + the view it refers to) rather than a bare string.

*Alternative considered:* a new reporting channel. Rejected — the harness
already reads this one, and a second channel is a second thing to forget.

### D7 — Coverage needs a follower that changes its own view

No existing test does this, which is why eleven runs missed both defects. One
new scenario drives both:

1. Follower isolates a clip locally → assert the host's displayed clip is
   unchanged, and no host visibility broadcast attributable to it.
2. Host then reports sequence view → assert the follower adopts it, and that its
   recorded outcome is `adopted`, not `already-displayed`.

Step 2 is the defect-2 regression test and it only means anything once D6 is in.

## Risks / Trade-offs

- **The provenance window misclassifies a genuine host user action taken while a
  remote apply is in flight, and reverts it.** → The window is bounded to the
  apply plus a short settle; the revert is logged with the peer that opened the
  window, so a false revert is visible rather than mysterious. Size the settle
  window from the observed trace, not from a guess.
- **Reverting the host's display fights the user.** → Only `remote-induced`
  changes are reverted, and only back to the view the host itself last held. If
  the trace (D2) shows the route is a host-app reaction that can be prevented
  outright, prefer prevention and drop the revert.
- **D2's trace does not name the route.** → Then the guard sits at the selection
  reaction, which is downstream of every candidate route and is where the
  display change becomes observable. Later than ideal, still correct.
- **Defect 2's real cause is inside `_switch_to_sequence_view`, so D4 alone
  changes nothing.** → Accepted and expected (D5). D4 is required by the spec on
  its own merits; D6 is what makes the residual cause visible.
- **Removing `_last_applied_*` causes redundant view switches.** → The displayed
  view is the comparison, so an instruction matching what is on screen is still
  a no-op — this is an explicit spec scenario and gets a test.
- **§5.1's guard deletion gets re-decided on this change's evidence.** → It must
  not. The three guards fired zero times in a session where the behaviour they
  guard was broken by another route, which is not evidence they are unnecessary.
  `docs/visibility_authority_guards.md` carries the inventory and the warning;
  the deletion needs a fresh soak taken *after* this change lands.

## Migration Plan

No wire-format, schema, or protocol change — every edit is peer-local, and a
patched peer interoperates with an unpatched one.

Order matters, and is driven by D5:

1. Provenance span + host-side logging (D2) — additive, no behaviour change.
2. Outcome recording in OpenRV's view block (D6) — makes the next soak legible.
3. Soak / reproduce both defects with 1–2 in place.
4. Displayed-view comparison in OpenRV (D4).
5. Host guard at the site step 3 names (D3).
6. `sync_test` coverage (D7).

Rollback: each step is independently revertable. Steps 1 and 2 are observation
only; the two behavioural changes (4, 5) are in different applications and do
not depend on each other.

## Open Questions

- **Which xStudio site owns the guard — the point of registration, or the
  selection reaction?** Deferred to step 3 of the migration plan, which names
  the route. Both sites satisfy the same spec scenarios and both are a single
  task in the same file set, so the answer changes the guard's placement but
  neither the specs nor the task breakdown.
- **How long the settle window after a remote apply needs to be.** The observed
  gap was 3.3 s, but that is one sample and includes poll latency. Size it from
  the traces collected in step 3.
