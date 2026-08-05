## MODIFIED Requirements

### Requirement: Single RV Annotation Codec

The system SHALL provide a single module `otio_sync_core.rv_annotation_codec` that is the sole authoritative implementation of the OTIO `SyncEvent` ⇄ RV paint-node mapping. All RV code that renders SyncEvents to paint nodes, or reads paint nodes back to SyncEvents, SHALL route through this module and SHALL NOT set or read RV paint-node properties for annotations directly.

#### Scenario: All RV call sites use the codec

- **WHEN** the testchart batch helper, the OTIO load plugin (import and export), or the live-sync renderer renders or parses annotations
- **THEN** each SHALL call the codec's conversion functions
- **AND** no annotation paint-node property SHALL be constructed inline at those call sites

#### Scenario: RV units owned by the codec

- **WHEN** a value is an RV-specific unit conversion (`RV_FONT_SCALE = 1080.0`, `RV_WIDTH_SCALE = 0.6`, `font_size_to_rv`, `rv_to_font_size`)
- **THEN** it SHALL be defined in `rv_annotation_codec`, mirroring how the xStudio codec owns xStudio's font factor

#### Scenario: RV owns two size-like text properties, only one authoritative

- **WHEN** the codec reads or writes a text paint-node's size
- **THEN** it SHALL treat `fontSize` (a WCS fraction of image height) as authoritative — the property that actually governs on-screen rendering in the current QPainter-based text renderer — and SHALL treat the legacy `size`/`ptsize` convention as reconstructible fallback-only, per the same fallback formula (`size * 100 * 100 / 1080 * scale`) the RV renderer's own C++ fallback uses for sessions predating `fontSize`
- **AND** SHALL NOT convert `size` directly via `RV_FONT_SCALE` as if it were already a WCS fraction, since that silently produces a value disconnected from what the sending host actually renders
