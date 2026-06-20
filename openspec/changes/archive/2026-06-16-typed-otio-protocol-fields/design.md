## Context

`protocol_messages.py` is the single source of truth for the wire form of each transport message. Its design doc states three constraints: messages are **pure data**, the module stays **importable in isolation** (so the doc generator needs no `opentimelineio`), and **hot-path** messages (`PartialAnnotation`, `PlaybackSettingsSet`) avoid reflective serialization.

Four fields carry OTIO content but are typed as `dict`/`list` holding *already-serialized* OTIO:

| Message | Field | Currently built by | Currently consumed by |
|---|---|---|---|
| `AddTimeline` | `timeline` | `_otio_to_dict(tl)` (`manager.py:379`) | `_dict_to_otio` (`manager.py:1303`) |
| `StateSnapshot` | `timelines` | `{g: _otio_to_dict(tl)}` (`manager.py:638`) | `_dict_to_otio` in handler |
| `InsertChild` | `child_data` | `_otio_to_dict(child)` (`patcher.py:256`, `manager.py:1025`) | `_dict_to_otio` (`patcher.py:416`) |
| `ReplaceAnnotationCommands` | `commands` | `[_otio_to_dict(e)…]` (`manager.py:1105`) | `_dict_to_otio` (`patcher.py:404`) |

The conversion is duplicated at ~6 sites; a missed wrap/unwrap is a silent wire bug. The wire bytes are produced by `otio.adapters.write_to_string(obj, "otio_json", indent=-1)` then `json.loads`; reverse is `read_from_string(json.dumps(d))`.

## Goals / Non-Goals

**Goals:**
- The four messages own their OTIO ⇄ wire conversion: callers pass the OTIO object, handlers read it back through one sanctioned accessor.
- Wire payload is **byte-for-byte identical** to today (interop preserved).
- The module remains importable without `opentimelineio` installed (doc generator).
- Preserve the receive-side optimization where a handler can skip work **before** paying deserialization cost.

**Non-Goals:**
- `PartialAnnotation.events` is **not** changed — it stays `list[dict]`. It is the hottest path and the host codec already produces dicts; typing it as OTIO would force deserialize-then-reserialize churn.
- No change to the envelope, registry, dispatch, or settings/selection messages.
- No memoization/caching of deserialized objects (handlers already call the accessor once).

## Decisions

### 1. Fields hold the OTIO object on the send side; the raw dict on the receive side

The field type becomes `Timeline | dict` (and `dict[str, Timeline | dict]` / `list[SerializableObject | dict]` for the collections). Concretely:
- **Producer** constructs the message with a real OTIO object. `to_payload()` serializes it.
- **`from_payload()`** stores the **raw wire dict unchanged** — it does *not* deserialize.
- A new **`as_otio()`** accessor returns the OTIO form, deserializing on demand.

Why a union rather than "always an OTIO object": deserializing eagerly in `from_payload()` would defeat the existing guard in `_h_add_timeline`, which checks `tl_guid not in self._timelines` *before* touching the OTIO ([manager.py:1302](../../../python/otio_sync_core/manager.py#L1302)) — a duplicate snapshot must stay free, and must not risk throwing before the guard runs. Lazy `as_otio()` keeps that skip.

*Alternative considered — always-object field with eager `from_payload`:* simplest type story, but loses the pre-guard skip and moves deserialization failures ahead of the guard. Rejected.

*Alternative considered — smart `from_otio()` constructor, field stays dict:* keeps full isolation but the stored field is still a dict, so it only half-satisfies "specify the OTIO object." Rejected in favor of the cleaner accessor model.

### 2. `as_otio()` is the single accessor; it coerces and tolerates both forms

`as_otio()` returns the message's OTIO content in object form:
- `AddTimeline.as_otio() -> Timeline`
- `InsertChild.as_otio() -> SerializableObject`
- `StateSnapshot.as_otio() -> dict[str, Timeline]`
- `ReplaceAnnotationCommands.as_otio() -> list[SerializableObject]`

It deserializes any element currently in dict form and passes through any element already an OTIO object — so it is correct whether the message was built locally (objects) or received (dicts). Symmetrically, `to_payload()` coerces the other way: serialize if it's an object, pass through if it's already a dict (cheap robustness for relays).

One method name across all four keeps the contract uniform; the return shape is fixed per class.

### 3. `opentimelineio` is imported lazily, inside the conversion methods

`to_payload()` and `as_otio()` do `import opentimelineio` at call time (cheap after first — `sys.modules` cached). The module top level stays OTIO-free, so the doc generator can import `protocol_messages.py` and read field/`doc_field` metadata without OTIO installed. `to_payload()`/`as_otio()` are never called by the doc generator.

### 4. Conversion helpers live in `protocol_messages.py`, not imported from `patcher.py`

To avoid a `protocol_messages → patcher` dependency, the module defines its own private `_to_wire(obj)` / `_from_wire(d)` mirroring the exact existing format (`otio_json`, `indent=-1`, `json.loads`/`json.dumps`). `patcher._otio_to_dict`/`_dict_to_otio` remain for any non-message uses but are dropped from the four migrated call sites.

### 5. Call-site cleanup

- `manager.py`: pass the `Timeline`/object directly to `AddTimeline`, `StateSnapshot`, `ReplaceAnnotationCommands`; handlers call `msg.as_otio()`.
- `patcher.py`: pass `child_obj` directly to `InsertChild`; `apply_patch` reads `msg.as_otio()` for `InsertChild` and `ReplaceAnnotationCommands`.

## Risks / Trade-offs

- **Field type is a union (`Timeline | dict`), less honest than a pure type.** → `as_otio()` is the sanctioned read path; direct field access is discouraged and documented. The union is the price of the lazy-deserialize skip we explicitly want.
- **Lazy import surfaces a missing-OTIO error at send/receive instead of import time.** → Acceptable: every runtime host ships OTIO; only the doc generator imports without it, and it never calls the conversion methods.
- **Wire-format drift if `_to_wire`/`_from_wire` diverge from the old helpers.** → They must use the identical `otio_json`/`indent=-1` path; covered by a round-trip + byte-equivalence test against a captured payload.
- **`as_otio()` called twice would deserialize twice.** → Handlers call it once (matches today). Memoization deliberately deferred (Non-Goal) to avoid dataclass-mutation complexity.

## Migration Plan

Wire format is unchanged, so peers on old and new code interoperate freely and messages can be migrated one class at a time. Steps: add `_to_wire`/`_from_wire` and `as_otio()` + object-typed fields per message; update producers to pass objects; update handlers to call `as_otio()`; delete the now-dead `_otio_to_dict`/`_dict_to_otio` call sites. Rollback is a straight revert (no persisted state, no schema change).

## Open Questions

- Should `as_otio()` memoize on first call (small win if any handler ends up reading twice), or stay stateless? Current plan: stateless. Revisit only if a second read appears.
