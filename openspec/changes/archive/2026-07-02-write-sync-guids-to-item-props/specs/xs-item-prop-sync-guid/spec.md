## ADDED Requirements

### Requirement: Sync GUIDs Persisted Into xStudio Item Props

The xStudio plugin SHALL write each timeline track's and clip's ORI sync GUID
into that xStudio item's `item_prop` at build time, so that a subsequent native
`to_otio_string()` export carries the GUID as the item's
`metadata["sync"]["guid"]`. The plugin SHALL NOT rely on
`clip.media_reference.target_url` for clip identity, because xStudio exports
`MissingReference` (empty `target_url`) for its internal `xstudio://` media.

#### Scenario: Track and clip GUIDs written after assignment

- **WHEN** the plugin builds an OTIO timeline from an xStudio sequence and
  assigns deterministic sync GUIDs to its tracks and clips
- **THEN** for each track and clip it writes `{"sync": {"guid": <guid>}}` into
  the corresponding xStudio timeline item's `item_prop`, matching items to the
  parsed OTIO by structural position

#### Scenario: Exported OTIO carries the sync GUID

- **WHEN** the plugin later calls `xs_tl.to_otio_string()` on a timeline whose
  item props were written
- **THEN** each exported clip's `metadata["sync"]["guid"]` equals the GUID the
  plugin assigned to that clip

#### Scenario: Existing item props are preserved

- **WHEN** the plugin writes the sync GUID into an item that already has other
  `item_prop` entries
- **THEN** it read-merge-writes so the sync GUID is added under the `sync` key
  without discarding the item's other props

#### Scenario: A stale-actor prop access does not block the build

- **WHEN** reading or writing an item's `item_prop` blocks (e.g. a stale
  actor)
- **THEN** the call is bounded, the failure is logged, that item's write is
  skipped, and the build continues for the remaining items
