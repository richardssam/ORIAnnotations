# annotation-lifecycle-sync (delta)

## ADDED Requirements

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
