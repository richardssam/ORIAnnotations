## ADDED Requirements

### Requirement: Visibility stripping gates on the lease, not on being host

The single enforcement point for the visibility field group SHALL strip on
whether this peer holds the visibility lease, rather than on whether it is the
elected host.

The field group SHALL continue to be stripped as a whole. The failure that rule
exists to prevent — a follower that drops `view_mode` but still sends a
`clip_guid`, which asserts what the session should look at — is unchanged by
where the authority comes from.

`SUPPRESSED` SHALL keep its established meaning of "sent, with fields stripped",
never "not sent".

#### Scenario: A peer holding the lease sends its view
- **WHEN** a peer holding visibility broadcasts a view state
- **THEN** the visibility fields SHALL be sent intact

#### Scenario: A peer not holding the lease has its view stripped
- **WHEN** a peer that does not hold visibility broadcasts a view state
- **THEN** the visibility fields SHALL be stripped
- **AND** the call SHALL report `SUPPRESSED`

#### Scenario: Position and visibility are stripped independently
- **WHEN** a peer holds the position lease but not visibility
- **THEN** the outgoing message SHALL retain its position fields and lose its visibility fields

### Requirement: A visibility claim is refused when the role forbids the category

The claim operation SHALL refuse a visibility claim from a peer whose role does
not permit the category, and SHALL refuse rather than release.

Plugins claim unconditionally on a local action, so a role that forbids
visibility must be enforced at the claim, not left to the caller. Refusing
rather than releasing keeps a role-blocked peer from disturbing the peer that
legitimately holds the category.

#### Scenario: A role-forbidden visibility claim is refused
- **WHEN** a peer whose role forbids visibility claims the category
- **THEN** the claim SHALL be refused

#### Scenario: A refusal does not release the current holder
- **WHEN** a visibility claim is refused
- **THEN** the peer currently holding the category SHALL keep it

#### Scenario: Disabling enforcement restores unconditional claims
- **WHEN** the ownership enforcement switch is disabled
- **THEN** a visibility claim SHALL behave as if every peer always held every lease
