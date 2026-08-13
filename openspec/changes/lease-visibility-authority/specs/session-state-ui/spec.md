## ADDED Requirements

### Requirement: The session panel names who currently holds the view

Both host applications' session panels SHALL show which participant currently
holds visibility authority, identified by person rather than by peer GUID.

A category that moves between users silently is worse than one that never moves.
The failure this capability addresses was invisible for exactly this reason: a
user's selections did nothing, no error appeared at either end, and the only
record that the authority sat elsewhere was a log line on the machine that had
lost it.

The panel SHALL distinguish holding the view from merely being able to take it,
so a user can tell "I am driving" from "I could drive".

#### Scenario: The holder is named
- **WHEN** a peer holds visibility authority
- **THEN** every peer's panel SHALL show that participant as holding the view

#### Scenario: This peer holding it is distinguishable
- **WHEN** this peer holds visibility authority
- **THEN** its panel SHALL show that this peer is the one driving the view

#### Scenario: An eligible peer that is not holding it is shown as such
- **WHEN** this peer may claim visibility but does not currently hold it
- **THEN** the panel SHALL distinguish that from holding it

#### Scenario: A peer that may never hold it is shown as such
- **WHEN** this peer's role forbids visibility
- **THEN** the panel SHALL show that changing the view is not available to it
- **AND** SHALL NOT present it as merely not currently holding the category
