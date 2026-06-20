## Context

Currently, the OTIO synchronization logic between xStudio and OpenRV handles paint strokes seamlessly but drops or corrupts text annotations (captions). The core protocol requires a shared dictionary of commands translated from application-native formats to the OTIO `SyncEvent` flat schema. We have discovered that three discrete data mapping issues are completely disabling text synchronization for both new and edited annotations.

## Goals / Non-Goals

**Goals:**
- Fix the 3x font size reduction occurring on each xStudio/OpenRV roundtrip.
- Ensure text node UUIDs generated in the OTIO data structure are durably applied to the xStudio caption schema.
- Prevent OpenRV crashes (`UnboundLocalError`) when receiving brand new text annotations.

**Non-Goals:**
- We are not changing the structure of the JSON over the network or RabbitMQ messaging payloads.
- We are not altering paint stroke logic or how xStudio processes text events from `AnnotationsUI`.

## Decisions

- **Consistent Font Sizing Factor (5000.0)**
  Currently `plugin.py` scales up by `5000.0` but divides by `15000.0` on import. We will uniformly use `5000.0` for OpenRV-to-xStudio text coordinate mapping to guarantee lossless roundtrips.
- **Cache Missing UUIDs in `xs_annotation_codec.py`**
  xStudio relies on `sync_events_to_xs_captions` to create native `caption` dictionaries from SyncEvents. By failing to include `"uuid": getattr(cmd, "uuid")` during parsing, local cache comparisons subsequently fail and append duplicates. Injecting the UUID natively during decoding fixes the merge operation.
- **Defensive Lexical Scoping in OpenRV plugin**
  The `text_val` assignment is accidentally tucked inside the `if _paint_node_cache` block. By defining `text_val = ev.text or ""` alongside `uuid_val`, we ensure the `text_data` dict is populated correctly even when the node does not yet exist.

## Risks / Trade-offs

- **Risk:** Existing legacy recordings or annotations using the skewed `15000.0` scale factor might look slightly incorrect when replayed.
- **Mitigation:** Live synchronized review takes precedence over matching visually broken legacy text scale artifacts. This fix creates a reliable foundation going forward.
