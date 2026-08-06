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
