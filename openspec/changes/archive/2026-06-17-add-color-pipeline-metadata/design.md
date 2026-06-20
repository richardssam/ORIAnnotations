## Context

The sync protocol coordinates OTIO timelines across OpenRV and xStudio peers but carries no color management context. The OTIO core "Color Pipeline Model" RFC defines eventual native fields (`Timeline.color`, `Composable.color_space`, `MediaReference.color_space`, `MediaReference.color_space_source`) but they do not exist in OTIO yet. We bridge the model through OTIO `metadata` now, mirroring the RFC verbatim, and sync it over the protocol that already exists.

Key facts established during exploration:
- `SetProperty` ([protocol_messages.py](../../../python/otio_sync_core/protocol_messages.py)) already syncs arbitrary `metadata/...` paths. The patcher's `set_property` ([patcher.py](../../../python/otio_sync_core/patcher.py)) walks the metadata dict and **auto-creates intermediate dicts**, so `metadata/color/working_space` works even when `color` is absent.
- The object map is keyed on `metadata["sync"]["guid"]`, assigned by `ensure_guid_and_map` to composables. **Timelines and Clips get GUIDs; MediaReferences do not** — they cannot be a `SetProperty` target.
- OpenRV drives per-source input color via the `ocio.inColorSpace` property on an OCIO node in `ocio_source_setup` (auto-detect uses `config.parseColorSpaceFromString`); output is the display/view stage.
- xStudio's OCIO engine reads everything from a `src_colour_mgmt_metadata` JsonStore (keys include `config`, `working_space`, source colourspace, `path`), populated via a media hook / `media_source().set_metadata("/colour_pipeline/...")`; Display and View are exposed as ColourPipeline attributes.
- A separate `ocio_annotation_color_space` already exists on `ReviewItemFrame` — the space annotation *strokes* live in. This change adds *media/timeline* color and must not reuse or collide with that field.

## Goals / Non-Goals

**Goals:**
- Carry the RFC color model verbatim in `Timeline.metadata["color"]` and `Clip.metadata["color_space"]`.
- Live-sync color over the existing `SetProperty` message; no new message type.
- Read/write the metadata in both the OpenRV and xStudio color pipelines.
- Keep the metadata shape promotable to native OTIO fields with a key move, not a reshape.

**Non-Goals:**
- `MediaReference.color_space` as a distinct live target (media input color lives on the Clip for v1).
- `color_space_source` provenance records (CICP/EXR/ICC).
- Resolution of `cicp:` / `resolve:` / `aces:` vocabularies, and any cross-vocabulary translation.
- Output as a list of named display/view targets (RFC open question); single `output_space` for v1.

## Decisions

### Metadata layout mirrors the RFC verbatim
- `Timeline.metadata["color"] = {"config": str?, "working_space": str?, "output_space": str?}`
- `Clip.metadata["color_space"] = "ocio:..."` (vocabulary-prefixed string)

Rationale: when OTIO core lands the native fields, migration is `clip.color_space = clip.metadata.pop("color_space")` and `timeline.color = timeline.metadata.pop("color")`. Any ORI-specific shape would impose a translation layer forever. The keys are clear of the existing `metadata["sync"]`, `metadata["annotation_commands"]`, and `ocio_annotation_color_space`.

### Sync rides existing messages
- Clip change → `SetProperty(target_uuid=clipGUID, path="metadata/color_space", value="ocio:...")`.
- Timeline change → `SetProperty(target_uuid=timelineGUID, path="metadata/color/<field>", value=...)`, relying on the patcher's intermediate-dict creation.
- Load time → color is already inside the OTIO payload of `AddTimeline`/`StateSnapshot`/`InsertChild`; nothing extra needed.

No protocol message is added; the wire format is unchanged, so older peers interoperate (they simply ignore unfamiliar metadata).

### Media input color lives on the Clip
MediaReferences have no sync GUID and the path syntax cannot descend into a child object, so a live `MediaReference.color_space` would require GUID-ing media references *and* extending the path grammar — beyond v1. The RFC's hierarchical resolution already lets `Clip.color_space` express "this element's input space," so we use the Clip and note the media-reference distinction as future work.

### Names only on the wire; peers re-resolve against their own config
The protocol transmits colorspace names and the `config` identifier, never resolved transforms. Each peer resolves against its own (assumed-identical) OCIO config. Unknown/unhonored prefixes (`cicp:`, `resolve:`, `aces:`, `custom:`) are preserved byte-for-byte on round-trip; only `ocio:`/`interop:` are actively resolved in v1.

### Host adapter mapping
| Color metadata | OpenRV | xStudio |
| --- | --- | --- |
| `timeline.color.config` | active OCIO config (`ocio_source_setup`) | `src_colour_mgmt_metadata["config"]` |
| `timeline.color.working_space` | OCIO node working space | `src_colour_mgmt_metadata["working_space"]` |
| `timeline.color.output_space` | display/view stage | ColourPipeline `Display` + `View` attrs |
| `clip.color_space` (resolved) | source OCIO node `ocio.inColorSpace` | media source colourspace via `/colour_pipeline` metadata |

Write-back is the inverse: a user color change in either host updates the corresponding metadata and broadcasts it via `SetProperty`.

### Unresolvable names warn, never fail
If a name cannot be resolved against the active config, the adapter leaves that source/media at its host default and emits a warning. A color string must never abort a timeline load or a sync apply.

## Risks / Trade-offs

- **Shared-config assumption is unenforced.** If peers run different OCIO configs, the same name may resolve differently. v1 assumes identical configs; mismatch is out of scope and at worst yields a per-peer visual difference, not a protocol failure. The `config` identifier is carried so a future version could detect mismatch.
- **Output as a single space, not a per-monitor list.** The RFC leaves multi-output and "output is a hint" open. v1 takes the simplest shape; a list can be layered later without changing the clip-level model.
- **Output-space live-sync is disabled (decided during implementation).** Both adapters initially broadcast the viewport's OCIO **Display** as `output_space`, but that value is the local *monitor* (e.g. `Apple Display P3 - Display`) — device-centric — so a joining peer pushed its monitor onto everyone else's display and corrupted their view. Output sync is therefore gated off behind `_SYNC_OUTPUT_SPACE = False` in both `color_sync.py` modules (the code remains for a future, monitor-aware design). Only the **input** colorspace is live-synced, which is authored data and peer-independent. This aligns with the RFC's "output is a per-device hint" open question.
- **Bidirectional write-back loops.** Applying a received color change must not re-broadcast it (echo). The existing patcher fires `on_property_changed` on apply; adapters must distinguish locally-originated changes from applied-remote ones, exactly as other synced properties already do.
- **xStudio ingestion path (media hook vs. direct `set_metadata`) is an implementation choice.** Both reach the same `src_colour_mgmt_metadata`; the spec is satisfied either way, decided at implementation time against the running build.
- **OTIO build fragility.** One RV build throws on nested metadata reads ([[project_rv_otio_bad_any_cast]]); the `color` group is a shallow dict of strings, which should avoid the deep-nesting trigger, but adapter reads should be defensive.
