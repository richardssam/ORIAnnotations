## Context

The transport layer in `python/otio_sync_core/manager.py` exchanges messages over RabbitMQ using a nested envelope:

```
{ session, source_guid, payload: { command_schema, command: { event, payload } } }
```

Today every message is assembled from string literals and ad-hoc dicts. The same `command_schema` / `event` strings appear on the send side (`broadcast_*` → `_send_event`) and the receive side (`apply_patch`'s `if/elif` ladder), plus a third time inside `patcher.py` for the `OTIO_SESSION_1.0` family. Nothing connects these occurrences; a typo silently breaks sync.

The message inventory (extracted from the current code) falls into families with different characteristics:

| Family | Messages | Where the shape lives today |
|---|---|---|
| Session | `WHO_IS_MASTER`, `I_AM_MASTER`, `STATE_REQUEST`, `STATE_SNAPSHOT` | inline dicts in `manager.py` |
| Timeline | `ADD_TIMELINE`, `RENAME_TIMELINE` | inline dicts in `manager.py` |
| Settings | `PLAYBACK_SETTINGS_1.0/SET`, `DISPLAY_SETTINGS_1.0/SET` | **nowhere** — implicit, split across `playback_sync.py` + `display_sync.py` producers |
| Selection | `SELECTION_1.0/SET` | inline dict in `manager.py` |
| Annotation | `Annotation.1/PARTIAL` | inline dict in `manager.py` |
| OTIO session | `INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `SET_PROPERTY`, `REPLACE_ANNOTATION_COMMANDS` | **built and consumed by `patcher.py`** |

The existing OTIO message layer (`SyncEvent.py`) is documented by `otio_doc_generator.py`, which reads the registered classes plus a `config.yml` of examples/categories. This change brings the transport layer up to the same standard.

## Goals / Non-Goals

**Goals:**
- One typed class per protocol message as the single source of truth for `command_schema`, `event`, and payload field shape.
- Eliminate magic `command_schema` / `event` strings on both send and receive sides.
- Replace the `apply_patch` `if/elif` dispatch ladder with a registry keyed by `(command_schema, event)`.
- Keep the `OTIO_SESSION_1.0` family a **two-point** contract: `patcher.py` builds and consumes the same message class — no third declaration.
- Generate transport-protocol docs as a side-effect of the message classes, mirroring `SyncEvent.py` + `otio_doc_generator.py`.
- Hold wire format and runtime performance at parity.

**Non-Goals:**
- No change to `live_review_experiment/sync_review_marshal.py` (legacy, divergent wire format).
- No change to the on-wire envelope structure — peers running the old code must still interoperate.
- No runtime schema *validation* / rejection of messages (documentation and structure only; see Decision 6).
- No change to `SyncEvent.py` or its existing doc generator.

## Decisions

### Decision 1 — Message classes are pure data; handlers stay external

Each message is a `@dataclass` carrying only its payload fields, with class-level `SCHEMA` / `EVENT` constants and explicit `to_payload()` / `from_payload()`:

```python
@dataclass
class PartialAnnotation(ProtocolMessage):
    SCHEMA = "Annotation.1"
    EVENT  = "PARTIAL"
    clip_guid: str
    frame: float
    fps: float
    events: list

    def to_payload(self) -> dict:
        return {"clip_guid": self.clip_guid, "frame": self.frame,
                "fps": self.fps, "events": self.events}
```

Apply/handler logic is **not** placed on the message class (no `message.apply(manager)`). Handlers remain in `manager.py` / `patcher.py`, looked up via the registry. **Rationale:** keeping the classes free of manager/patcher coupling is what lets the doc generator treat them as self-contained, documentable schemas — the same property that makes `SyncEvent` classes documentable. *Alternative considered:* polymorphic `apply()` on each message — rejected because it drags transport internals into the schema definitions and makes them un-importable for doc generation without side effects.

The base class and all message definitions live in a **dedicated module** (`python/otio_sync_core/protocol_messages.py`), parallel to `SyncEvent.py`, so the schemas are importable in isolation and the doc generator has a single file to read.

### Decision 2 — Send seam: `_send_event` accepts a message object

`_send_event` gains the ability to take a `ProtocolMessage` and derive `(SCHEMA, EVENT, to_payload())` from it, wrapping it in the existing envelope. The `broadcast_*` methods construct the message instead of passing three positional strings/dicts. The envelope-wrapping code is unchanged.

### Decision 3 — Receive seam: a `(schema, event) → handler` registry

A registry maps `(command_schema, event)` to a handler. `apply_patch` becomes: look up the pair, reconstruct the message via `from_payload`, call the handler. **Rationale:** O(1) dict lookup replaces the O(n) string-comparison ladder (marginally faster), and adding a message means adding a class + registry entry rather than editing a growing `if/elif`. The registry is built from the message classes so it cannot drift from the definitions.

Registration uses an explicit **`@register` decorator** on each message class rather than an `__init_subclass__` hook. **Rationale:** an explicit decorator makes registration visible at the definition site and greppable, and avoids surprising contributors with implicit side effects from subclassing the base. *Alternative considered:* `__init_subclass__` auto-registration — rejected as too implicit for a contract this central.

### Decision 4 — OTIO_SESSION family: patcher returns/consumes the message class (two-point)

`patcher.py` methods (`insert_child`, `move_child`, `remove_child`, `set_property`) currently return wire dicts; `patcher.apply_patch` reads them back. They will instead build and consume the corresponding message class. The class carries the **wire form** of `child_data` (already-serialized dict), so `to_payload()` stays a cheap field copy and the expensive `_otio_to_dict` / `_dict_to_otio` happens exactly where it does today — at construction (build side) and in the handler (read side). **Rationale:** this is the crux decision from exploration. A naïve refactor that declared these payloads in a new module while `patcher` kept building raw dicts would create a *three-point* contract (patcher builds, class declares, apply reads). Routing both patcher build and patcher read through the one class keeps it two-point.

### Decision 5 — Performance: explicit `to_payload()`, no reflective serialization, no hot-path validation

`to_payload()` returns an explicit dict literal. We do **not** use `dataclasses.asdict()` (its recursive deep-walk adds real cost on nested payloads). Hot-path messages — `PartialAnnotation` (fires mid-stroke) and the playback `SET` message (fires per frame change) — get **no** `__post_init__` `isinstance` validation. **Rationale:** the dominant per-message costs (RabbitMQ round-trip, JSON encode/decode, OTIO ser/deser for the OTIO_SESSION family) are untouched by this refactor; the only way to regress is to add reflection or validation in the construction path, so we explicitly forbid it on hot paths.

### Decision 6 — Settings messages declare fields but tolerate extras (forward-compat)

`PLAYBACK_SETTINGS_1.0/SET` and `DISPLAY_SETTINGS_1.0/SET` get message classes documenting their known fields (`playing`, `current_time`, `looping`, `timeline_guid`, `sync_timestamp`; `pan`, `zoom`, `exposure`, `channel`, `sync_timestamp`). `from_payload` must **not** reject unknown keys, and the handler keeps reading via `.get(...)`. **Rationale:** this is the family with the biggest documentation win (the shape exists nowhere today), but it is also produced by independent plugins that may add keys; rejecting extras would break interop. The class documents the contract without enforcing it rigidly.

### Decision 7 — Doc generator imports the message module (vs. AST-parsing it)

The new generator imports the message-definitions module and introspects via `dataclasses.fields()`, class docstrings, and the `SCHEMA`/`EVENT` constants, reusing a `config.yml`-style side-file for categories and examples. **Rationale:** unlike `SyncEvent.py` (whose classes pull in OTIO and are awkward to import), the message module is self-contained pure-Python and cheap to import, so introspection is simpler and less fragile than `otio_doc_generator.py`'s AST walk. *Alternative considered:* AST parsing for consistency with the existing generator — rejected as needless fragility given the module imports cleanly.

The generator emits a **standalone HTML page** (as `otio_doc_generator.py` does for the OTIO layer), not an integration into the Sphinx build. **Rationale:** keeps the transport-protocol docs symmetric with the existing OTIO message docs and independent of the Sphinx toolchain.

## Risks / Trade-offs

- **Three-point contract creep on OTIO_SESSION** → Mitigation: Decision 4 makes the patcher methods the only build site and `from_payload` the only read site; a task explicitly verifies no raw-dict construction of these payloads remains.
- **Hot-path regression from validation/`asdict`** → Mitigation: Decision 5 forbids both on hot paths; a benchmark/parity check on partial-annotation and playback broadcast is a task.
- **Settings interop break from strict parsing** → Mitigation: Decision 6 requires tolerant `from_payload` and `.get()` access; producers keep emitting current payloads unchanged.
- **Registry vs. definition drift** → Mitigation: registry is derived from the classes (subclass hook / decorator), not maintained by hand.
- **Wire compatibility during rollout** → Mitigation: envelope structure and field names are byte-for-byte preserved; this is an internal representation change only.

## Open Questions

*All resolved (see Decisions 1, 3, 7):*

- ~~Message base class location~~ → dedicated module `python/otio_sync_core/protocol_messages.py` (Decision 1).
- ~~Registry construction~~ → explicit `@register` decorator (Decision 3).
- ~~Doc generator output~~ → standalone HTML page (Decision 7).
