## ADDED Requirements

### Requirement: xStudio Applies Input Color Metadata to Its Colour Pipeline
The xStudio plugin SHALL read the color metadata from a synced timeline and apply a clip's resolved input colorspace to its media by writing the media source metadata key `/colour_pipeline/override_input_cs` — the same key xStudio's OCIO plugin writes for a user Source-colourspace change and that the OCIO engine reads back. Resolution SHALL follow the hierarchical rule defined by the color-pipeline-sync capability.

#### Scenario: Clip input colorspace sets the media source colourspace
- **WHEN** xStudio loads or receives a clip whose resolved input colorspace is known
- **THEN** the plugin SHALL set that media's `/colour_pipeline/override_input_cs` accordingly

#### Scenario: Unresolvable name leaves the media unmanaged and warns
- **WHEN** a clip's color string cannot be resolved against the active OCIO config
- **THEN** xStudio SHALL leave that media's colour handling at its default and SHALL emit a warning rather than failing the load

### Requirement: xStudio Writes Input Color Changes Back to Metadata
When a user changes a media's source colourspace in xStudio, the plugin SHALL write the change into `Clip.metadata["color_space"]` using the vocabulary-prefix convention, so the change is broadcast through the existing `SetProperty` path.

#### Scenario: User changes a source colourspace
- **WHEN** the user sets a media's source colourspace in xStudio
- **THEN** the plugin SHALL update that clip's `metadata["color_space"]` with the prefixed name and broadcast it via `SetProperty`

### Requirement: xStudio Does Not Live-Sync Output Space
The xStudio plugin SHALL NOT broadcast its viewport OCIO Display/View as the timeline `output_space`, and SHALL NOT apply a received `output_space` to its ColourPipeline Display/View. The viewport Display is device-centric (the local monitor), so propagating it would override each peer's display. Output-space synchronisation is deferred to a future, monitor-aware design; the timeline `output_space` metadata field MAY still be carried for provenance.

#### Scenario: Viewport Display/View change is not broadcast
- **WHEN** the user changes the viewport Display or View in xStudio
- **THEN** the plugin SHALL NOT broadcast a `Timeline.metadata["color"]["output_space"]` change

#### Scenario: Received output space does not alter the local Display/View
- **WHEN** a peer's `output_space` change is received
- **THEN** xStudio SHALL leave its local ColourPipeline Display and View unchanged
