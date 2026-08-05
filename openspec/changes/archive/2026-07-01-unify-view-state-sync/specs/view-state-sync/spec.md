## ADDED Requirements

### Requirement: Single Authoritative View-State Message

The system SHALL describe "what a peer is viewing" with a single message,
`PLAYBACK_SETTINGS_1.0/SET`, extended to carry `view_mode` (`"sequence"` or
`"source"`) and `clip_guid` (nullable) in addition to the existing
`timeline_guid`, `current_time`, `playing`, `looping`, and `sync_timestamp`
fields. There SHALL NOT be a second, independent channel describing selection or
view state.

#### Scenario: View-state message carries mode and clip
- **WHEN** a peer broadcasts its view state
- **THEN** the message SHALL include `view_mode` and `clip_guid` alongside the
  timeline guid, current time, and play state, so a single message fully
  describes what that peer is viewing.

#### Scenario: SELECTION_1.0 is retired
- **WHEN** the protocol is in use
- **THEN** no `SELECTION_1.0` message SHALL be produced or consumed; selection
  state SHALL be conveyed only by the view-state fields of
  `PLAYBACK_SETTINGS_1.0`.

### Requirement: Sequence-Mode Position Is Authoritative

In `view_mode = "sequence"`, the `timeline_guid` and `current_time` SHALL be the
authoritative description of the view. The receiver SHALL position its playhead
to `current_time` within that sequence and SHALL derive the active clip from the
track's clip `source_range` durations. `clip_guid` SHALL be treated as
confirmation/highlight only and SHALL NOT trigger a seek.

#### Scenario: Follower derives the clip from the frame
- **WHEN** a sequence-mode view state arrives with a `current_time`
- **THEN** the receiver SHALL seek to that frame in the sequence and determine
  the active clip by summing preceding clip `source_range` durations, rather
  than seeking to any clip's start based on `clip_guid`.

#### Scenario: Crossing a clip boundary while scrubbing does not snap
- **WHEN** a peer scrubs across a clip boundary in a sequence and broadcasts the
  new position
- **THEN** the follower SHALL track the broadcast position and SHALL NOT jump to
  the start of the newly active clip.

### Requirement: Source-Mode Clip Is Authoritative

In `view_mode = "source"`, the `clip_guid` SHALL be the authoritative
description of the isolated single clip being viewed, and `current_time` SHALL
be interpreted as the offset within that clip's source.

#### Scenario: Follower isolates the broadcast clip
- **WHEN** a source-mode view state arrives with a `clip_guid`
- **THEN** the receiver SHALL switch to single-clip/source view of that clip and
  seek to the in-clip offset given by `current_time`.

### Requirement: Single Broadcast and Apply Paths With Echo Suppression

Each plugin SHALL compute and broadcast its view state through one path fed by
every view-affecting event (playhead move, selection/on-screen change, view-mode
change), and SHALL apply an incoming view state through one atomic path. While
applying a remote view state, the plugin SHALL suppress re-broadcasting the local
events that the apply itself triggers.

#### Scenario: Applying a remote view state does not echo
- **WHEN** a peer applies a received view state and that application causes local
  playhead/selection events to fire
- **THEN** those events SHALL NOT be re-broadcast within the echo-suppression
  window, so two peers do not enter a feedback loop.

#### Scenario: Peers converge rather than oscillate
- **WHEN** two peers begin on different active clips and one broadcasts its view
  state
- **THEN** because position is authoritative and the clip is derived, both peers
  SHALL converge on the same frame and clip rather than swapping selections
  back and forth.

### Requirement: Mode and Position Applied Atomically

The receiver SHALL apply `view_mode`, source/timeline, and position together as a
single unit, so the displayed clip and the displayed frame cannot contradict each
other.

#### Scenario: No transient disagreement on apply
- **WHEN** a view state is applied
- **THEN** the on-screen source, the active clip, and the playhead frame SHALL be
  set as one operation, leaving no window in which the clip and frame disagree.
