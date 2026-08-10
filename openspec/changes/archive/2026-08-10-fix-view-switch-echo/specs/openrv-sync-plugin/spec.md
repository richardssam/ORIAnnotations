## ADDED Requirements

### Requirement: A broadcast describes the view being displayed when it is sent
The `view_mode` and `clip_guid` on an outbound playback broadcast SHALL describe the view OpenRV is displaying at the moment the message is built, not the last view the plugin recorded.

Those two are updated by the view-change handler, and a frame-changed broadcast can be dispatched before that handler runs — the frame changes as part of the switch. A broadcast built from the recorded values therefore describes the view the application has already left, while carrying the new view's frame.

This matters beyond the message itself: a peer that applies such a position moves its own playhead, which can present as a local selection on that peer and be reported back, undoing the switch that started it.

#### Scenario: A view switch does not broadcast the previous view

- **WHEN** OpenRV switches to an isolated clip and a frame-changed broadcast is dispatched during the switch
- **THEN** the broadcast SHALL NOT report the previously displayed view mode
- **AND** the reported view SHALL match the view node OpenRV is displaying

#### Scenario: The displayed view is observable in the log

- **WHEN** a playback broadcast is sent
- **THEN** the log line SHALL record both the displayed view and the broadcast view mode
- **AND** a disagreement between them SHALL be visible without attaching a debugger

#### Scenario: A settled view still broadcasts normally

- **WHEN** OpenRV is displaying a view it has finished switching to and the user scrubs
- **THEN** the broadcast SHALL carry that view's mode and clip guid as before
