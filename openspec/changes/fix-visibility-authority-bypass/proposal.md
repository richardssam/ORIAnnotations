## Why

`host-owned-visibility` made visibility single-writer by stripping `view_mode`
and `clip_guid` from a follower's broadcasts. A live two-app soak on 2026-08-06
confirms that rule holds on the wire — OpenRV, as follower, stripped visibility
**284 times and sent `view_mode` zero times** — and confirms it is **not
sufficient**. Two defects were observed that the scripted suite has never
surfaced across eleven runs.

**1. A follower changes what the host displays, without broadcasting
visibility.**

```
20:38:19      RV isolates clip 0090c5d3 (graphic)   → visibility stripped ✓
20:38:20      RV isolates clip a407f8c8 (laser)     → visibility stripped ✓
20:38:20.556  host: ADD_TIMELINE from RV → registered clip_tl=927c7206
20:38:23.824  host: [SEL] Selection event fired (source_atom)   ← host's own
20:38:24.256  host: → broadcast view-state clip 0090c5d3 mode=source
20:38:24.948  host: → broadcast view-state clip a407f8c8 mode=source
```

The host isolated **exactly the two clips the follower had isolated, in the same
order**. Isolating a clip in OpenRV creates a clip timeline and broadcasts
`ADD_TIMELINE` — structure, correctly not stripped. Registering it fires the
host's own selection machinery as a side effect, and the host then broadcasts
the result as visibility, legitimately, because it is the host.

The follower never sent visibility. It did not need to. Enforcement is defined
over *fields*, and this travels through *structure*.

The host's other defences did hold and are not the gap: it rejected the
follower's position messages carrying a clip-timeline GUID
(`mismatched timeline_guid — ignoring`).

**2. The host cannot pull a diverged follower back.**

At `20:38:26.899` OpenRV received `mode=sequence` and did nothing for six
seconds — no view switch, no `MIRROR FAILED`, no log line. At `20:38:32` it was
still displaying its own isolated clip. Two peers silently disagreeing about
what is on screen is the exact failure `Followers mirror visibility rather than
deriving it` exists to prevent, and it produced no signal at either end.

Together these mean a session can diverge and stay diverged: (1) starts it, (2)
makes it permanent.

## What Changes

- Visibility authority SHALL be defined over the **displayed outcome**, not only
  over the fields of one message. A non-host peer's action SHALL NOT cause the
  host to change what it displays, by any route.
- A peer SHALL compare a remote view against **what it is currently
  displaying**, not against the last view it happened to adopt from a peer. A
  local view change must not make a subsequent remote instruction look
  redundant.
- A follower that declines or fails to adopt the host's view SHALL report it.
  Silence is currently indistinguishable from compliance — the divergence above
  produced zero `MIRROR FAILED` records.
- **Inherits the superseded-guard deletion** from `host-owned-visibility` §5.1,
  which is closed with the outcome *do not delete*. That task's justification was
  "a host's transitions are user-caused by definition"; defect 1 falsifies it,
  since the host's transition was caused by a follower's structural message. The
  three candidate guards fired 0 times in the soak, but a guard cannot be shown
  unnecessary by a session in which the behaviour it guards is broken by another
  route. Re-decide the deletion only once the bypass is closed;
  `docs/visibility_authority_guards.md` carries the inventory and the warning.

## Capabilities

### Modified Capabilities
- `session-visibility-authority`: authority is defined over the displayed
  outcome rather than over message fields alone; a peer that does not adopt the
  host's view reports it.
- `openrv-sync-plugin`: a remote view is compared against the view actually
  displayed, so a local change cannot suppress a later remote instruction.

## Impact

- `xstudio_plugin/ori_sync/` — the host's reaction to a peer's structural
  registration is what converts a follower's action into a host view change.
  Whether the fix belongs at registration or at the selection reaction is a
  design question, not settled here.
- `rvplugin/ori_sync/playback_sync.py` — `_last_applied_view_mode` /
  `_last_applied_clip_guid` / `_last_applied_tl_guid` and the branch at ~line
  189 that gates view adoption on them.
- `openspec/changes/host-owned-visibility` — §5.1 stays blocked; this change
  owns the question.
- `sync_test/` — no existing test covers either defect; eleven suite runs missed
  both. Coverage needs a follower that changes its own view, which the suite
  never does.

## Evidence

Logs: `rvplugin/ori_sync/xstudio_host.log`, `rvplugin/ori_sync/rv_client.log`
(2026-08-06 soak; xStudio host+master `44f3accf`, OpenRV follower `0cdfaa1f`).

Confidence differs between the two, and should not be levelled up when this is
read later:

- **Defect 1 is directly evidenced.** The clip GUIDs, their order, and the
  message sequence are all in the logs.
- **Defect 2's symptom is directly evidenced; its mechanism is inferred.** The
  reading is that `mode_changed` compares against the last *remotely applied*
  view rather than the displayed one, so a local change leaves it stale. The
  variable's state at that instant was not observed, and OpenRV did perform 13
  remote view switches over the session, so the path works in general. Confirm
  before fixing.
