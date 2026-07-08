## ADDED Requirements

### Requirement: Sequence-mode clip_guid is playhead-derived and NOT actioned by the receiver
In sequence view a `PLAYBACK_SETTINGS_1.0` `clip_guid` reflects the clip under the sender's playhead (emitted on media-change), not an explicit selection — there is no distinct selection signal in sequence mode (the sender's timeline selection actor stays empty during interaction). Because the field changes on scrubbing, playback, and the initial connect, a receiver cannot distinguish "user selected a clip" from "playhead moved onto a clip". Therefore a receiver SHALL NOT change its view in response to a sequence-mode `clip_guid` change: it stays on the sequence. Explicit clip isolation flows through `view_mode: "source"` (double-click in xStudio) instead, which is out of scope of this behaviour and unchanged.

#### Scenario: scrubbing a sequence does not change the receiver's view
- **WHEN** a peer scrubs (or plays) across cuts in sequence view, broadcasting `view_mode: "sequence"` messages whose `clip_guid` changes while the timeline is unchanged
- **THEN** the receiving peer stays on the sequence and does not switch to any clip's isolated/source view

#### Scenario: initial connect leaves the receiver on its sequence
- **WHEN** a receiver already showing a sequence receives its first sequence-mode message on connect, carrying the peer's playhead `clip_guid`
- **THEN** the receiver continues to show the sequence and does not switch to that clip

#### Scenario: a real sequence/timeline transition still switches the sequence view
- **WHEN** a receiver receives a `view_mode: "sequence"` message whose view mode or `timeline_guid` actually changed (entering sequence mode, or opening a different sequence)
- **THEN** the receiver switches to that sequence view

### Requirement: xStudio re-enables its timeline item-highlight behind a stability guard
xStudio's timeline item-highlight (`item_selection_atom`) — previously disabled outright due to a crash when sent into a recently-rebuilt timeline — SHALL be re-enabled for the clip-isolation paths that already carry a clip identity (the source-mode `apply_selection` flow), guarded by a timeline-stability check that skips the send (without error) if the target timeline was structurally rebuilt within a short recency window. All `item_selection_atom` sends SHALL be gated by this guard; none may send unconditionally.

#### Scenario: highlight applies on a stable timeline
- **WHEN** xStudio applies a clip isolation whose target timeline has not been structurally rebuilt within the stability window
- **THEN** xStudio highlights the corresponding clip in its timeline view

#### Scenario: highlight is skipped on a recently-rebuilt timeline
- **WHEN** the target timeline was structurally rebuilt within the stability window
- **THEN** xStudio skips the `item_selection_atom` send rather than risk the known actor-teardown race
- **AND** no error is raised to the user and no other state is affected

### Requirement: Isolated-clip (source-mode) selection is broadcast reliably from the selection event
When the user isolates a clip in xStudio (single-clip / `Pinned Source Mode = False`), xStudio SHALL broadcast that clip as `view_mode: "source"` driven by the selection-actor event and single-clip state — not solely by the `show_atom` media-change — so that a selection is not dropped when the displayed media does not change (e.g. re-selecting an already-shown clip) or a `show_atom` broadcast is suppressed. The broadcast SHALL be deduped against the last-broadcast clip so it does not double-fire, and gated to single-clip mode so it cannot fire while scrubbing a sequence. The first such selection SHALL NOT be mislabelled as `view_mode: "sequence"` because of a stale cached Pinned Source Mode (PSM SHALL be read freshly for a short window after a selection event).

#### Scenario: re-selecting an already-shown clip still broadcasts
- **WHEN** the user isolates a clip whose media is already on screen (no `show_atom` media-change fires)
- **THEN** xStudio still broadcasts that clip as `view_mode: "source"`

#### Scenario: the first isolated selection is labelled source, not sequence
- **WHEN** the user makes the first "select a clip in the timeline" selection, entering single-clip mode
- **THEN** xStudio broadcasts it as `view_mode: "source"` (reading Pinned Source Mode freshly), not `view_mode: "sequence"`

### Requirement: Isolating a clip seeks both peers to the clip's first frame
When a peer isolates a clip that differs from the currently-isolated one, both peers SHALL show the clip's first frame (`current_time.value = 0` in the isolated clip's 0-based frame space, i.e. `source_range.start`), rather than each restoring its own last-viewed position within that clip. Scrubbing *within* an isolated clip SHALL be unaffected (it is not a new isolation).

#### Scenario: a previously-viewed clip opens at its first frame
- **WHEN** a peer isolates a clip it (or the other peer) has viewed before and left partway through
- **THEN** both peers seek to that clip's first frame, not the last-viewed position

### Requirement: Isolated clips loop for review
An isolated single clip SHALL play in Loop mode on both peers, rather than the engine's per-clip Play Once default. While in single-clip mode xStudio SHALL be authoritative for the clip's loop mode: it SHALL report Loop for its own broadcasts and SHALL ignore an incoming `playback_mode: "play-once"` from a peer (which would otherwise revert the clip to Play Once and produce a loop↔play-once oscillation). Sequence mode SHALL continue to respect the peer's playback mode.

#### Scenario: an isolated clip loops on both peers
- **WHEN** a peer isolates a clip and plays it
- **THEN** the clip loops continuously on both peers (past its end), and play at the clip end restarts it

#### Scenario: a peer echoing play-once does not revert the loop
- **WHEN** a peer broadcasts `playback_mode: "play-once"` while xStudio is in single-clip mode
- **THEN** xStudio keeps the isolated clip in Loop and does not revert to Play Once

### Requirement: Selection-path playhead operations must not freeze the app
Reads and writes to the active playhead on the selection/broadcast path (position, loop mode, playback state) SHALL be bounded, so that a playhead actor torn down at a clip's end (leaving a stale reference) cannot block the poll thread for the full default request timeout (~100 s). On timeout the operation SHALL fail fast and the plugin SHALL recover by re-acquiring a live playhead.

#### Scenario: a clip ending does not wedge the app
- **WHEN** a clip finishes and its playhead actor is torn down, then another selection or broadcast reads/writes the (now stale) active playhead
- **THEN** the operation fails within the bounded timeout and the plugin continues, rather than freezing

### Requirement: Applying an incoming clip change does not echo back to the sender
Applying an incoming clip isolation/highlight SHALL NOT itself trigger a new outbound `PLAYBACK_SETTINGS_1.0` broadcast from the receiving peer for that same selection.

#### Scenario: applying a peer's selection does not bounce back
- **WHEN** a peer applies an incoming clip isolation/highlight
- **THEN** that peer does not broadcast a `PLAYBACK_SETTINGS_1.0/SET` message carrying the same `clip_guid` as an echo of the just-applied change
