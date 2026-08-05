## MODIFIED Requirements

### Requirement: Settings Messages Declare Fields but Tolerate Extras
The system SHALL provide message classes for `PLAYBACK_SETTINGS_1.0/SET` and `DISPLAY_SETTINGS_1.0/SET` that document their known fields, while accepting messages that contain additional, unrecognized fields without failure. This preserves interoperability with independent producers that may emit extra keys. The `PLAYBACK_SETTINGS_1.0/SET` message SHALL additionally carry the view-state fields `view_mode` (`"sequence"` or `"source"`) and `clip_guid` (nullable), making it the sole message describing what a peer is viewing.

#### Scenario: Known settings fields are documented
- **WHEN** the playback and display settings message classes are defined
- **THEN** they SHALL enumerate the established fields (playback: `playing`, `current_time`, `looping`, `timeline_guid`, `view_mode`, `clip_guid`, `sync_timestamp`; display: `pan`, `zoom`, `exposure`, `channel`, `sync_timestamp`).

#### Scenario: Extra fields do not break parsing
- **WHEN** a settings message arrives containing fields beyond the declared set
- **THEN** the message SHALL be parsed and applied without error, and unrecognized fields SHALL be ignored rather than rejected.

## REMOVED Requirements

### Requirement: Selection Message and Dispatch

**Reason**: Selection is no longer a separate channel; the same information
(active clip, sequence vs. source view) is now carried by the view-state fields
of `PLAYBACK_SETTINGS_1.0`, eliminating two-source-of-truth disagreement and the
bidirectional selection echo loop.

**Migration**: Producers/consumers SHALL stop emitting and handling
`SELECTION_1.0`. Senders SHALL set `view_mode` and `clip_guid` on the
`PLAYBACK_SETTINGS_1.0` view-state message; receivers SHALL act on those fields
in the single view-state apply path. There is no compatibility shim — both the RV
and xStudio plugins are updated together (hard cutover).

#### Scenario: SELECTION_1.0 is not registered
- **WHEN** the dispatch registry is built
- **THEN** there SHALL be no handler registered for `SELECTION_1.0`, and the
  `SelectionSet` message class and `broadcast_selection` / `selection_changed`
  manager entry points SHALL no longer exist.
