# annotation-lifecycle-sync

## Purpose

Detecting local annotation deletion (partial or full clear) and visibility toggling in both RV and xStudio, and broadcasting/applying that state across peers, reusing the existing `REPLACE_ANNOTATION_COMMANDS` message and `display_settings` broadcast rather than introducing new wire message types.

## Requirements

### Requirement: Local annotation deletion is resolved by stroke uuid, not by re-deriving sender frame context

When a peer detects a local annotation deletion (a subset of a frame's strokes/shapes/text, or an entire frame/session clear), it SHALL identify the affected OTIO annotation clip(s) by looking up each deleted item's `uuid` against its own `sync_manager` state (the local Annotations tracks), rather than re-deriving the target clip/frame from host-specific context (e.g. RV node names or xStudio bookmark timing) carried in the deletion event.

#### Scenario: RV clear-paint resolves by uuid

- **WHEN** RV's `clear-paint` internal event fires with a pipe-joined list of deleted stroke uuids
- **THEN** the plugin SHALL look up each uuid against the local Annotations tracks to find its owning annotation clip
- **AND** SHALL NOT depend on RV's frame-numbering math to identify the clip

#### Scenario: RV clear-all-paint resolves each uuid independently

- **WHEN** RV's `clear-all-paint` internal event fires with a pipe-joined list of deleted stroke uuids spanning multiple sources/frames
- **THEN** the plugin SHALL group the deleted uuids by their owning annotation clip (which may span many clips)
- **AND** SHALL broadcast one replace per affected clip

#### Scenario: RV pen strokes carry a resolvable uuid

- **WHEN** a pen stroke is broadcast (locally drawn, or applied on receipt from a remote peer)
- **THEN** the plugin SHALL persist the broadcast uuid onto RV's own `<node>.<component>.uuid` property (not just track it in memory)
- **AND** the uuid SHALL remain stable for the entire lifetime of that stroke's gesture, including across any premature pen-up detection mid-drag, so that a later local clear can resolve the stroke back to its OTIO annotation clip

### Requirement: Deletion is broadcast via the existing REPLACE_ANNOTATION_COMMANDS message

Detected local deletions (partial or full) SHALL be broadcast using the existing `broadcast_replace_annotation_commands` / `REPLACE_ANNOTATION_COMMANDS` message, carrying the affected clip's surviving `annotation_commands` (which may be an empty list). No new SyncEvent type or wire message SHALL be introduced for deletion.

#### Scenario: Partial delete broadcasts survivors

- **WHEN** one of several strokes/shapes/text items on a frame is deleted locally
- **THEN** the plugin SHALL broadcast a `REPLACE_ANNOTATION_COMMANDS` message for that clip containing the remaining (surviving) commands, with survivors' uuids unchanged

#### Scenario: Full clear broadcasts an empty command list

- **WHEN** every annotation on a frame (or, for RV, every annotation across the session) is cleared locally
- **THEN** the plugin SHALL broadcast a `REPLACE_ANNOTATION_COMMANDS` message per affected clip whose `annotation_commands` list is empty

### Requirement: xStudio detects deletion as a stroke/caption count decrease during the existing poll scan

`broadcast_local_bookmark` SHALL detect local deletion by observing that a bookmark's current stroke or caption count is lower than the count already reflected in the OTIO timeline for that clip/frame, in addition to its existing detection of count increases. On detecting a decrease, it SHALL rebuild the complete surviving stroke/caption list (preserving existing uuids for surviving items) and broadcast it via `broadcast_replace_annotation_commands`, rather than computing a (negative, and therefore empty) delta as it does today.

#### Scenario: Stroke count decrease triggers a replace broadcast

- **WHEN** the poll scan observes a bookmark whose `pen_strokes` count is lower than the count already recorded for that clip/frame in the OTIO timeline
- **THEN** the plugin SHALL broadcast the bookmark's current (smaller) full stroke list via `broadcast_replace_annotation_commands`, reusing existing uuids for surviving strokes

#### Scenario: A local clear is detected from the PaintClear draw interaction

- **WHEN** the user presses Ctrl+D ("Delete all strokes") in xStudio
- **THEN** the plugin SHALL schedule the existing debounced flush scan from the `PaintClear` draw interaction received on AnnotationsCore's draw-events group
- **AND** the subsequent scan's count-decrease detection SHALL be responsible for producing the replace broadcast

### Requirement: xStudio detects a bookmark that has disappeared as a full clear

The existing count-decrease detection requires a surviving bookmark to compare against. Clearing a drawing normally removes the bookmark outright — xStudio deletes it whenever it carries no note text — so the plugin SHALL additionally treat the *disappearance* of a bookmark it has previously broadcast annotations for as a full clear of that clip/frame, and SHALL broadcast an empty `REPLACE_ANNOTATION_COMMANDS` for it.

The plugin SHALL therefore record which clip/frame keys it has broadcast annotations for and which bookmark each came from, and SHALL evaluate disappearance on every scan, including when no bookmarks remain at all — the case where every annotation in the session has been cleared MUST NOT be skipped as "nothing to scan".

Detection SHALL be by disappearance, not by any particular originating action, so that clears, note deletions, and any other route that removes the bookmark are all covered.

A key SHALL be treated as disappeared only when the plugin has previously broadcast annotations for it and its bookmark is absent from the current scan. Keys whose bookmark still exists, keys the plugin has never broadcast for, and annotations owned by remote peers SHALL NOT produce a clear broadcast.

#### Scenario: Clearing a drawing broadcasts an empty replace

- **WHEN** the user clears an annotation whose bookmark carries no note text, so xStudio removes the bookmark
- **THEN** the plugin SHALL broadcast an empty `REPLACE_ANNOTATION_COMMANDS` for that clip/frame
- **AND** peers SHALL hard-clear the frame, per the existing empty-replace requirement

#### Scenario: The last annotation in the session is cleared

- **WHEN** the removed bookmark was the only bookmark in the session, leaving the bookmark list empty
- **THEN** the plugin SHALL still evaluate disappearance and broadcast the empty replace
- **AND** SHALL NOT skip the scan on the grounds that there are no bookmarks to iterate

#### Scenario: Deleting a note removes the annotation with it

- **WHEN** the user deletes a note from the notes panel, removing a bookmark the plugin had broadcast annotations for
- **THEN** the plugin SHALL broadcast the same empty `REPLACE_ANNOTATION_COMMANDS` for that clip/frame

#### Scenario: A clear is broadcast once, not on every scan

- **WHEN** a disappearance has been broadcast for a clip/frame
- **THEN** the plugin SHALL forget that key
- **AND** subsequent scans SHALL NOT re-broadcast a clear for it

#### Scenario: Annotations the plugin never broadcast produce no clear

- **WHEN** a bookmark disappears that the plugin has no recorded broadcast for — for example one created from a remote peer's annotation and cleared by that peer
- **THEN** the plugin SHALL NOT broadcast a clear for it

#### Scenario: A bookmark that still exists is left to the count-decrease path

- **WHEN** a bookmark survives with fewer strokes than the timeline records
- **THEN** the existing count-decrease detection SHALL handle it
- **AND** the disappearance path SHALL NOT also fire for that key

### Requirement: An empty REPLACE_ANNOTATION_COMMANDS payload is applied as an authoritative hard clear

When a received `REPLACE_ANNOTATION_COMMANDS` message's command list for a clip is completely empty, each receiver SHALL apply it as an authoritative "this clip has no annotations" state via a dedicated hard-clear path, distinct from the existing kind-inferring reconcile/merge logic used for non-empty (e.g. text-edit) replaces.

#### Scenario: RV hard-clears the frame's paint order on empty replace

- **WHEN** RV receives a `REPLACE_ANNOTATION_COMMANDS` message whose command list is empty for a clip it has a paint node for
- **THEN** the plugin SHALL set that frame's `order` property to an empty list directly
- **AND** SHALL NOT rely on `apply_specs`'s reconcile-mode kind inference (which treats an empty incoming batch as "no opinion," not "authoritatively empty")

#### Scenario: xStudio hard-clears the bookmark's annotation on empty replace

- **WHEN** xStudio receives a `REPLACE_ANNOTATION_COMMANDS` message whose command list is empty for a clip with a tracked bookmark
- **THEN** the plugin SHALL call `bm.set_annotation(strokes=[], captions=[])` on that bookmark unconditionally
- **AND** SHALL NOT early-return before doing so (unlike the existing `refresh_annotation_bookmark` behavior for an empty derived stroke/caption list)

#### Scenario: Existing partial-replace (text-edit) behavior is unaffected

- **WHEN** a `REPLACE_ANNOTATION_COMMANDS` message intentionally omits a kind (e.g. a text-only edit that says nothing about pen strokes) but is not fully empty
- **THEN** receivers SHALL continue to apply the existing kind-inferring reconcile/merge behavior for that kind, unchanged

### Requirement: Annotation visibility is a single session-wide flag synced via display_settings

The system SHALL synchronize one boolean, `annotations_visible`, as part of the existing `display_settings` broadcast/state. Both RV's per-source "Show Drawings" toggle and xStudio's global "Toggle annotation visibility" ('V') hotkey SHALL broadcast this flag, and both SHALL apply a received value session-wide (not scoped to a single clip or media source). The flag SHALL default to visible (`true`) when absent, so existing snapshots and older peers are unaffected.

#### Scenario: RV toggling one source broadcasts and is applied session-wide

- **WHEN** the local user toggles "Show Drawings" for one RV source (`<node>.paint.show`)
- **THEN** the plugin SHALL broadcast `{"annotations_visible": <bool>}` merged into `display_settings`
- **AND** on receipt, every peer (including other RV instances) SHALL apply the value to every `RVPaint` node, not only the node that originated the change

#### Scenario: RV reads the currently-viewed node, not an arbitrary one

- **WHEN** the plugin reads the current `annotations_visible` state to decide whether to broadcast
- **THEN** it SHALL resolve the specific currently-viewed `RVPaint` node (e.g. via `metaEvaluateClosestByType`)
- **AND** SHALL NOT scan all `RVPaint` nodes for "any node that has the property set," since a prior session-wide apply can leave multiple nodes holding different values simultaneously

#### Scenario: xStudio's global toggle broadcasts and is applied

- **WHEN** the local user presses the 'V' hotkey, firing `HideDrawings` or `ShowDrawings`
- **THEN** the plugin SHALL broadcast `{"annotations_visible": <bool>}` merged into `display_settings`
- **AND** on receipt, an xStudio peer SHALL apply the value by setting the `AnnotationsUI` plugin's `action_attribute_` to `["ShowVisibility"]` or `["HideVisibility"]` — setting the `Visibility` attribute's plain value directly is insufficient, since `AnnotationsUI::attribute_changed()` has no branch for it and it produces no rendering effect

#### Scenario: Absent flag defaults to visible

- **WHEN** a peer loads a snapshot or joins a session where `display_settings` carries no `annotations_visible` key
- **THEN** annotations SHALL be treated as visible, matching pre-existing behavior
