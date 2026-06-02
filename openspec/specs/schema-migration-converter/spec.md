## ADDED Requirements

### Requirement: JSONL Format Converter
The system SHALL provide a utility to convert legacy flat-schema `.jsonl` recording files to the ASWF nested envelope schema.

#### Scenario: Converting an old recording
- **WHEN** the converter is run on a `.jsonl` file containing legacy `{"command": "...", "event": "..."}` payloads
- **THEN** it SHALL output rewritten payloads in the `{"schema": "SYNC_REVIEW_1.0", "payload": {"command_schema": "LiveSession.1", ...}}` format.
