## ADDED Requirements

### Requirement: OpenRV Applies Input Color Metadata to Its OCIO Pipeline
The OpenRV plugin SHALL read the color metadata from a synced timeline and apply a clip's resolved input colorspace to that source's OCIO input transform (via the OCIO source-setup / OCIONode path). Resolution SHALL follow the hierarchical rule defined by the color-pipeline-sync capability, with the timeline `working_space` used as the inherited fallback.

#### Scenario: Clip input colorspace sets the source transform
- **WHEN** OpenRV loads or receives a clip whose resolved input colorspace is `"ocio:ARRI LogC3 (EI800) - Wide Gamut"`
- **THEN** the corresponding RV source's OCIO input transform SHALL be set to that colorspace

#### Scenario: Unresolvable name leaves the source unmanaged and warns
- **WHEN** a clip's color string cannot be resolved against the active OCIO config
- **THEN** OpenRV SHALL leave that source's color handling at its default and SHALL emit a warning rather than failing the load

### Requirement: OpenRV Writes Input Color Changes Back to Metadata
When a user changes a source's input colorspace in OpenRV, the plugin SHALL write the change into `Clip.metadata["color_space"]` using the vocabulary-prefix convention, so the change is broadcast through the existing `SetProperty` path.

#### Scenario: User changes a source colorspace
- **WHEN** the user sets a source's input colorspace in OpenRV
- **THEN** the plugin SHALL update that clip's `metadata["color_space"]` with the prefixed name and broadcast it via `SetProperty`

### Requirement: OpenRV Does Not Live-Sync Output Space
The OpenRV plugin SHALL NOT broadcast its viewport display colorspace as the timeline `output_space`, and SHALL NOT apply a received `output_space` to its display pipeline. The viewport OCIO Display is device-centric (the local monitor), so propagating it would override each peer's display. Output-space synchronisation is deferred to a future, monitor-aware design; the timeline `output_space` metadata field MAY still be carried for provenance.

#### Scenario: Viewport display change is not broadcast
- **WHEN** the user changes the viewport display/view in OpenRV
- **THEN** the plugin SHALL NOT broadcast a `Timeline.metadata["color"]["output_space"]` change

#### Scenario: Received output space does not alter the local display
- **WHEN** a peer's `output_space` change is received
- **THEN** OpenRV SHALL leave its local display pipeline unchanged
