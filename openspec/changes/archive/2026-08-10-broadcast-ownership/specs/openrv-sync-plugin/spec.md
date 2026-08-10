## ADDED Requirements

### Requirement: OTIO-origin structural broadcasts require the structure lease, not master status
The plugin's OTIO-origin structural broadcast paths (timeline add/replace/rename, property changes, structural child insert/remove/move) SHALL require the structure ownership lease before broadcasting, replacing the prior rule that only the session master could originate them. Any peer holding the structure lease SHALL be able to originate a structural broadcast, whether or not it is the session master.

The master's own structural rebuild paths SHALL claim the structure lease the same way any other input-driven structural change does, rather than bypassing the check by virtue of being master.

#### Scenario: A non-master peer holding the structure lease broadcasts a structural change
- **WHEN** a peer that is not the session master holds the structure lease and performs a structural edit
- **THEN** the resulting structural broadcast SHALL be sent

#### Scenario: A non-master peer without the structure lease does not broadcast a structural change
- **WHEN** a peer that is not the session master performs a structural edit while another peer holds the structure lease
- **THEN** the structural broadcast SHALL be suppressed rather than sent, regardless of master status

#### Scenario: The master's rebuild path claims the lease like any other peer
- **WHEN** OpenRV's master-side structural rebuild would emit a structural broadcast
- **THEN** it SHALL first hold the structure lease, obtained the same way any other input-driven structural change obtains it

### Requirement: Remote-apply echo suppression is reentrant
The plugin's apply-scope guard around applying a remote message SHALL be reentrant: if applying one remote message triggers a further local state change that would itself normally be treated as a remote apply, echo suppression SHALL remain active until the outermost application completes, not the innermost.

#### Scenario: A nested remote apply does not prematurely re-enable broadcast
- **WHEN** applying a remote message triggers a further local state change that itself enters the apply-scope guard
- **THEN** echo suppression SHALL remain active until the outermost apply scope exits
- **AND** no broadcast or ownership claim SHALL occur while any scope remains open
