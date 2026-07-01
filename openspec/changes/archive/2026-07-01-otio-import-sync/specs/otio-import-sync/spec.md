## ADDED Requirements

### Requirement: OTIO-Origin Timeline Detection

The plugin SHALL classify each synced timeline by origin so peers route it identically. A timeline whose RV root is an RVStackGroup produced by movieproc `otioFile` expansion (carrying an `.otio.metadata` property) is **OTIO-origin**; all other timelines are **native**. The plugin SHALL record the classification on the timeline as `metadata.sync.origin` (e.g. `"otio_import"` vs `"native"`) and SHALL exclude RV's default `defaultSequence` and `defaultStack` nodes from sequence and stack scans.

#### Scenario: Imported OTIO timeline is classified as OTIO-origin

- **WHEN** an `.otio` file is imported and RV expands it into a `tracks` RVStackGroup bearing `.otio.metadata`
- **THEN** the plugin SHALL register the timeline with `metadata.sync.origin = "otio_import"`
- **AND** SHALL treat the RVStackGroup (not `defaultSequence`) as the timeline root

#### Scenario: Native clip list is classified as native

- **WHEN** a timeline is built from an ad-hoc clip list with no `otioFile`-expanded RVStackGroup root
- **THEN** the plugin SHALL register it with `metadata.sync.origin = "native"`

#### Scenario: Default RV containers are ignored

- **WHEN** the plugin scans for sequences or stacks
- **THEN** it SHALL skip `defaultSequence` and `defaultStack` and SHALL NOT register them as timelines

### Requirement: Wait for OTIO Expansion Before Snapshot

OTIO import in RV is asynchronous (the movieproc placeholder is expanded after progressive loading). The plugin SHALL NOT snapshot or register an OTIO-origin timeline until its `tracks` RVStackGroup and contained sequence have materialized, replacing any retry-on-empty heuristic.

#### Scenario: Snapshot deferred until expansion completes

- **WHEN** an OTIO file is imported but the `tracks` RVStackGroup has not yet appeared in the node graph
- **THEN** the plugin SHALL defer the snapshot
- **AND** SHALL perform it once the RVStackGroup and its sequence exist

#### Scenario: Empty placeholder is not registered as a timeline

- **WHEN** only the blank `otioFile` movieproc placeholder is present (no expanded sequence)
- **THEN** the plugin SHALL NOT register an empty timeline for it

### Requirement: Topology Changes Use Whole-OTIO Snapshot

For OTIO-origin timelines, structural (topology) changes — inserting or removing clips, and large re-edits — SHALL be synced as a whole-OTIO snapshot rather than per-child patches. The plugin SHALL export the snapshot via RV's `otio_writer.create_timeline_from_node(<tracks stack>)` and SHALL apply a received snapshot via RV's `otio_reader.create_rv_node_from_otio(timeline)`. Reordering of clips within an OTIO-origin timeline SHALL NOT be tracked separately; it rides the snapshot push.

#### Scenario: Clip inserted into an OTIO timeline pushes a snapshot

- **WHEN** a clip is added to or removed from an OTIO-origin timeline in RV
- **THEN** the plugin SHALL export the timeline via `otio_writer.create_timeline_from_node` and broadcast it as a whole-OTIO push

#### Scenario: Peer applies a received OTIO snapshot

- **WHEN** a peer receives a whole-OTIO push for an OTIO-origin timeline
- **THEN** it SHALL build the RV node graph via `otio_reader.create_rv_node_from_otio`
- **AND** SHALL NOT attempt to apply the change through the per-child patch path

#### Scenario: Reorder within an OTIO timeline is not separately broadcast

- **WHEN** clips are reordered within an OTIO-origin timeline
- **THEN** the plugin SHALL NOT emit `MOVE_CHILD` patches for it
- **AND** any structural divergence SHALL be reconciled by a whole-OTIO push

### Requirement: Attribute Changes Use Incremental Patches

For OTIO-origin timelines, attribute changes on an existing clip SHALL be synced as incremental patches keyed by the clip's guid, not as a whole-OTIO push. This SHALL cover: media swap (the clip's `media_reference.target_url`), cut trim (the clip's `source_range`), and CDL/color. Cut trim SHALL be detected by a watcher that diffs the RVSequence EDL `in`/`out` arrays and maps a change to a `source_range` patch on the matching clip. CDL SHALL be read using RV's `cdlHook` and sent through the existing color property channel.

#### Scenario: Media swap is a targeted property patch

- **WHEN** the media of a clip in an OTIO-origin timeline is swapped
- **THEN** the plugin SHALL emit a property patch updating that clip's `media_reference.target_url`
- **AND** SHALL preserve the clip's guid and its bound annotations

#### Scenario: Cut trim detected from EDL diff

- **WHEN** the in/out length of a cut changes in the RVSequence EDL
- **THEN** the EDL-diff watcher SHALL emit a property patch updating the matching clip's `source_range`
- **AND** SHALL NOT trigger a whole-OTIO push

#### Scenario: CDL change flows through the color channel

- **WHEN** a CDL/color change is made on a clip in an OTIO-origin timeline
- **THEN** the plugin SHALL read it via RV's `cdlHook` and emit it on the existing color property channel keyed by the clip guid

### Requirement: Identity Round-Trips Through RV Reader and Writer

The plugin SHALL set `metadata.sync.guid` on OTIO timelines, tracks, and clips so that the same guid survives a round-trip through RV's `otio_reader` (persisted on the node as `.otio.metadata`) and `otio_writer` (restored on export). Attribute patches and annotation bindings SHALL resolve their target by this guid across both the snapshot and patch models.

#### Scenario: Guid preserved across import and export

- **WHEN** an OTIO timeline carrying `metadata.sync.guid` on its clips is applied via `otio_reader` and later exported via `otio_writer`
- **THEN** each clip's `metadata.sync.guid` SHALL be unchanged

#### Scenario: Annotation binds to the correct clip after snapshot

- **WHEN** a whole-OTIO snapshot replaces an OTIO-origin timeline's nodes
- **THEN** existing annotations keyed by clip guid SHALL remain bound to the same clips

#### Scenario: Re-exported OTIO matches the imported reference modulo guids

- **WHEN** an imported OTIO timeline is synced and re-exported from either app
- **THEN** its structure — track set, clip order, clip names, `source_range`s, and media references — SHALL match the original `.otio` file
- **AND** differences SHALL be limited to volatile metadata (sync guids, RV-injected `.otio.*` node metadata) and media path normalization

### Requirement: Native Sync and Annotation Deltas Unchanged

This capability SHALL NOT alter sync of native (non-OTIO-origin) timelines, which continue to use the fine-grained `insert/move/remove_child` model with first-class reordering. Live annotation strokes SHALL continue to flow through the existing per-stroke delta path for both OTIO-origin and native timelines.

#### Scenario: Native timeline keeps fine-grained reorder sync

- **WHEN** clips are reordered in a native timeline
- **THEN** the plugin SHALL emit `MOVE_CHILD` patches as before

#### Scenario: Annotations stay on the live delta path

- **WHEN** a user paints a stroke on a clip in either timeline class
- **THEN** the stroke SHALL be synced via the existing per-stroke annotation delta path, not via a whole-OTIO push
