## Why

A synced review session today carries no color management context: peers don't know what colorspace a clip's media is in, what working space the timeline is authored against, or what the delivery target is. Without that, CDL and every color-accurate effect is not executable, and OpenRV and xStudio cannot agree on what a frame should look like. The OTIO core RFC ("OTIO Color Pipeline Model") defines the eventual native fields, but they do not exist in OTIO yet — so we bridge the model through OTIO `metadata` now, mirroring the RFC shape verbatim so promotion to native fields later is a rename rather than a redesign.

## What Changes

- Introduce a **color metadata convention** mirroring the RFC verbatim:
  - `Timeline.metadata["color"]` — a config group: `config`, `working_space`, `output_space`.
  - `Clip.metadata["color_space"]` — the clip's input colorspace as a vocabulary-prefixed string (e.g. `"ocio:ACEScg"`).
- Define **hierarchical resolution**: a clip's effective input space resolves clip `color_space` → timeline `working_space` → host default.
- Define the **string-prefix vocabulary convention** (`ocio:`, `interop:`, plus others preserved verbatim). `ocio:` / `interop:` are honored; unknown prefixes round-trip unchanged. No cross-vocabulary translation in the protocol.
- **Live-sync** color over the **existing** `SetProperty` message (`path="metadata/color"` / `"metadata/color_space"`); color also rides `AddTimeline`/`StateSnapshot` at load time. No new protocol message is added.
- Assume **all peers share the same OCIO config**; each peer re-resolves names against its own config.
- **OpenRV adapter**: read/write the color metadata to/from the OCIO source-setup pipeline (per-source input colorspace) and the display pipeline (output/view).
- **xStudio adapter**: read/write the same metadata to/from `src_colour_mgmt_metadata` (`config`, `working_space`, source colourspace) and the ColourPipeline Display/View attributes.
- **Deferred** (explicitly out of scope): `MediaReference.color_space` as a separate live-synced target (media input color lives on the Clip for v1), `color_space_source` provenance records, and `cicp:`/`resolve:`/`aces:` name resolution.

## Capabilities

### New Capabilities
- `color-pipeline-sync`: The color metadata schema (`Timeline.metadata["color"]`, `Clip.metadata["color_space"]`), the vocabulary-prefix convention, hierarchical resolution, and the live-sync semantics over the existing `SetProperty` message including the shared-config assumption and verbatim preservation of unknown names.
- `color-pipeline-openrv`: How the OpenRV plugin reads color metadata and applies it to its OCIO source/display pipeline, and writes user color changes back into the metadata for broadcast.
- `color-pipeline-xstudio`: How the xStudio plugin reads color metadata and applies it to `src_colour_mgmt_metadata` and the ColourPipeline Display/View, and writes user color changes back into the metadata for broadcast.

### Modified Capabilities
<!-- None: color is carried by the existing SetProperty / AddTimeline / StateSnapshot messages without changing their requirements. -->

## Impact

- **Protocol**: No new message types. Relies on the existing `SetProperty` (`metadata/...` paths) and the OTIO-bearing `AddTimeline`/`StateSnapshot`/`InsertChild` messages in `python/otio_sync_core/protocol_messages.py`.
- **OpenRV plugin**: New read/write path against `ocio_source_setup` / OCIONode and the display pipeline.
- **xStudio plugin**: New read/write path against the OCIO colour pipeline (`src_colour_mgmt_metadata`, ColourPipeline attributes).
- **Forward compatibility**: Metadata keys mirror the OTIO Color Pipeline Model RFC, so a future migration to native `Timeline.color` / `Clip.color_space` / `MediaReference.color_space` fields is a key move, not a reshape.
- **Coexistence**: Distinct from the existing `ocio_annotation_color_space` (the space annotation strokes are drawn in); this change adds media/timeline color and must not conflate the two.
