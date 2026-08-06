## ADDED Requirements

### Requirement: OpenRV follows the host's view instead of deriving its own
When OpenRV is not the session host, it SHALL NOT broadcast visibility changes, and SHALL adopt the host's reported clip and view mode rather than selecting a view of its own.

OpenRV currently broadcasts a visibility change whenever its local view node changes — including when that change was itself caused by applying a remote message — and independently decides between sequence and isolated-clip views. Both behaviours belong to a peer that owns visibility, not to a follower.

#### Scenario: A local view-node change is not broadcast by a follower
- **WHEN** OpenRV is not the host and its view node changes for any reason
- **THEN** it SHALL NOT broadcast a visibility change

#### Scenario: OpenRV adopts the host's view mode
- **WHEN** the host reports viewing an isolated clip
- **THEN** OpenRV SHALL display that clip in an isolated view
- **WHEN** the host reports viewing the sequence
- **THEN** OpenRV SHALL display the sequence

#### Scenario: OpenRV retains position and annotation authority
- **WHEN** an OpenRV user scrubs, plays, stops, or annotates while not the host
- **THEN** those actions SHALL be broadcast and honoured by other peers, as before

#### Scenario: OpenRV hosts when it is the only capable peer
- **WHEN** a session contains OpenRV peers only
- **THEN** one SHALL be elected host and SHALL broadcast visibility changes normally
