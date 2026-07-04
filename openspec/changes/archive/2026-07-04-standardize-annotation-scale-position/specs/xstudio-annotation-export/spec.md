## MODIFIED Requirements

### Requirement: Caption Conversion

The plugin SHALL convert xstudio captions to `SyncEvent.TextAnnotation` events. Because xStudio has no per-caption scale field, the emitted `scale` SHALL be `1.0`; on import, the `scale` field MAY be dropped since xStudio cannot represent it. Coordinate and font-size conversions SHALL use the shared helpers (`coords` for geometry, the xStudio codec for xStudio's font factor), not inline constants.

#### Scenario: Caption to TextAnnotation

- **WHEN** a bookmark's serialized data contains a `caption`
- **THEN** a `TextAnnotation` event SHALL be created with `text`, `position`, `font` (from `font_name`), `font_size`, `rgba` from caption colour+opacity, `scale=1.0`, `rotation=0.0`

#### Scenario: Scale round-trip on xStudio is lossless within its capability

- **WHEN** a `TextAnnotation` is imported into xStudio and later exported again
- **THEN** the re-exported `TextAnnotation.scale` SHALL be `1.0`
- **AND** no error SHALL result from xStudio lacking a native scale field
