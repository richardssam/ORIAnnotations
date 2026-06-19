"""Canonical state projection and diff for sync validation.

This module is the **single source of truth** for what "in sync" means when the
``sync_test`` framework compares a live client's state against a recorded
:class:`~otio_sync_core.protocol_messages.StateSnapshot` (or two clients against
each other).  It deliberately has **no OTIO or transport dependency** so both the
record/test side and the OpenRV/xStudio client integrations can import it.

The projection reduces a ``StateSnapshot``-shaped payload to a comparable
skeleton and *drops* fields that legitimately differ across applications or
machines (media URLs, color/OCIO metadata, available ranges, timestamps,
device-centric viewport values).  Equality is keyed on stable GUIDs
(``metadata.sync.guid`` for clips, the timeline GUID for timelines) because the
player preserves those GUIDs across record -> replay.

See ``openspec/changes/record-periodic-state-snapshots`` for the rationale.
"""

from __future__ import annotations

from typing import Any

# Field/metadata keys dropped from the projection because they legitimately
# differ across apps/machines and do not indicate desync.
_DROPPED_DISPLAY_KEYS = frozenset({"pan", "zoom", "exposure"})


def normalize_clip_name(name: Any) -> str:
    """Normalise a clip/timeline name for tolerant comparison.

    Mirrors the historical ``runner._normalize_clip_name`` so record-side and
    replay-side agree: case-folded, spaces removed, and the word ``sequence``
    stripped (OpenRV often appends/uses "Sequence" where xStudio does not).

    :param name: Raw name value (may be ``None``).
    :returns: Normalised name string.
    """
    return str(name or "").replace(" ", "").lower().replace("sequence", "")


def _clip_guid(child: dict[str, Any]) -> str | None:
    """Return a child's stable sync GUID, or ``None`` (e.g. for a Gap)."""
    return child.get("metadata", {}).get("sync", {}).get("guid")


def _project_track(track: dict[str, Any]) -> dict[str, Any]:
    """Project a single OTIO track-wire-dict to ordered clip entries.

    All children are kept in order (clips *and* gaps) so reordering is
    detectable; gaps contribute a ``None`` GUID and an empty name.
    """
    entries = []
    for child in track.get("children", []) or []:
        entries.append(
            {
                "guid": _clip_guid(child),
                "name": normalize_clip_name(child.get("name")),
            }
        )
    return {
        "name": normalize_clip_name(track.get("name")),
        "kind": track.get("kind"),
        "clips": entries,
    }


def _project_timeline(timeline: dict[str, Any]) -> dict[str, Any]:
    """Project a single OTIO timeline-wire-dict to its comparable skeleton.

    The Annotations track is excluded: apps represent annotations differently in
    their manager timeline (OpenRV keeps a clip per partial stroke; xStudio uses
    bookmarks and keeps no annotation clips), so a clip-by-clip comparison there
    is apples-to-oranges. Annotation sync is validated by the presence check, not
    structurally.
    """
    tracks = timeline.get("tracks", {}).get("children", []) or []
    return {
        "name": normalize_clip_name(timeline.get("name")),
        "tracks": [
            _project_track(t) for t in tracks
            if normalize_clip_name(t.get("name")) != "annotations"
        ],
    }


def _is_clip_timeline(timeline: dict[str, Any] | None) -> bool:
    """True if *timeline* is a single-clip-view timeline (``clip_timeline_for``).

    These are created lazily per-peer for single-clip view, so they are local
    view state rather than synced sequence structure and must be excluded from
    cross-peer comparison.
    """
    if not isinstance(timeline, dict):
        return False
    return bool(timeline.get("metadata", {}).get("clip_timeline_for"))


def _resolve_active(timelines: dict[str, Any], active_guid: Any) -> Any:
    """Resolve the active timeline GUID for comparison.

    Apps differ on how single-clip view sets the active timeline: xStudio points
    it at a per-clip timeline, OpenRV keeps it on the sequence. Normalise by
    resolving a clip-timeline active to the sequence that contains its clip, so
    both compare as "in the same sequence" regardless of view-mode representation.
    """
    active_tl = timelines.get(active_guid)
    if not _is_clip_timeline(active_tl):
        return active_guid
    clip_guid = active_tl.get("metadata", {}).get("clip_timeline_for")
    for guid, tl in timelines.items():
        if _is_clip_timeline(tl):
            continue
        for track in tl.get("tracks", {}).get("children", []) or []:
            for child in track.get("children", []) or []:
                if child.get("metadata", {}).get("sync", {}).get("guid") == clip_guid:
                    return guid
    return None  # clip's sequence not present — cannot resolve


def _project_frame(playback_state: dict[str, Any] | None) -> Any:
    """Extract the current frame from a playback-state dict, or ``None``.

    A missing frame means "not asserted" — never a mismatch.
    """
    if not playback_state:
        return None
    current = playback_state.get("current_time")
    if isinstance(current, dict):
        return current.get("value")
    return None


def _project_display(display_state: dict[str, Any] | None) -> dict[str, Any]:
    """Project display state, dropping device-centric viewport values.

    ``pan``/``zoom``/``exposure`` are local/device-centric and dropped.  Any
    remaining keys (e.g. a synced view/display target) are kept verbatim.
    """
    if not display_state:
        return {}
    return {
        k: v for k, v in display_state.items() if k not in _DROPPED_DISPLAY_KEYS
    }


def project_state(snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a ``StateSnapshot`` payload to its canonical comparable form.

    :param snapshot_payload: The ``command.payload`` dict of a ``STATE_SNAPSHOT``
        message (or any dict with ``timelines``/``active_timeline_guid``/
        ``playback_state``/``display_state`` keys).  Media URLs, color metadata,
        available ranges and timestamps inside the timelines are simply not read,
        so they cannot cause a false mismatch.
    :returns: A canonical-state dict: ``active_timeline``, ``frame``,
        ``timelines`` (keyed by GUID), and ``display``.
    """
    timelines = snapshot_payload.get("timelines", {}) or {}
    return {
        # Active timeline, with a clip-timeline active resolved to its containing
        # sequence so view-mode representation differences don't false-mismatch.
        "active_timeline": _resolve_active(
            timelines, snapshot_payload.get("active_timeline_guid")
        ),
        "frame": _project_frame(snapshot_payload.get("playback_state")),
        "timelines": {
            guid: _project_timeline(tl) for guid, tl in timelines.items()
            # Exclude single-clip-view timelines (metadata.clip_timeline_for):
            # peers create these lazily based on local view mode, so they diverge
            # when one app is in single-clip view and the other in sequence view.
            # They are not synced sequence structure.
            if not _is_clip_timeline(tl)
        },
        "display": _project_display(snapshot_payload.get("display_state")),
    }


def _diff_track(tl_guid: str, expected: dict[str, Any], actual: dict[str, Any],
                messages: list[str]) -> None:
    """Append clip-level differences for one track to *messages*."""
    track_name = expected.get("name") or actual.get("name")
    exp_clips = expected.get("clips", [])
    act_clips = actual.get("clips", [])

    exp_guids = [c["guid"] for c in exp_clips if c["guid"]]
    act_guids = [c["guid"] for c in act_clips if c["guid"]]
    exp_set, act_set = set(exp_guids), set(act_guids)

    for missing in exp_set - act_set:
        name = next((c["name"] for c in exp_clips if c["guid"] == missing), "")
        messages.append(
            f"timeline {tl_guid[:8]} track '{track_name}': missing clip "
            f"{missing[:8]} ('{name}')"
        )
    for extra in act_set - exp_set:
        name = next((c["name"] for c in act_clips if c["guid"] == extra), "")
        messages.append(
            f"timeline {tl_guid[:8]} track '{track_name}': unexpected clip "
            f"{extra[:8]} ('{name}')"
        )

    # Reorder: same GUID set but different order.
    if exp_set == act_set and exp_guids != act_guids:
        messages.append(
            f"timeline {tl_guid[:8]} track '{track_name}': clips reordered "
            f"(expected {[g[:8] for g in exp_guids]}, got {[g[:8] for g in act_guids]})"
        )


def diff_states(expected: dict[str, Any], actual: dict[str, Any],
                frame_tolerance: int = 5, compare_frame: bool = True) -> list[str]:
    """Diff two canonical states (from :func:`project_state`), GUID-keyed.

    :param expected: The expected canonical state (e.g. from a recorded snapshot).
    :param actual: The actual canonical state (e.g. from a live client).
    :param frame_tolerance: Allowed absolute frame difference.
    :param compare_frame: If False, skip the frame comparison entirely. Use this
        for the vs-recording (oracle) checkpoint: a snapshot's frame is a
        point-in-time, and live playback advances past it by validation time even
        when both clients stay in lockstep. Frame agreement is asserted instead
        by the dedicated frame checkpoints and by client-vs-client consensus.
    :returns: A list of human-readable difference strings; empty means a match.
    """
    messages: list[str] = []

    # Active timeline.
    if expected.get("active_timeline") != actual.get("active_timeline"):
        messages.append(
            f"active timeline mismatch: expected "
            f"{expected.get('active_timeline')}, got {actual.get('active_timeline')}"
        )

    # Frame (skip if disabled, or if either side did not assert one).
    exp_frame = expected.get("frame")
    act_frame = actual.get("frame")
    if compare_frame and exp_frame is not None and act_frame is not None:
        if abs(float(exp_frame) - float(act_frame)) > frame_tolerance:
            messages.append(
                f"frame mismatch: expected ~{exp_frame}, got {act_frame} "
                f"(tolerance {frame_tolerance})"
            )

    # Timeline set, keyed by GUID.
    exp_tls = expected.get("timelines", {})
    act_tls = actual.get("timelines", {})
    for missing in set(exp_tls) - set(act_tls):
        messages.append(f"missing timeline {missing[:8]} ('{exp_tls[missing]['name']}')")
    for extra in set(act_tls) - set(exp_tls):
        messages.append(f"unexpected timeline {extra[:8]} ('{act_tls[extra]['name']}')")

    # Per common timeline: compare tracks by name.
    for guid in set(exp_tls) & set(act_tls):
        exp_tracks = {t["name"]: t for t in exp_tls[guid].get("tracks", [])}
        act_tracks = {t["name"]: t for t in act_tls[guid].get("tracks", [])}
        for tname in set(exp_tracks) | set(act_tracks):
            if tname not in act_tracks:
                messages.append(f"timeline {guid[:8]}: missing track '{tname}'")
            elif tname not in exp_tracks:
                messages.append(f"timeline {guid[:8]}: unexpected track '{tname}'")
            else:
                _diff_track(guid, exp_tracks[tname], act_tracks[tname], messages)

    return messages
