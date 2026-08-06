## 1. Core: categories and enforcement point

- [x] 1.1 Add the category table to `otio_sync_core` — visibility / position / annotation — mapping each `broadcast_*` method, and for playback specifically the *field groups* (`view_mode`/`clip_guid` = visibility, `current_time`/`playing`/`playback_mode` = position).
  - `python/otio_sync_core/authority.py`: `BROADCAST_CATEGORIES`, `VISIBILITY_FIELDS`, `POSITION_FIELDS`. `structure` is in the table for completeness but stays gated by `is_master` as today. `display_state` is filed under position per §7.1.
- [x] 1.2 Make `broadcast_*` return `SENT` / `SUPPRESSED`. Enforcement **disabled** at this step — always `SENT`. No behaviour change; this lands the shape only.
  - Landed as one pass with §3.1 rather than as two commits: with the kill switch in place from the start, a dark step would have been untestable dead code. `broadcast_add_annotation` is the one exception to the status return — callers need the annotation clip's GUID, and annotation is never gated.
- [x] 1.3 Strip visibility fields from a non-host broadcast in **one** place in core, not at call sites. A follower omitting `view_mode` but still carrying `clip_guid` is the easy mistake here (design.md Risks).
  - `SyncManager._enforce_visibility`, the single call being from `broadcast_playback_state`. Tested on the wire, including the `clip_guid`-without-`view_mode` case.
- [x] 1.4 Confirm no plugin tests "am I host" — authority is checked in core only, so the two hosts cannot drift the way they already have on hand-replicated behaviour.
  - Confirmed: no plugin gates a broadcast on the role. The two *local intent* branches (§4.4) do need to know, and go through one shared core predicate, `SyncManager.owns_visibility()`, rather than each application deciding — so they cannot drift either, and they honour the same kill switch. Plugin reads of `is_host` are logging only. Guarded by `test_no_plugin_gates_a_broadcast_on_being_host`.

## 2. Core: host election

- [x] 2.1 Elect a host by capability, preferring xStudio, falling back to any capable peer, deterministic tie-break by guid. Do **not** hard-code the application name — an RV-only session must still have a host.
  - `authority.elect_host_guid` is a pure function of the peer table; `HOST_PREFERENCE` ranks applications without requiring membership, so an unranked application still hosts when it is the only capable peer. Peers learn each other through a new `PEER_ANNOUNCE` message (announce on join, answer once, no storm). The sync viewer declares `capabilities=[]` so a passive observer can never be elected.
- [x] 2.2 Keep host distinct from master. Verify a master re-election does not change the host (design.md D2) — this is the failure mode of conflating them.
- [x] 2.2a Model host election on `fix-discovery-thread-safety` (archived 2026-08-05), which solved the identical problem for master election:
  - a **single** `elect_host()`-style operation owning every field the transition touches, in a documented order; no call site assembles the sequence itself;
  - **single-writer, not locks** — host state is mutated only by the poll thread; other threads enqueue. That change's explicit non-goal was making `SyncManager` thread-safe in general, and the same applies here;
  - **re-check eligibility at drain time, not enqueue time**, so a host discovered during queue latency cancels a pending election. Two peers electing simultaneously is the shared hazard;
  - ensure both applications leave **identical post-election state** — that change started setting `master_guid` on RV for exactly this reason. Every peer must reach the same host from the same inputs, which divergent post-election state makes impossible.
  - unit-test the *ordering* from inside a callback that fires during the transition, not just the end state — that change flagged an ordering slip as the failure most likely to surface as "a client that never loads".
  - All five satisfied: `SyncManager.elect_host()` owns the transition and callers are forbidden from assigning `host_guid`/`is_host`; other threads use `request_host_election()`, which enqueues onto a `queue.Queue` drained by the poll thread in `tick()` — no locks; `_drain_host_elections` re-evaluates the peer table at drain time, so a preferred peer that announced during the queue latency wins (`test_eligibility_is_re_checked_at_drain_time`); election lives wholly in core, so both applications reach identical post-election state by construction; and `test_callback_observes_a_fully_elected_manager` asserts the ordering from inside an `on_host_changed` callback.
- [x] 2.3 Carry the elected host in `STATE_SNAPSHOT` so a late joiner does not assume it is host and fight the real one.
  - `StateSnapshot.host_guid`, adopted via `SyncManager.adopt_host()`. Omitted from the payload when unset, and a `None` is ignored on receipt, so a peer predating the field cannot clear a locally-elected host.
- [x] 2.4 Expose the host in the test inspector's `/state` so the suite can assert who holds visibility — the harness cannot reason about a distinction it does not collect.
  - `is_host` / `host_guid` on both hooks (xStudio via the existing `export_state` file bridge), plus `view_mirror_error` on the OpenRV hook. All three are in the runner's `ignore_keys` — they differ between peers by construction, so they are for assertions, never for structural equality.
  - This paid for itself immediately: `_format_observed` now renders `… view=sequence host | … view=sequence follower`, which is what made a stale RV plugin visible (RV reported `follower` from core while its plugin banner lacked the new suffix — see the packaging note below).

## 3. Enable enforcement

- [x] 3.1 Turn enforcement on behind an env kill switch, mirroring `session-roles` D5, so a wrong category split can be reverted in a live session without a rebuild.
  - `ORI_VISIBILITY_AUTHORITY=0`. Read per call rather than cached at import, so it can be flipped in a running interpreter; it also reverts `owns_visibility()`, so the kill switch restores the old behaviour completely rather than half of it.
- [x] 3.2 Verify a follower's broadcasts never carry visibility fields — assert on the wire, not on intent.
- [x] 3.3 Verify a follower can still scrub, play/stop and annotate, and that peers honour it.
  - `tests/otio_sync/test_broadcast_authority.py`, asserting on the sent envelope: position, display, annotation and structure all still go out from a follower, and a suppressed message is still *sent* — only its visibility fields are gone.

## 4. Follower behaviour

- [x] 4.1 OpenRV: `on_view_changed` broadcasts visibility only when host.
  - Achieved without a plugin-side role check (per 1.4): the funnel through `broadcast_view_state` is unchanged, the manager strips the fields, and RV observes the returned `SUPPRESSED` only to log it.
- [x] 4.2 OpenRV: adopt the host's `view_mode`/`clip_guid` directly rather than deciding between sequence and isolated view locally (design.md D4).
  - A message with no `view_mode` — which is what a follower's stripped broadcast now looks like — leaves the local view alone and applies position only. Snap-back stays passive per §7.2.
- [x] 4.3 OpenRV: when the host's clip cannot be shown, report it rather than displaying the nearest local match — silent substitution is what made the wrong-clip divergence invisible.
  - `_report_mirror_failure` records and logs; every unresolvable path in `_switch_to_source_view` now reports instead of returning quietly. `_switch_to_sequence_view`'s "first non-source-group node" fallback is narrowed to the single-sequence case, where there is no choice to get wrong; with several candidates it reports rather than guessing. Surfaced as `view_mirror_error` and failed by the runner as a new `view_mirror_failed` fail kind.
- [x] 4.4 xStudio: make the intent inferences host-only — `playing_override` on a PSM transition, and the `_new_source_clip` frame reset. Under D3 the follower branches they guess about cannot arise.
  - Both now ask `manager.owns_visibility()` instead of "was the echo guard armed in the last 400 ms?". The deadline they used to read (`_playback_apply_suppress_until`) is still armed and still used by the position guards — only these two *uses* of it are retired.

## 5. Retire superseded guards

- [x] 5.1 Only after §3 has soaked: remove visibility-related echo suppression made unnecessary. Separate, revertible commit (design.md Migration 5).
  - **Soaked 2026-08-06. Outcome: do not delete.** The precondition was met — a live two-app session, xStudio host+master `44f3accf`, OpenRV follower `0cdfaa1f`. Resolved with a negative result rather than left open.
  - The three candidate guards fired **0 times** on both peers, which read alone says "inert, delete them". That reading does not survive the rest of the session: **a guard cannot be shown unnecessary by a session in which the behaviour it guards is broken by another route.**
  - The deletion's stated justification — *"a host's transitions are user-caused by definition"* — is **falsified**. The host isolated exactly the two clips the follower had isolated (`0090c5d3`, then `a407f8c8`), in the same order, seconds after registering the follower's `ADD_TIMELINE`. The host's transition was caused by a follower's structural message. With the premise gone, the deletion has no basis.
  - The enforcement this change exists for **does** work: OpenRV stripped visibility 284 times and sent `view_mode` zero times, and the host correctly rejected its clip-timeline position messages. The gap is that authority is defined over *fields* while this travels through *structure*.
  - Ownership moves to `fix-visibility-authority-bypass`, which carries the evidence and both defects. `docs/visibility_authority_guards.md` is updated so a later reader does not re-adopt the falsified premise from the inventory alone.
- [x] 5.2 Keep position-related apply-scope guards — position stays multi-writer, so its echoes are still real.
  - Nothing removed. The retained set is listed with its justification in the same document, including the guards a visibility *transition* triggers but which protect a *position* field — the frame-0 reset being the clearest.
- [x] 5.3 Record which guards were removed and which replaced them, so the next person can tell deliberate deletion from oversight.
  - `docs/visibility_authority_guards.md`. Also records the known residual: `timeline_guid` is not a visibility field and is not stripped, so a follower can still move a peer's `active_timeline_guid` bookkeeping — but not its view, which both applications gate on `view_mode`.

## 6. Test suite

- [x] 6.1 Retire `xstudio_selects_script_rv` — its premise (RV driving selection) is the behaviour being removed.
  - Deleted, with a comment in its place recording why it went and where its coverage moved, so it is not resurrected as a "missing test".
- [x] 6.2 Confirm `xstudio_selects_script_xstudio` covers the intended topology; extend it if it does not assert visibility propagation.
  - It does: xStudio drives and is the elected host, OpenRV follows. Not extended with new checkpoints; instead the assertion was strengthened for every test at once — a follower that cannot mirror the host's view now fails the run rather than reporting matching state while showing something else.
- [x] 6.3 Add a case for an RV-only session electing an RV host and broadcasting visibility normally — otherwise §2.1's fallback is untested.
  - `openrv_hosts_selection` (two OpenRV instances). It proves what §2.1 asks — an RV-only session elects a host, and that host drives and broadcasts visibility. It is nevertheless marked `known_broken`, because it fails one step *past* the thing it proves: the follower never materialised an RV source group for media that arrived by `REPLACE_TIMELINE`, so it cannot display the clip the host isolated. Pre-existing and unrelated to authority — §4.3's reporting is only what made it visible. Tracked as `fix-orphaned-structure-patches`.
  - Needed a harness capability, `drive_host: true`: with two equally-ranked peers the tie breaks on a random per-launch GUID, so driving `apps[0]` asserted visibility propagation *from a follower* half the time. `_select_host_driver` also has to wait for the peer set to settle rather than take the first `is_host` it sees — a peer briefly alone elects itself, so an early sample is true for a few hundred milliseconds and wrong thereafter.
- [x] 6.4 Assert follower divergence is detectable: media and view_mode are now reported per app, so a test can catch "same timeline name, different clip".
  - `_format_observed` now prints host/follower per app and any mirror failure, so a media/view split can be read as "the host's view" or "a follower's local drift" rather than an unattributed difference. `view_mode` stays out of structural equality, deliberately and as documented in `compare_states`.

## 6a. Packaging and harness fixes found by the live suite

- [x] 6a.1 Ship `otio_sync_core/authority.py` in the rvpkg. `rvplugin/ori_sync/makepackage.csh` vendors the library through a hand-maintained file list, and `__init__.py` imports it inside `try/except ImportError` — so a module missing from that list does not fail loudly, it makes every sync name absent and leaves the RV plugin connected but inert. Added to the list, with a comment saying why the list has to be kept in step.
- [x] 6a.2 Exclude clip timelines when the xStudio hook resolves the synced timeline name. `active_timeline_guid` can point at the single-clip timeline created by an isolation, which is named after the clip — a full media path in xStudio — so the hook reported `'/…/car_ACES_sRGB.mov'` where OpenRV reported `'Default Sequence'`: two synced peers describing different levels of one hierarchy, the exact fault that resolution was added to remove. `SyncManager` already applies this rule when syncing `active_timeline_guid` on a receiving peer; the harness now applies it too. **Pre-existing**, from the commit before this work (949ee90), not caused by the authority change — it accounted for four of the five reported failures.

## 7. Open questions to settle before implementing

- [x] 7.1 Does `display_state` (channel, colour) follow the host or stay per-peer? Leaning per-peer — reviewers legitimately toggle channels locally — but it is currently grouped with navigation in `session-roles`.
  - **Settled: per-peer, ungated.** `broadcast_display_state` is categorised **position**, so every peer may broadcast channel/exposure/pan/zoom as today. Reviewers legitimately toggle channels locally, and nothing in the wrong-clip divergence traces to display state.
- [x] 7.2 Is a follower's local visibility change snapped back on the next host broadcast, or left until the host next changes something?
  - **Settled: passive.** A follower keeps its local view until the host's next visibility broadcast, then adopts it. No re-assert-on-every-message machinery; matches `session-roles`' accepted "local divergence + snap-back" model. RV's existing transition detection (`_last_applied_*`) is what implements "adopt on change", so this is the no-extra-code answer as well as the less jarring one.
- [x] 7.3 Does host warrant a user-visible "take control" affordance, or is election enough for now?
  - **Settled: election only, no UI.** Host is elected by capability and exposed in `STATE_SNAPSHOT` and the test inspector, but neither plugin gains a menu action. An explicit claim would contradict the proposal's "no dynamic ownership handoff" non-goal; `elect_host` is shaped so a future `claim_host()` slots in without changing call sites.
