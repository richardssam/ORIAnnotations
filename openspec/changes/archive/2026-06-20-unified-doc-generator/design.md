## Context

Two documentation generators exist in `docs/`:

- `otio_doc_generator.py` — AST-parses a Python source file to document OTIO SyncEvent schema classes. Produces rich HTML with sidebar, tabbed examples (JSON + Python), syntax highlighting, and copy buttons. Driven by `docs/config.yml`.
- `protocol_doc_generator.py` — Imports `otio_sync_core.protocol_messages` at runtime and introspects the registry. Produces a simple dark-theme HTML page with no sidebar, tabs, or copy buttons. Driven by `docs/protocol_messages_config.yml`.

The two outputs are disconnected in style and must be maintained separately. The goal is a single generator and config producing one coherent reference document.

## Goals / Non-Goals

**Goals:**
- Single script `docs/doc_generator.py` producing one HTML file covering both layers
- Unified `docs/config.yml` with `meta`, `otio_events`, and `protocol_messages` sections
- Protocol messages rendered in the same rich format as OTIO events (sidebar, tabbed examples, Python instantiation)
- Optional Markdown introduction section from a file referenced in `meta.introduction`
- All existing OTIO generator features preserved

**Non-Goals:**
- Rewriting the HTML/CSS styling
- Sphinx integration or multi-page output
- Supporting additional documentation sources beyond the two existing ones

## Decisions

### D1: Unified config shape — sectioned (Option B)

```yaml
meta:
  title: "OTIO Sync Protocol"
  otio_input: ../python/otio_sync_core/SyncEvent.py
  introduction: ../docs/introduction.md
  output: otio_sync_docs.html

otio_events:
  Play:
    _category: Playback
    default: { ... }

protocol_messages:
  WhoIsMaster:
    _category: Session
    default: { ... }
```

**Rationale:** Keeps the two namespaces separate (no name collision risk), allows `meta` to absorb the current `--input` CLI arg, and maps cleanly onto the layered sidebar structure. The existing `config.yml` and `protocol_messages_config.yml` contents slot directly under their respective keys with no data changes.

**Alternative considered:** Flat namespace with `_source: otio|protocol` discriminator — rejected because it risks name collisions and makes the config harder to read at a glance.

### D2: Layered sidebar (OTIO Events first, Protocol Messages second)

The sidebar groups entries into two top-level sections mirroring the two protocol layers, not a merged-by-category view.

**Rationale:** OTIO SyncEvents and ProtocolMessages live at genuinely different abstraction levels. Interleaving them by category (e.g. one "Session" section mixing both) would obscure the architecture. Readers using the doc as a reference need to know which layer they are looking at.

### D3: Unified `SchemaClass` data model extended with `source_type` and `event`

```python
@dataclass
class SchemaClass:
    name: str
    schema_label: str   # OTIO: "Play.1"; Protocol: command_schema value
    base_class: str     # OTIO: parent class name; Protocol: "ProtocolMessage"
    description: str
    parameters: List[Parameter]
    category: str
    source_type: str    # "otio" | "protocol"
    event: str = ""     # Protocol-only: event name
```

`Parameter.required` is always `False` for protocol messages (no required/optional distinction at that layer).

**Rationale:** Reusing `SchemaClass` keeps the HTML generator unchanged — it only needs to conditionally render the extra `event` badge when `source_type == "protocol"`.

### D4: Protocol message data extracted via registry introspection (unchanged)

The protocol section continues to import and call `pm.registered_messages()` and `cls.doc_fields()` at generation time. No AST parsing needed.

**Rationale:** The protocol message module is pure Python with no circular imports; runtime import is cheap and already proven. AST parsing would be more complex without added value.

### D5: Markdown introduction rendered with `mistune`

`meta.introduction` points to a `.md` file; its content is rendered to HTML using `mistune` (already in `requirements.txt`) and injected as the first content section, replacing the current hardcoded overview paragraph.

**Rationale:** `mistune` is already a project dependency. If `meta.introduction` is absent, the section falls back to a default paragraph (backward compatible).

### D6: Python tabs for protocol messages with layer comment

Protocol Python examples are generated as:
```python
# Protocol Message (transport layer)
from otio_sync_core.protocol_messages import WhoIsMaster

msg = WhoIsMaster(requester_guid="peer-abc123")
```

OTIO Python examples retain their existing comment:
```python
# OTIO SyncEvent (serialized as OpenTimelineIO object)
import opentimelineio as otio
...
```

`black` formatting is applied to both; failures fall back to unformatted (existing behaviour).

**Rationale:** The comment is the simplest way to communicate the layer distinction without extra UI elements. Readers copy-pasting get the context inline.

### D7: Old scripts and configs retired

`otio_doc_generator.py`, `protocol_doc_generator.py`, and `protocol_messages_config.yml` are deleted. `config.yml` is restructured in place.

**Rationale:** Keeping old scripts alongside the new one would create confusion about the canonical generator. The Makefile is updated to call the new script.

## Risks / Trade-offs

- **Protocol module import fails at generation time** → If `otio_sync_core` is missing (e.g. running in a bare env), the protocol section will be empty with a warning. Mitigation: generator prints a clear error message and exits non-zero if the import fails.
- **`black` not installed** → Python examples silently fall back to unformatted code. Mitigation: existing behaviour, no change.
- **`mistune` API changes** → `mistune` 3.x has a different API than 2.x. `requirements.txt` pins `mistune==3.1.1`; use the v3 API (`mistune.create_markdown()`). Mitigation: pin is already in place.
- **Config migration is manual** → Users with customised `config.yml` or `protocol_messages_config.yml` must re-nest their entries. Mitigation: document the migration in the Makefile comment and/or a brief note at the top of the new config.

## Migration Plan

1. Create `docs/doc_generator.py`
2. Restructure `docs/config.yml` — add `meta:` and `otio_events:` wrapper, merge `protocol_messages_config.yml` under `protocol_messages:`
3. Update `docs/Makefile` — replace two separate generator calls with one `doc_generator.py --config config.yml` call
4. Delete `docs/otio_doc_generator.py`, `docs/protocol_doc_generator.py`, `docs/protocol_messages_config.yml`

Rollback: restore the deleted files from git history. No database or network state is involved.
