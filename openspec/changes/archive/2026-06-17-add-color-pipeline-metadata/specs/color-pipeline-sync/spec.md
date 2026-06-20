## ADDED Requirements

### Requirement: Timeline Color Metadata Group
The system SHALL carry timeline-level color configuration in `Timeline.metadata["color"]`, a group whose keys mirror the OTIO Color Pipeline Model RFC verbatim: `config`, `working_space`, and `output_space`. The group and each of its keys SHALL be optional; an absent `color` group means the timeline is unmanaged. Key names and value semantics SHALL match the future native `Timeline.color` fields so promotion is a key move rather than a reshape.

#### Scenario: Managed timeline carries a color group
- **WHEN** a timeline authored against an OCIO config is serialized
- **THEN** `Timeline.metadata["color"]` SHALL contain `config`, `working_space`, and/or `output_space` string values

#### Scenario: Unmanaged timeline omits the color group
- **WHEN** a timeline with no color management is serialized
- **THEN** `Timeline.metadata["color"]` SHALL be absent
- **AND** consumers SHALL treat the timeline as unmanaged rather than injecting a default config

### Requirement: Clip Color Space Metadata
The system SHALL carry a clip's input colorspace in `Clip.metadata["color_space"]` as a single vocabulary-prefixed string. The key SHALL be optional; an absent or null value means "inherit." This is the element's input space — what the media is treated as when converting to the working space. Media-reference-level color and provenance records are explicitly out of scope for this capability.

#### Scenario: Clip declares its input colorspace
- **WHEN** a clip's input colorspace is known
- **THEN** `Clip.metadata["color_space"]` SHALL hold a non-empty prefixed string such as `"ocio:ACEScg"`

#### Scenario: Clip omits color space to inherit
- **WHEN** a clip has no explicit input colorspace
- **THEN** `Clip.metadata["color_space"]` SHALL be absent or null
- **AND** its effective space SHALL be resolved by inheritance

### Requirement: Vocabulary-Prefix Convention
Every color string field (`working_space`, `output_space`, `color_space`) SHALL follow the prefix convention where the text before the first `:` is the vocabulary tag (ASCII `[a-z0-9_]`) and the remainder is the name. The system SHALL honor `ocio:` and `interop:` names and SHALL preserve any other prefix (including `cicp:`, `resolve:`, `aces:`, `custom:`) verbatim on round-trip without interpretation. The system SHALL NOT translate names between vocabularies.

#### Scenario: Known vocabulary is honored
- **WHEN** a field holds `"ocio:ARRI LogC3 (EI800) - Wide Gamut"` or `"interop:ACEScg"`
- **THEN** a host adapter SHALL resolve it against its OCIO config

#### Scenario: Unknown vocabulary is preserved verbatim
- **WHEN** a field holds `"resolve:DaVinci Wide Gamut Intermediate"` and the host cannot resolve it
- **THEN** the value SHALL round-trip through receive and re-broadcast byte-for-byte
- **AND** no cross-vocabulary translation SHALL be performed by the protocol

#### Scenario: Name containing a colon is disambiguated by prefix
- **WHEN** a name legitimately contains a colon (e.g. `"ocio:Utility - Curve - sRGB"`)
- **THEN** only the text before the first colon SHALL be treated as the vocabulary tag

### Requirement: Hierarchical Color Space Resolution
The system SHALL resolve a clip's effective input colorspace by the order: the clip's own `color_space` if set; otherwise `Timeline.metadata["color"]["working_space"]`; otherwise the host default. Resolution SHALL NOT consult any deferred media-reference field or provenance data.

#### Scenario: Clip override wins
- **WHEN** a clip has a `color_space` and the timeline has a `working_space`
- **THEN** the clip's `color_space` SHALL be the effective input space

#### Scenario: Falls back to timeline working space
- **WHEN** a clip has no `color_space` but the timeline has a `working_space`
- **THEN** the timeline `working_space` SHALL be the effective input space

#### Scenario: Falls back to host default
- **WHEN** neither the clip nor the timeline declares a space
- **THEN** the host's default SHALL be used and the host MAY warn

### Requirement: Live Color Synchronisation Over Existing Messages
The system SHALL synchronise color changes using the existing `SetProperty` message with a `metadata/...` path; it SHALL NOT introduce a new protocol message for color. A change to the timeline color group SHALL target the timeline's sync GUID with path `metadata/color`; a change to a clip's input colorspace SHALL target the clip's sync GUID with path `metadata/color_space`. Color SHALL also be carried at load time inside the existing OTIO-bearing messages (`AddTimeline`, `StateSnapshot`, `InsertChild`).

#### Scenario: Clip color change broadcasts via SetProperty
- **WHEN** the master changes a clip's input colorspace mid-session
- **THEN** a `SetProperty` SHALL be broadcast with `target_uuid` = the clip's sync GUID and `path` = `metadata/color_space`

#### Scenario: Timeline color change broadcasts via SetProperty
- **WHEN** the master changes the timeline working or output space
- **THEN** a `SetProperty` SHALL be broadcast targeting the timeline GUID with `path` under `metadata/color`

#### Scenario: Color rides timeline load
- **WHEN** a timeline is added or sent in a state snapshot
- **THEN** its color metadata SHALL be present in the OTIO payload without any additional message

### Requirement: Shared Configuration Assumption and Peer Re-resolution
The system SHALL assume all peers share the same OCIO config. On receiving a color change, each peer SHALL re-resolve the colorspace name against its own config rather than transmitting resolved transforms. The protocol SHALL transmit only names and config identifiers, never resolved color transforms.

#### Scenario: Peer applies a received color change against its own config
- **WHEN** a peer receives a `SetProperty` setting `metadata/color_space`
- **THEN** the peer SHALL resolve the name against its own OCIO config and apply the result locally

#### Scenario: No transforms on the wire
- **WHEN** any color change is broadcast
- **THEN** the payload SHALL contain colorspace names and/or a config identifier only, and SHALL NOT contain a resolved LUT or transform
