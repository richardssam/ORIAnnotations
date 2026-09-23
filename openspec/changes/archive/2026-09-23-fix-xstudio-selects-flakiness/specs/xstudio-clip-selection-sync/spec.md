## ADDED Requirements

<!--
DEFERRED — not delivered by this change.

The requirement below was implemented and reverted: the plugin cannot currently
observe a seek that lands on a not-yet-adopted playhead, so it has no ordering
information to gate on. Retained here (rather than deleted) because the
requirement itself is still the right one; only the mechanism was wrong. See
this change's tasks.md 5.5, and the KNOWN GAP note in broadcast_view_state's
_new_source_clip branch.

Delivering it needs the playhead-adoption path addressed first, which is
entangled with the event-group subscription work — do not re-attempt the
timestamp comparison on its own.
-->

### Requirement: An isolation's first-frame reset never discards a newer position

When isolating a clip resets the playhead to that clip's first frame, the reset
SHALL apply only if no position change has been established *after* the
selection event that triggered the isolation. If the playhead has since been
moved, the isolation SHALL keep the newer position and SHALL broadcast that
same position, rather than resetting to frame 0.

The invariant that the frame moved to and the frame announced are the same
frame SHALL be preserved in both cases.

#### Scenario: a seek after the selection survives the isolation

- **WHEN** a peer selects a clip to isolate
- **AND** the playhead is moved to a new position before the resulting
  isolation is resolved and applied
- **THEN** the isolation SHALL leave the playhead at that newer position on
  both peers, and SHALL broadcast that position

#### Scenario: an isolation with no intervening seek still opens at frame 0

- **WHEN** a peer isolates a clip and no position change occurs between the
  selection event and the isolation being applied
- **THEN** both peers SHALL show the clip's first frame, as before
