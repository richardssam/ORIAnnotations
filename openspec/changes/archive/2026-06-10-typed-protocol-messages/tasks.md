## 1. Message module foundation

- [x] 1.1 Create `python/otio_sync_core/protocol_messages.py` with a `ProtocolMessage` base class exposing `SCHEMA`/`EVENT` class constants and `to_payload()` / `from_payload()` contracts
- [x] 1.2 Implement the `@register` decorator that records each message class in a `(SCHEMA, EVENT) -> class` registry, and expose a lookup helper
- [x] 1.3 Add a registry accessor that the receive side can use to resolve `(command_schema, event)` to a message class, returning `None` for unknown pairs

## 2. Define message classes per family

- [x] 2.1 Session family: `WHO_IS_MASTER`, `I_AM_MASTER`, `STATE_REQUEST`, `STATE_SNAPSHOT` (schema `LiveSession.1`)
- [x] 2.2 Timeline family: `ADD_TIMELINE`, `RENAME_TIMELINE` (schema `TIMELINE_1.0`)
- [x] 2.3 Settings family: playback `SET` (`PLAYBACK_SETTINGS_1.0`) and display `SET` (`DISPLAY_SETTINGS_1.0`) — declare known fields, tolerant `from_payload` that ignores unknown keys
- [x] 2.4 Selection family: `SET` (schema `SELECTION_1.0`)
- [x] 2.5 Annotation family: `PARTIAL` (schema `Annotation.1`) — hot path, no validation
- [x] 2.6 OTIO session family: `INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `SET_PROPERTY`, `REPLACE_ANNOTATION_COMMANDS` (schema `OTIO_SESSION_1.0`), carrying wire-form payloads
- [x] 2.7 Add docstrings/field metadata (name, type, description) on every message class for the doc generator to consume

## 3. Send side

- [x] 3.1 Extend `_send_event` to accept a `ProtocolMessage` and derive `(SCHEMA, EVENT, to_payload())`, keeping the existing envelope-wrapping byte-for-byte _(implemented as a new typed `_send_message(msg)` entry point; legacy `_send_event`/`_send_session_event` removed)_
- [x] 3.2 Convert each `broadcast_*` method in `manager.py` to construct the corresponding message class instead of passing literal schema/event strings and ad-hoc dicts
- [x] 3.3 Verify `to_payload()` uses explicit dict construction (no `dataclasses.asdict()`) everywhere

## 4. OTIO session two-point contract (patcher)

- [x] 4.1 Change `patcher.py` mutation methods (`insert_child`, `move_child`, `remove_child`, `set_property`) to return the corresponding OTIO-session message instances instead of raw dicts
- [x] 4.2 Update `manager.py` call sites that forward patcher return values into `_send_event` to pass the message objects
- [x] 4.3 Confirm no raw-dict construction of OTIO-session payloads remains (single definition is the message class)

## 5. Receive side dispatch registry

- [x] 5.1 Replace the `apply_patch` `if/elif` ladder in `manager.py` with registry lookup on `(command_schema, event)` → reconstruct message via `from_payload` → call handler
- [x] 5.2 Route OTIO-session messages through `patcher.apply_patch` using the reconstructed message type (same class produced in §4)
- [x] 5.3 Ensure unknown `(command_schema, event)` pairs are ignored without error and processing of subsequent messages continues
- [x] 5.4 Confirm handler logic lives in manager/patcher (not on the message classes), preserving pure-data message definitions

## 6. Performance verification

- [x] 6.1 Confirm hot-path messages (`PARTIAL` annotation, playback `SET`) have no `__post_init__`/isinstance validation and no reflective serialization _(verified: no `__post_init__`, no `asdict`; explicit `to_payload`)_
- [x] 6.2 Benchmark partial-annotation and playback-broadcast construction+serialize before/after to confirm parity _(typed overhead ~0.6–1.9µs/msg, below the 3.1µs JSON step and ~1000× below the ms-scale network round-trip; dominant costs unchanged)_

## 7. Documentation generator

- [x] 7.1 Create a standalone HTML doc generator under `docs/` that imports `protocol_messages.py` and introspects classes (schema, event, fields via `dataclasses.fields()` + docstrings)
- [x] 7.2 Add a `config.yml`-style side-file for protocol-message categories and example payloads, mirroring the OTIO docs config
- [x] 7.3 Render each message with its schema, event, and fields (name/type/description); group by category; show configured examples
- [x] 7.4 Ensure messages absent from the side-file still appear using class-derived metadata
- [x] 7.5 Generate the HTML page and verify all registered messages are present _(15/15 rendered across 6 categories)_

## 8. Validation

- [x] 8.1 Add/extend tests asserting each `broadcast_*` produces an envelope whose `command_schema`/`event` match the message class and whose structure is unchanged from the prior format
- [x] 8.2 Add tests for registry dispatch: known pair routes to handler; unknown pair is ignored safely
- [x] 8.3 Add tests for settings messages tolerating extra/unknown fields
- [x] 8.4 Run the existing sync test suite to confirm cross-app (RV ↔ xStudio) interop is unaffected _(13 pass; 1 pre-existing failure in `xs_annotation_codec` caption font_size, unrelated — fails without this change too)_
