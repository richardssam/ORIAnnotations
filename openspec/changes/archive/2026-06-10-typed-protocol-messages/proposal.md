## Why

The OTIO message layer (`SyncEvent.py`) is self-documenting: each message *is* a registered class, and `otio_doc_generator.py` reads those classes to produce protocol docs. The transport/envelope layer in `manager.py` has no equivalent — its messages are stringly-typed (`command_schema` / `event` magic strings repeated on both the send and receive sides) and their payload shapes exist only implicitly. The worst case is the settings family (`PLAYBACK_SETTINGS_1.0` / `DISPLAY_SETTINGS_1.0`), whose field shape lives *nowhere* in code — it is the accidental intersection of two producers (`rvplugin/.../playback_sync.py` and `xstudio_plugin/.../display_sync.py`) that never reference each other. This makes the protocol undocumented, easy to break with a typo, and impossible to validate.

## What Changes

- Introduce typed **protocol message classes** in `otio_sync_core` as the single source of truth for each message's `command_schema`, `event` name, and payload field shape — replacing magic strings on both the send (`broadcast_*` → `_send_event`) and receive (`apply_patch`) sides.
- Replace the `apply_patch` `if command_schema == ... and event == ...` ladder with a **dispatch registry** keyed by message type.
- Make the `OTIO_SESSION_1.0` family (`INSERT_CHILD` / `MOVE_CHILD` / `REMOVE_CHILD` / `SET_PROPERTY` / `REPLACE_ANNOTATION_COMMANDS`) flow through the message classes that `patcher.py` **returns and consumes**, so the contract stays two-point (build + read) rather than becoming three-point.
- Add a **protocol message documentation generator** that reads the message classes and emits docs as a side-effect, mirroring `otio_doc_generator.py` + `config.yml` for the OTIO layer.
- Performance is held neutral: explicit `to_payload()` (no `dataclasses.asdict()` reflection) and no heavyweight validation on hot-path messages (`broadcast_partial_annotation`, `broadcast_playback_state`).
- Out of scope: `live_review_experiment/sync_review_marshal.py` (legacy, divergent wire format).

## Capabilities

### New Capabilities
- `protocol-message-docs`: A generator that produces human-readable documentation of the transport-layer protocol messages (schema, event, fields, direction, examples) directly from the typed message classes — the envelope-layer analogue of the existing OTIO SyncEvent docs.

### Modified Capabilities
- `otio-sync-core`: The command-based messaging requirement gains typed message classes as the source of truth for envelope `command_schema`/`event`/payload shapes, and a type-keyed dispatch registry replaces the string-comparison dispatch.

## Impact

- **Code**: `python/otio_sync_core/manager.py` (`broadcast_*` send sites, `apply_patch` dispatch), `python/otio_sync_core/patcher.py` (mutation payload build/consume), a new message-definitions module, and a new doc generator under `docs/`.
- **Contract**: Producer plugins (`rvplugin/ori_sync/playback_sync.py`, `xstudio_plugin/ori_sync/display_sync.py`) keep emitting the same wire payloads; the settings field shapes they rely on become explicitly declared. Wire format is unchanged — no breaking change to peers.
- **Performance**: Hot paths (partial annotation, playback broadcast) must remain at parity; dispatch becomes marginally faster (dict lookup vs. string ladder).
