## Context

See proposal.md (Why) for the incident. The relevant code is all in `xstudio_plugin/ori_sync/playback_sync.py`. These handlers run on the poll thread, fed from one FIFO command queue:

- `handle_viewport_playhead` runs on the viewport's `viewport_playhead_atom` (Form-2). This is the first event of a container switch. It adopts the new playhead and already calls `get_viewed_container_safe()` to re-subscribe selection and container events.
- `handle_show_atom_media` runs on an on-screen media change. It picks `view_mode` in priority order: PSM False → `source`; `is_timeline` → `sequence`; `is_playlist` → `source`. `is_timeline`/`is_playlist` are the cached `_viewport_container_is_*` flags.
- `resolve_and_broadcast_selection` is the selection poll. It is the **only** writer of those flags. It announces `sequence` only on a PSM False→True transition.

In the incident the queue order was viewport (50.931) → show_atom (50.958) → poll (50.986). The show_atom read the flags the previous poll had left behind.

`get_local_viewed_timeline_guid` shows how a Timeline container maps to a sync guid. A published sequence matches `str(xs_tl.uuid)` for an entry in `plugin._sync_playlists` with `xs_tl is not None`. A flat playlist's internal Timeline maps to the Playlist's guid. Anything else is unpublished and returns the raw uuid.

## Goals / Non-Goals

**Goals:**
- A bin → sequence container switch on the xStudio host produces exactly one `view_mode: "sequence"` broadcast for that sequence.
- The show_atom path stops labelling that switch as a source isolation.

**Non-Goals:**
- The unpublished-sequence case (a Timeline not in `_sync_playlists`). That stays parked. This change doesn't announce it and doesn't change what gets sent for it.
- Sequence → sequence switches beyond what falls out of the same check. The check below also covers them, but they aren't the target and there's no separate handling.
- The OpenRV plugin and `otio_sync_core`. Receivers already switch on a real `view_mode`/`timeline_guid` transition (`xstudio-clip-selection-sync`, "a real sequence/timeline transition still switches the sequence view").
- Retiring the flat-playlist-internal-Timeline `is_timeline` classification in the show_atom path. It's existing behaviour, and this change only makes it arrive sooner.

## Decisions

### 1. Refresh the container-type flags in `handle_viewport_playhead`

Classify the container that `handle_viewport_playhead` already fetches, and write `_viewport_container_is_playlist` / `_viewport_container_is_timeline` there too. Classification follows `resolve_and_broadcast_selection`: Timeline → timeline, Playlist → playlist, Subset/ContactSheet → neither. Put the classification in one small helper that both sites call, so the two writers can't disagree.

Because the queue is FIFO and the viewport event comes first, the show_atom then reads the new container.

*Alternative considered:* read the container fresh inside `handle_show_atom_media`. Rejected because show_atoms fire once per edit during playback and scrub. A viewport round-trip on each one is the cost that `cached_viewed_timeline_guid` exists to avoid. The viewport event fires once per switch.

### 2. Announce the switch from the selection poll

In `resolve_and_broadcast_selection`, track the last viewed container uuid in a dedicated field. Don't reuse `_last_logged_container_uuid`, which also changes with the clip name. When the uuid changes, all of the following must hold:

- the new container is a Timeline whose uuid matches a published **sequence** entry in `_sync_playlists` (`xs_tl is not None`), which rules out flat-playlist internal Timelines and unpublished Timelines;
- the last polled PSM is True;
- this peer hasn't already announced that view (`_cur_view_mode == "sequence"` and `manager.active_timeline_guid` equals that sequence's guid).

If they do, set `manager.active_timeline_guid` to the sequence's guid. Then run `_view_assertion_is_echo(None, "sequence")`. If it's an echo, only `_record_current_view(None, "sequence")`. Otherwise call `broadcast_view_state(None, "sequence")`. That is the same shape as the existing PSM True branch.

This path is needed alongside Decision 1, not just as a backup to it. The show_atom for the switch can still be suppressed legitimately by `_is_scan_through` (the playhead moved as the new sequence loaded). The poll is the one place that sees the container change without depending on a media change.

*Alternative considered:* bypass `_is_scan_through` for the first show_atom after a container switch. Rejected because it adds another time window to a guard that already has a history of windows misfiring (see its docstring). The poll gives a deterministic trigger.

*Alternative considered:* use `manager.sequence_timeline_guid` like the PSM True branch does. Rejected because it names *a* sequence, not the one on screen. With more than one sequence it would announce the wrong one. Resolve from the container uuid instead.

### 3. Leave the show_atom's `source` branch alone

After Decision 1 the incident's show_atom sees `is_timeline` and chooses `sequence`. If it isn't suppressed it broadcasts first, and Decision 2's already-announced check then makes the poll a no-op. No change is needed to the priority order.

## Risks / Trade-offs

- [Two paths can announce the same switch] → Decision 2 checks `_cur_view_mode` and `active_timeline_guid`, which `broadcast_view_state` and the show_atom path already update. Whichever runs second finds the view already announced.
- [A peer-driven apply also switches the container on the receiver] → it's gated through `_view_assertion_is_echo`, which covers in-apply, remote-induced and applied-view echoes. The same gate already protects the PSM True branch.
- [`broadcast_view_state(None, "sequence")` carries the position from `current_playback_state()` on the newly adopted playhead] → same as the PSM True branch today. If the read fails, the existing path announces the view with no position.
- [Viewer role] → `broadcast_playback_state` already strips view fields for non-host roles. No extra gating is needed.

## Migration Plan

Plugin-only, with no wire change. Deploy by updating the xStudio plugin. Roll back by reverting the commit.
