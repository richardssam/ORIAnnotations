## ADDED Requirements

### Requirement: Routine poll work does not pay wrapper-construction cost to answer identity questions

Acquiring a wrapper object for an xStudio module actor is an expensive
operation — it enumerates the actor's attributes and fetches each one with its
own synchronous round-trip, on the order of dozens of round-trips per
acquisition. Recurring plugin work SHALL NOT perform such an acquisition
merely to establish an actor's *identity*, and SHALL NOT perform one whose
result a guard is going to discard.

Where the plugin needs to know only which playhead is current — the periodic
active-playhead scan, and any dedup or change-detection guard keyed on
playhead identity — it SHALL resolve the actor handle without constructing the
attribute-enumerating wrapper, and SHALL construct the wrapper only once it has
established that the wrapper is actually needed.

The existing "Threading invariant preserved" requirement governs *which*
thread runs a given piece of work. This one governs how expensive that work
may be: work that respects the threading invariant can still block the poll
thread for seconds, and a poll thread stalled on routine bookkeeping delays
every queued command behind it — including position and view broadcasts whose
correctness depends on being applied in the order the user performed them.

#### Scenario: the periodic playhead scan is cheap

- **WHEN** the recurring scan checks whether the active playhead has changed
- **AND** the playhead has not changed
- **THEN** the check SHALL complete without enumerating and fetching the
  playhead's attributes

#### Scenario: a guard that will skip does not pay for what it skips

- **WHEN** a guard would decline to act because the playhead is one it has
  already handled
- **THEN** the expensive wrapper acquisition SHALL NOT have been performed for
  that declined call

#### Scenario: the wrapper is still available when genuinely required

- **WHEN** the plugin needs to read or write a playhead's attributes
- **THEN** it SHALL construct the wrapper at that point, so avoiding the cost
  for identity checks does not remove the capability where it is needed
