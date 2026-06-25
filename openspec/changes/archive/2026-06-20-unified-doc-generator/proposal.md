## Why

Two separate doc generators (`otio_doc_generator.py` and `protocol_doc_generator.py`) produce disconnected HTML files with inconsistent styling and require separate config files. Merging them into a single generator with a unified config produces one coherent reference document covering both the OTIO SyncEvent layer and the transport-layer protocol messages.

## What Changes

- **New file** `docs/doc_generator.py` replaces both `otio_doc_generator.py` and `protocol_doc_generator.py`
- **New unified config** `docs/config.yml` replaces `docs/config.yml` (OTIO portion) and `docs/protocol_messages_config.yml` (protocol portion) with a single structured file
- **New output** single HTML file covering both layers, with a layered sidebar (OTIO Events / Protocol Messages)
- **BREAKING**: `--input` CLI argument removed; OTIO source file path moves to `meta.otio_input` in config
- **BREAKING**: `--config` now points to the unified config (old single-purpose configs no longer used)
- Old generators (`otio_doc_generator.py`, `protocol_doc_generator.py`) retired

## Capabilities

### New Capabilities

- `unified-doc-generator`: Single Python script that reads a unified `config.yml` to produce one HTML reference document covering both OTIO SyncEvent schemas and transport-layer protocol messages, with a layered sidebar, tabbed examples (JSON + Python), copy buttons, and an optional Markdown introduction section rendered via `mistune`

### Modified Capabilities

- `protocol-message-docs`: The protocol message documentation capability gains the rich HTML format from the OTIO generator (sidebar navigation, tabbed examples, copy buttons, Python instantiation examples), replacing its current simple dark-theme output

## Impact

- `docs/otio_doc_generator.py` — deleted
- `docs/protocol_doc_generator.py` — deleted
- `docs/doc_generator.py` — new file
- `docs/config.yml` — restructured (existing `config.yml` entries move under `otio_events:`, `protocol_messages_config.yml` entries move under `protocol_messages:`, new `meta:` section added)
- `docs/protocol_messages_config.yml` — deleted (contents merged into unified config)
- `docs/Makefile` — updated to use new script and config
- `requirements.txt` — `mistune` already present, no new dependencies
