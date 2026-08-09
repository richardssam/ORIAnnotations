## ADDED Requirements

### Requirement: A remote view is compared against the displayed view
OpenRV SHALL decide whether a remote view instruction requires action by
comparing it against the view it is **currently displaying**, not against the
last view it adopted from a peer.

Those two diverge the moment the user changes the view locally, which the
application permits and which no message records. A peer that compares against
the last adopted value then reads a correct instruction as a no-op: it believes
it is already showing what the host asked for, while showing something else.

#### Scenario: A locally isolated clip does not block a later sequence instruction
- **WHEN** the user isolates a clip in OpenRV, changing the view locally
- **AND** the host subsequently reports sequence view
- **THEN** OpenRV SHALL return to sequence view

#### Scenario: An instruction matching the displayed view is still a no-op
- **WHEN** the host reports the view OpenRV is already displaying
- **THEN** no view switch SHALL be performed

#### Scenario: Ignoring the host's view is recorded
- **WHEN** OpenRV receives the host's view and does not adopt it
- **THEN** it SHALL record that fact and the reason
- **AND** the record SHALL be observable without reading application logs
