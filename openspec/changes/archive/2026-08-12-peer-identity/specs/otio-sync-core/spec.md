## MODIFIED Requirements

### Requirement: A joining peer learns the peer set from session state

The session state a joining peer is given SHALL carry the current peer set,
alongside the elected host it already carries, so the joiner learns who is
present from the message it already requests.

The peer set SHALL identify each peer by the same attributes its own
announcement carries, including its identity. A joiner that learns a peer only
from session state SHALL be able to name it on the same terms as one learned
from an announcement — otherwise identity is systematically missing for the
peers that have gone quiet, which are the ones a joiner cannot name by any
other means.

Peers SHALL NOT answer one another's announcements in order to make a joiner
aware of them. That answering behaviour is the only step in the peer protocol
whose message count grows with the number of peers, and periodic announcement
makes it redundant: a joiner learns a silent peer from that peer's next
announcement regardless of whether anyone answered.

A joiner that receives no session state SHALL still learn the peer set from
subsequent announcements, within the announcement interval. Session state SHALL
NOT be the only means by which a peer becomes discoverable.

#### Scenario: A joiner learns existing peers without an answer cascade

- **WHEN** a peer joins a session and receives session state
- **THEN** that state SHALL identify the peers currently present
- **AND** no peer SHALL emit an announcement in response to the joiner's own

#### Scenario: Join cost does not grow with session size

- **WHEN** a peer joins a session
- **THEN** the number of messages emitted in response SHALL NOT grow with the
  number of peers already present

#### Scenario: A joiner that receives no session state still discovers peers

- **WHEN** a peer joins a session in which no session state is sent to it
- **THEN** it SHALL still learn every present peer from their periodic
  announcements
- **AND** SHALL do so within the announcement interval

#### Scenario: Every peer converges on the same peer set

- **WHEN** a joiner adopts the peer set from session state
- **AND** subsequently receives announcements from those peers
- **THEN** its peer table SHALL agree with the sender's
- **AND** host election evaluated against it SHALL reach the same host

#### Scenario: A peer learned from session state is as identifiable as one that announced

- **WHEN** a joiner adopts the peer set from session state
- **THEN** each adopted peer SHALL carry the identity its own announcement would
  have carried
- **AND** the joiner SHALL be able to name it without waiting for that
  announcement
