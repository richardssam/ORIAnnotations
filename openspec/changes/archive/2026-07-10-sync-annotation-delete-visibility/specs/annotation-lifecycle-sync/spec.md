## ADDED Requirements

### Requirement: Local annotation deletion is resolved by stroke uuid, not by re-deriving sender frame context

When a peer detects a local annotation deletion (a subset of a frame's strokes/shapes/text, or an entire frame/session clear), it SHALL identify the affected OTIO annotation clip(s) by looking up each deleted item's `uuid` against its own `sync_manager` state (the local Annotations tracks), rather than re-deriving the target clip/frame from host-specific context (e.g. RV node names or xStudio bookmark timing) carried in the deletion event.

#### Scenario: RV clear-paint resolves by uuid

- **WHEN** RV's `clear-paint` internal event fires with a pipe-joined list of deleted stroke uuids
- **THEN** the plugin SHALL look up each uuid against the local Annotations tracks to find its owning annotation clip
- **AND** SHALL NOT depend on RV's frame-numbering math to identify the clip

#### Scenario: RV clear-all-paint resolves each uuid independently

- **WHEN** RV's `clear-all-paint` internal event fires with `(node, uuid)` pairs spanning multiple sources/frames
- **THEN** the plugin SHALL group the deleted uuids by their owning annotation clip (which may span many clips)
- **AND** SHALL broadcast one replace per affected clip

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

#### Scenario: PaintClear is recognised, not treated as generic noise

- **WHEN** `on_annotation_event` receives a `JsonStore` payload with `data["event"] == "PaintClear"`
- **THEN** the plugin SHALL schedule the existing debounced flush scan (as it already does for any annotation event)
- **AND** the subsequent scan's count-decrease detection SHALL be responsible for producing the replace broadcast

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

#### Scenario: xStudio's global toggle broadcasts and is applied

- **WHEN** the local user presses the 'V' hotkey, firing `HideDrawings` or `ShowDrawings`
- **THEN** the plugin SHALL broadcast `{"annotations_visible": <bool>}` merged into `display_settings`
- **AND** on receipt, an xStudio peer SHALL apply the value to the `AnnotationsUI` plugin's visibility state

#### Scenario: Absent flag defaults to visible

- **WHEN** a peer loads a snapshot or joins a session where `display_settings` carries no `annotations_visible` key
- **THEN** annotations SHALL be treated as visible, matching pre-existing behavior
