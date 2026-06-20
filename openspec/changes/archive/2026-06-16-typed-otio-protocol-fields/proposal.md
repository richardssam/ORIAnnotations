## Why

The four protocol message fields that carry OTIO data (`AddTimeline.timeline`, `StateSnapshot.timelines`, `InsertChild.child_data`, `ReplaceAnnotationCommands.commands`) are typed as plain `dict`/`list` holding *pre-serialized* OTIO. Every producer must remember to wrap the object with `_otio_to_dict` before construction, and every consumer must unwrap with `_dict_to_otio` after reading the field — the same conversion duplicated across ~6 call sites in `manager.py` and `patcher.py`. The field type lies about its contents (it says "dict" but means "a Timeline"), and a missed wrap/unwrap is a silent wire-format bug. We want the message to be the single place that owns OTIO ⇄ wire conversion.

## What Changes

- Change the four OTIO-bearing fields to carry real `opentimelineio` objects instead of pre-serialized dicts. Producers pass the object directly; no more `_otio_to_dict` at the call site.
- Move OTIO serialization into each message's `to_payload()` (the one place that emits wire form).
- Make deserialization **lazy** via an `.as_otio()` accessor on the message, **not** eager in `from_payload()`. This preserves the pre-guard skip in handlers like `_h_add_timeline`, which checks `tl_guid not in self._timelines` *before* paying the deserialization cost (and avoids throwing before that guard).
- Import `opentimelineio` **lazily inside the methods** so `protocol_messages.py` remains importable in isolation for the documentation generator.
- **Leave `PartialAnnotation.events` as raw dicts** — it is the hottest path and the host codec already speaks dict; typing it as OTIO would force deserialize-then-reserialize churn for no benefit.
- Remove the now-redundant `_otio_to_dict`/`_dict_to_otio` wrap/unwrap boilerplate from the affected `manager.py`/`patcher.py` call sites.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `otio-sync-core`: The OTIO session payload messages and the timeline/snapshot messages now own their OTIO ⇄ wire conversion (serialize in `to_payload`, lazily deserialize via `as_otio`) rather than relying on callers to pre-serialize. Hot-path and registry behavior are unchanged.

## Impact

- `python/otio_sync_core/protocol_messages.py`: field type changes on 4 messages, `to_payload` now serializes, new `as_otio()` accessor(s), lazy `opentimelineio` import.
- `python/otio_sync_core/manager.py`: drop `_otio_to_dict` at `AddTimeline`/`StateSnapshot`/`ReplaceAnnotationCommands` construction; consume via `as_otio()` in handlers.
- `python/otio_sync_core/patcher.py`: drop `_otio_to_dict`/`_dict_to_otio` at `InsertChild` construction/application; consume via `as_otio()`.
- Wire format is **unchanged** (byte-for-byte identical payloads), so cross-version interop is preserved.
- Doc generator must still import `protocol_messages.py` without `opentimelineio` installed.
