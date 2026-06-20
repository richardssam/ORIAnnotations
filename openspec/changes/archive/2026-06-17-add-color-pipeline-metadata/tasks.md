# Tasks

## 1. Color metadata schema & resolution (color-pipeline-sync)
- [x] 1.1 Define the metadata layout constants/helpers: `Timeline.metadata["color"]` (`config`, `working_space`, `output_space`) and `Clip.metadata["color_space"]`, mirroring the RFC keys verbatim.
- [x] 1.2 Implement a vocabulary-prefix parser: split on the first `:` into `(tag, name)`; treat bare strings per host default; preserve unknown tags verbatim. Honor `ocio:`/`interop:`; do not translate.
- [x] 1.3 Implement `resolve_input_colorspace(clip)`: clip `color_space` → timeline `working_space` → host default, with no media-reference/provenance lookup.
- [x] 1.4 Add a shared read helper that defensively reads the shallow `color` group (guard against the nested-metadata read defect on the older OTIO build).

## 2. Live sync over existing SetProperty (color-pipeline-sync)
- [x] 2.1 Confirm clip color changes broadcast as `SetProperty(target=clipGUID, path="metadata/color_space")` with no new message type.
- [x] 2.2 Confirm timeline color changes broadcast as `SetProperty(target=timelineGUID, path="metadata/color/<field>")`, relying on the patcher's intermediate-dict creation.
- [x] 2.3 Verify color survives `AddTimeline`/`StateSnapshot`/`InsertChild` round-trip with no extra handling.
- [x] 2.4 Ensure applying a received color change does not re-broadcast (no echo loop), matching how other synced properties are guarded.

## 3. OpenRV adapter (color-pipeline-openrv)
- [x] 3.1 On timeline load/receive, resolve each clip's input colorspace and set the source OCIO node `ocio.inColorSpace`. _(validated live: receiver applies input colorspace)_
- [x] 3.2 Map timeline `working_space`/`output_space` to the OCIO working space and display/view stage. _(working_space drives resolution; output_space apply DISABLED — gated behind `_SYNC_OUTPUT_SPACE=False`, see 3.5)_
- [x] 3.3 Warn (do not fail) when a name cannot be resolved against the active OCIO config; leave the source at its default.
- [x] 3.4 Write-back: when the user changes a source colorspace, update `Clip.metadata["color_space"]` (prefixed) and broadcast via `SetProperty`. _(validated live: event-driven from graph-state-change)_
- [ ] 3.5 Write-back: when the user changes display/view, update `Timeline.metadata["color"]["output_space"]` and broadcast. _(DEFERRED: the viewport OCIO Display is the local monitor and device-centric — broadcasting it clobbered peers' displays. Code present but gated off via `_SYNC_OUTPUT_SPACE=False`; revisit per RFC "output is a hint".)_

## 4. xStudio adapter (color-pipeline-xstudio)
- [ ] 4.1 On timeline load/receive, populate `src_colour_mgmt_metadata` `config` and `working_space` from the timeline color group. _(DEFERRED: xStudio derives working space from the shared OCIO config; needs investigation of whether per-media `/colour_pipeline/working_space` flows into the engine before wiring)_
- [x] 4.2 Set each media's source colourspace from the clip's resolved input colorspace. _(validated live: apply_clip_color_space → media_source `/colour_pipeline/override_input_cs`)_
- [ ] 4.3 Map timeline `output_space` to the ColourPipeline `Display` and `View` attributes. _(DEFERRED: same device-centric reason as 3.5; gated off via `_SYNC_OUTPUT_SPACE=False`.)_
- [x] 4.4 Warn (do not fail) when a name cannot be resolved; leave the media at its default.
- [x] 4.5 Write-back: when the user changes a source colourspace, update `Clip.metadata["color_space"]` and broadcast via `SetProperty`. _(validated live: polls media `/colour_pipeline/override_input_cs`)_
- [ ] 4.6 Write-back: when the user changes Display/View, update `Timeline.metadata["color"]["output_space"]` and broadcast. _(DEFERRED: same device-centric reason as 3.5; gated off via `_SYNC_OUTPUT_SPACE=False`.)_

## 5. Verification
- [x] 5.1 Unit tests: prefix parser (bare/known/unknown/colon-in-name) and hierarchical resolution (clip override, timeline fallback, host default).
- [x] 5.2 Round-trip test: unknown-prefix name (`resolve:`/`cicp:`) survives receive + re-broadcast byte-for-byte.
- [x] 5.3 Cross-host test: a clip color change made in one host applies and re-resolves correctly in the other (shared config). _(validated live: RV↔xStudio input colorspace both directions)_
- [x] 5.4 Confirm `ocio_annotation_color_space` is untouched and the new `color`/`color_space` keys do not collide with existing metadata namespaces (`sync`, `annotation_commands`).
