## MODIFIED Requirements

### Requirement: Asynchronous Polling

The plugin SHALL use a background consumer thread to receive messages without blocking the RV UI. The poll loop (`poll_network`) SHALL reside in `plugin.py` and SHALL delegate action handling to domain-specific controller methods via `_handle_action`. Structural polling (sequence reorders, new sequences, renames) and display state polling SHALL be delegated to the `SequenceSyncController` and `DisplaySyncController` respectively.

#### Scenario: Poll loop delegates structural checks

- **WHEN** the poll timer fires and `sync_manager.status` is `STATE_SYNCED`
- **THEN** `poll_network` SHALL call `self.sequence.check_sequence_reorders()`, `self.sequence.poll_new_sequences()`, `self.sequence.poll_sequence_renames()`, and `self.display.broadcast_display_state()`
