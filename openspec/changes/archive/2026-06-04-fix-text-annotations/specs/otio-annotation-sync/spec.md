## ADDED Requirements

### Requirement: TextAnnotation Font Sizing Symmetry

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. Specifically, if the text size is scaled by a factor of 5000.0 upon export, it MUST be unscaled by a factor of 5000.0 upon import.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

### Requirement: TextAnnotation UUID Persistence

When converting `SyncEvent.TextAnnotation` commands to a client-native format (e.g., xStudio caption dictionaries), the unique identifier (`uuid`) MUST be explicitly carried over into the native structure. This guarantees that subsequent modification broadcasts can correctly merge against the original node.

#### Scenario: Replacing an existing caption

- **WHEN** a client receives a `broadcast_replace_annotation_commands` payload containing edited text
- **THEN** it SHALL use the text node's `uuid` to find and update the existing native caption in-place, rather than appending a duplicate copy.
