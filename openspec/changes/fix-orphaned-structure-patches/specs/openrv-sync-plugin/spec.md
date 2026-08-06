## ADDED Requirements

### Requirement: Track identity survives re-initialisation
When OpenRV re-initialises a timeline's tracks, the rebuilt tracks SHALL keep
the sync GUIDs the originals had, so that peers' references to them stay valid.

OpenRV re-initialises a sequence's tracks after the first media is added. If the
rebuilt Media track is given a fresh GUID, every later insertion addresses a
container no peer holds, and the media silently never reach them.

#### Scenario: Media added after a re-initialisation reach peers
- **WHEN** an OpenRV peer adds media, causing its tracks to be re-initialised
- **AND** further media are added afterwards
- **THEN** every added item SHALL reach the other peers
- **AND** each SHALL become viewable there

#### Scenario: A peer that joined earlier is unaffected by the rebuild
- **WHEN** a peer joined before the re-initialisation
- **THEN** the container GUIDs it holds SHALL still resolve afterwards

### Requirement: Media learned about after joining are materialised
OpenRV SHALL create the viewable source for a media item whenever it learns of
that item, regardless of when in the session it learns of it or which structural
message carries it.

#### Scenario: A peer that joined before the media can still display them
- **WHEN** an OpenRV peer joins a session whose timeline is empty
- **AND** media are added to that timeline afterwards
- **THEN** the peer SHALL create a viewable source for each added item
- **AND** SHALL be able to display any of them on request

#### Scenario: Media already viewable are not duplicated
- **WHEN** the peer learns again of a media item it already has a source for
- **THEN** no additional source SHALL be created

### Requirement: A peer reports media it cannot make viewable
When OpenRV knows of a media item but cannot make it viewable, it SHALL report
that, rather than presenting a session state in which the item appears absent.

This is the same rule the session already applies to a view that cannot be
mirrored: a peer that cannot comply says so, so that "cannot show it" is never
indistinguishable from "was never told about it".

#### Scenario: Unmaterialisable media are reported
- **WHEN** the peer holds a clip whose media it cannot make viewable
- **THEN** it SHALL report that the item could not be made viewable
- **AND** the failure SHALL be observable without reading application logs
