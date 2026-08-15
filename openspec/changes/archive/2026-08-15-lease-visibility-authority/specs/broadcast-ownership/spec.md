## ADDED Requirements

### Requirement: Visibility is a leased channel

`visibility` SHALL be a leased broadcast channel alongside position, display and
structure, carried by the existing ownership message and reported in session
state on the same terms.

No new message type is introduced: the ownership claim already names a category.
A peer that predates this channel neither claims it nor understands a claim for
it, and SHALL be unaffected — its absent claims leave the elected host holding
the category, which is the behaviour it already implements.

#### Scenario: A visibility claim uses the existing ownership message
- **WHEN** a peer claims visibility
- **THEN** the claim SHALL be carried by the same message that claims every other category

#### Scenario: Visibility ownership is reported to late joiners
- **WHEN** a peer builds session state while a peer holds visibility
- **THEN** that holder and the lease's remaining time SHALL be included on the same terms as every other leased category

#### Scenario: An omitted visibility owner does not clear a held lease
- **WHEN** session state arrives with no visibility ownership recorded
- **THEN** the receiving peer SHALL leave its locally-tracked visibility ownership unchanged

### Requirement: Visibility resolves contested claims to the later claimant

Where two peers claim visibility, resolution SHALL prefer the **later** claim,
in deliberate contrast to every other leased category, which prefers the
earlier.

The categories differ because the conflict differs. Two peers scrubbing at once
is a race to be settled in favour of whoever began, so that a burst of position
messages does not hand the playhead back and forth. Two peers selecting is not a
race: the later selection is the user who just acted, and preferring the earlier
one is what makes a user's selections silently do nothing.

The resolution rule SHALL be expressed per category rather than shared, so that
a change to one category's rule cannot silently alter another's.

#### Scenario: The later visibility claim wins
- **WHEN** two peers claim visibility
- **THEN** the later claim SHALL hold the category

#### Scenario: The earlier claim still wins for every other category
- **WHEN** two peers claim position, display or structure
- **THEN** the earlier claim SHALL hold the category

#### Scenario: A category's rule is not shared with another
- **WHEN** one category's resolution rule changes
- **THEN** no other category's resolution SHALL change as a result
