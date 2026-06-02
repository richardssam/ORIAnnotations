## ADDED Requirements

### Requirement: Master Schema Advertisement
The session manager SHALL inject the top-level `schema: "SYNC_REVIEW_1.0"` attribute into the envelope specifically when responding to discovery with `I_AM_MASTER`, to advertise the session's protocol version.

#### Scenario: Master responds to discovery
- **WHEN** the master node receives a `WHO_IS_MASTER` request
- **THEN** it SHALL broadcast `I_AM_MASTER` with `schema: "SYNC_REVIEW_1.0"` at the root of the JSON envelope.
