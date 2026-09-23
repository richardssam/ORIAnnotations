# Capability: xstudio-clip-selection-sync

## Purpose

Synchronization of the isolated-clip (source-mode) selection workflow between xStudio and OpenRV over `PLAYBACK_SETTINGS_1.0`: double-clicking / "selecting a clip" in xStudio reflects reliably in RV, seeks both peers to the clip's first frame, and loops the clip for review. Also records the deliberate decision that a sequence-mode `clip_guid` — which is playhead-derived and carries no distinct selection signal — is NOT actioned by the receiver.

## Requirements

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

This SHALL hold for any selection change attributable to a remote apply, not only for a directly applied clip change. Applying a remote *position* moves the local playhead, which can raise a selection event of its own; that event is a consequence of the remote message, and reporting it as a local selection sends a peer's own action back to it as an instruction. Where the plugin already attributes an event to a recent remote apply, that attribution SHALL gate the broadcast rather than only annotate the log.

#### Scenario: applying a peer's selection does not bounce back
- **WHEN** a peer applies an incoming clip isolation/highlight
- **THEN** that peer does not broadcast a `PLAYBACK_SETTINGS_1.0/SET` message carrying the same `clip_guid` as an echo of the just-applied change

#### Scenario: a selection raised by applying a remote position is not reported as local
- **WHEN** applying a remote position moves the local playhead and that raises a selection event
- **AND** the plugin attributes that event to the remote apply it has just performed
- **THEN** it SHALL NOT broadcast the event as a local selection
- **AND** the peer that sent the position SHALL NOT be instructed to change its view

#### Scenario: a genuine local selection is still broadcast
- **WHEN** the user changes the selection locally, with no recent remote apply to attribute it to
- **THEN** the selection SHALL be broadcast as before

### Requirement: Playback addressed to an isolated clip is followed, not discarded

While a peer views an isolated clip, its `PLAYBACK_SETTINGS_1.0` messages carry a `timeline_guid` identifying that clip's **clip timeline** rather than the sequence. A receiver SHALL resolve such a `timeline_guid` and apply the playback state, so that play, stop, and scrub inside an isolated clip keep peers in step.

Resolution SHALL use the existing deterministic derivation — every peer computes a clip timeline's guid from the clip's own guid without coordination — so no additional field, message, or handshake is introduced. A receiver therefore SHALL be able to resolve the guid for any clip it knows, whether or not it has itself created that clip timeline.

A `timeline_guid` matching neither a known timeline nor a clip timeline of a known clip SHALL continue to be ignored: this requirement widens what is resolvable, it does not remove the guard.

The frame carried in such a message is clip-local. Applying it SHALL leave the receiver showing the same image as the sender, on both hosts.

#### Scenario: Stopping inside an isolated clip stops the peer

- **WHEN** a peer isolates a clip, plays, and then stops at a clip-local frame
- **THEN** the receiving peer SHALL stop
- **AND** SHALL land on the same image, rather than continuing to play

#### Scenario: Scrubbing inside an isolated clip moves the peer

- **WHEN** a peer scrubs within an isolated clip
- **THEN** the receiving peer SHALL follow to the same image

#### Scenario: A receiver that never created the clip timeline still follows

- **WHEN** playback arrives addressed to the clip timeline of a clip the receiver knows, but whose clip timeline the receiver has not itself created
- **THEN** the receiver SHALL resolve the guid from the clip and follow the playback
- **AND** SHALL NOT require any prior announcement of that clip timeline

#### Scenario: An unresolvable timeline is still ignored

- **WHEN** playback arrives with a `timeline_guid` that matches no known timeline and no clip timeline of any known clip
- **THEN** the receiver SHALL ignore it, as before

#### Scenario: Returning to the sequence is unaffected

- **WHEN** a peer leaves the isolated clip and returns to sequence view
- **THEN** playback addressed to the sequence timeline SHALL be applied as it is today

### Requirement: The echo guard covers the whole period a peer is driving playback
A peer that receives a `PLAYBACK_SETTINGS_1.0` message SHALL treat itself as being driven by another peer, and SHALL NOT broadcast its own playhead position, for a short rolling period after that message — regardless of whether it applied the message.

This SHALL hold for messages the receiver deliberately declines to apply (a mismatched `timeline_guid`, a play-state-only update), because the receiver's own playhead can still move during that period for unrelated local reasons — most commonly a selection change resetting it to a clip start — and broadcasting that position would override the seek the driver just issued.

#### Scenario: A dropped message still suppresses the receiver's position broadcasts
- **WHEN** a peer receives a playback message it does not apply
- **THEN** it SHALL NOT broadcast its own playhead position for the guard period
- **AND** the driving peer's position SHALL remain in effect

#### Scenario: A position sampled before the guard armed is not released after
- **WHEN** a peer has a throttled position update pending, and a remote playback message arrives before that update is flushed
- **THEN** the pending update SHALL be discarded rather than broadcast
- **AND** the discard SHALL be reported, so a dropped local scrub is visible rather than silent

#### Scenario: Every broadcast path honours the guard
- **WHEN** any code path would broadcast this peer's playhead position while the guard is armed
- **THEN** that position SHALL be withheld
- **AND** a broadcast carrying information beyond position — a view or mode change — SHALL still be delivered, with only the position withheld

#### Scenario: The guard expires once the peer stops driving
- **WHEN** no playback message has been received for longer than the guard period
- **THEN** the peer SHALL resume broadcasting its own playhead position normally

### Requirement: A peer-driven view transition is not reported as a local play action
xStudio starts playing when a user double-clicks a clip, and broadcasts that so peers follow. A peer-driven view switch produces the identical local transition, so the transition alone SHALL NOT be treated as evidence of a local user action.

A peer SHALL assert `playing=true` on such a transition only when it is not currently being driven by another peer. When it is, the broadcast SHALL NOT assert play, leaving the play state to whichever peer is actually driving.

#### Scenario: A remotely-caused view switch does not start playback everywhere
- **WHEN** a peer's view transitions to an isolated clip as a result of applying a remote message
- **THEN** it SHALL NOT broadcast that playback has started
- **AND** no peer SHALL begin playing as a result

#### Scenario: A genuine local double-click still starts playback on all peers
- **WHEN** a user double-clicks a clip locally, with no peer driving playback
- **THEN** the peer SHALL broadcast that playback has started, as before

### Requirement: View convergence and isolation under peer contention
The system SHALL converge on the host's view when a viewer joins or the host reasserts a view, while preserving local isolation if a viewer explicitly changes their own view.

#### Scenario: A viewer joins an established session
- **WHEN** a viewer joins an active session where the host is viewing a specific frame of an isolated clip
- **THEN** the viewer SHALL converge to show the same clip and frame

#### Scenario: A viewer makes a local selection
- **WHEN** a viewer in a synchronized session selects a different clip locally
- **THEN** the viewer's view SHALL change to the new clip
- **AND** the host's session SHALL NOT be affected

#### Scenario: Host reasserts their view
- **WHEN** a viewer has isolated a different clip, and the host subsequently scrubs or changes frames on their own clip
- **THEN** the viewer SHALL switch back to the host's clip and match the host's playhead position

### Requirement: Flat playlist clip isolation classification
The plugin SHALL accurately classify the isolation of a clip within a flat playlist as a `source` mode view, and SHALL NOT misclassify it as `sequence` mode. The fallback view state resolution logic SHALL respect the `xs_flat_playlist` timeline metadata flag to prevent misattributing flat playlist media to sequence timelines.

#### Scenario: Isolating media in a flat playlist
- **WHEN** a user isolates a clip in a playlist without a timeline (flat playlist)
- **THEN** the plugin SHALL broadcast `view_mode: "source"`
- **AND** the plugin SHALL NOT attribute the media to a synthetic or unrelated sequence timeline

### Requirement: Opening a sequence in xStudio is announced as a sequence view

When the user opens a published sequence in xStudio (for example by double-clicking it in the bin), the viewed container changes from a Playlist to that sequence's Timeline. Pinned Source Mode does not change. xStudio SHALL announce this as `view_mode: "sequence"`, with that sequence's sync `timeline_guid`. It SHALL NOT announce it as an isolation (`view_mode: "source"`) of the clip under the playhead. The announcement SHALL NOT depend on the order in which xStudio's viewport, media-change and selection events arrive. It SHALL still be made when the media-change event for the switch is suppressed as scan-through.

The announcement SHALL be made only for a Timeline that is a published sync sequence. A flat playlist's internal Timeline, which xStudio's viewport reports once a playlist starts playing, is not a sequence. Neither is a Timeline the session hasn't been told about. The announcement SHALL NOT be made when this peer already announced that same sequence view, and SHALL NOT be made when the switch was caused by applying a peer's message.

#### Scenario: double-clicking a sequence from the bin switches the peer to the sequence
- **WHEN** the xStudio host is viewing the bin (a Playlist) and double-clicks a published sequence
- **THEN** xStudio broadcasts `view_mode: "sequence"` with that sequence's `timeline_guid`
- **AND** it does not broadcast `view_mode: "source"` for the clip under the playhead
- **AND** the receiving peer switches to the sequence view and follows subsequent scrubs of it

#### Scenario: the media-change for the switch arrives before the selection poll
- **WHEN** xStudio reports the new on-screen media for the opened sequence before the plugin's selection poll has observed the container change
- **THEN** the media-change is classified against the sequence's Timeline container, not the previously viewed Playlist

#### Scenario: the switch is still announced when the media-change is suppressed
- **WHEN** the media-change event for the opened sequence is suppressed as scan-through (the playhead was moving)
- **THEN** xStudio still broadcasts `view_mode: "sequence"` for that sequence once it observes the container change

#### Scenario: playing a flat playlist is not announced as a sequence
- **WHEN** the user starts playback in a flat playlist and xStudio's viewport starts reporting that playlist's internal Timeline
- **THEN** xStudio does not broadcast `view_mode: "sequence"` because of that container change

#### Scenario: a peer-driven switch to a sequence is not re-announced
- **WHEN** this peer switches to a sequence because it applied a peer's `view_mode: "sequence"` message
- **THEN** it does not broadcast its own `view_mode: "sequence"` assertion for that switch

#### Scenario: an already-announced sequence is not announced twice
- **WHEN** the switch to a sequence has already been broadcast as `view_mode: "sequence"` for that timeline
- **THEN** observing the same container change again does not produce a second broadcast
