## MODIFIED Requirements

### Requirement: TextAnnotation Font Sizing Symmetry

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. For the RV host, the factor `RV_FONT_SCALE` (`1080.0` — see "RV Font Size Reads The Authoritative On-Screen Property" below) SHALL be defined in `rv_annotation_codec` (not in the shared `coords` module), and if the text size is scaled by that factor upon export it MUST be unscaled by the same factor upon import.

This requirement covers only a single host's own round-trip self-consistency (RV export then re-import, or xStudio export then re-import, yields the same `font_size`). It does NOT by itself guarantee that RV and xStudio agree with each other on apparent on-screen size for the same `font_size` — see "Cross-Host TextAnnotation Font Size Parity" below.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

## ADDED Requirements

### Requirement: RV Font Size Reads The Authoritative On-Screen Property

RV's text paint-node carries two size-like properties: `fontSize` (a WCS fraction of image height — the property that actually governs on-screen rendering in the current QPainter-based text renderer) and a legacy `size` (retained only for session-file compatibility with builds predating the QPainter migration; no longer read by any current rendering code). The OTIO sync codec's RV-side conversion (`rv_paint_applier.read_stroke`, `rv_annotation_codec.rv_to_font_size`/`font_size_to_rv`) SHALL read/write `fontSize` as the authoritative value, and SHALL reconstruct the same value via a fallback (`size * 100 * 100 / 1080 * scale`) for sessions/broadcasts that predate `fontSize` — mirroring the fallback the C++ renderer itself uses, so the synced value always matches what is actually on screen regardless of session age.

Using the legacy `size` property directly (without this reconstruction) for any current session/broadcast is a defect: it silently converts a value the renderer itself no longer uses for display, producing a synced size disconnected from what the sending host actually shows.

#### Scenario: Reading a current (fontSize-bearing) text annotation

- **WHEN** an RV paint-node text component has a `fontSize` property set
- **THEN** the sync codec SHALL use that value directly (divided by `scale` to keep the nominal size scale-exclusive) as the basis for the exported `TextAnnotation.font_size`

#### Scenario: Reading a legacy (pre-fontSize) text annotation

- **WHEN** an RV paint-node text component has no `fontSize` property, only a legacy `size`
- **THEN** the sync codec SHALL reconstruct the WCS-fraction value as `size * 100 * 100 / 1080 * scale` before converting to `TextAnnotation.font_size`, rather than treating raw `size` as if it were already a WCS fraction

#### Scenario: Writing a text annotation preserves both properties

- **WHEN** a `TextAnnotation` event is applied to a new RV paint node
- **THEN** the codec SHALL write both the authoritative `fontSize` (the value actually rendered) and a legacy `size` chosen so that an older RV build's own fallback formula would reconstruct the same `fontSize`

### Requirement: Cross-Host TextAnnotation Font Size Parity

The RV and xStudio font-size unit conversions (`RV_FONT_SCALE` in `rv_annotation_codec`, `XS_FONT_SCALE` in `xs_annotation_codec`) SHALL be calibrated so that a `TextAnnotation` of a given `font_size` renders at approximately the same apparent on-screen glyph height on both hosts — not merely round-trip symmetrically within a single host (see "TextAnnotation Font Sizing Symmetry" above, which does not by itself guarantee this), and not merely self-consistent between a test's expected-value formula and the production code if that formula and the code share the same underlying defect (see "RV Font Size Reads The Authoritative On-Screen Property" above — the actual historical failure was exactly this: a numeric round-trip check that derives its expected value from the same wrong property/formula the production code also uses will pass without detecting anything).

Both hosts' native units are anchored to the same reference frame: RV's `fontSize` is a fraction of image height that `RV_FONT_SCALE = 1080` maps to reference-frame pixels; xStudio's own `font_size` is already pixels at a 1920-wide (1080-tall, for 16:9) reference frame. With both sides anchored the same way, `XS_FONT_SCALE = 1.0` — no additional per-host fudge factor is needed or should be introduced without being validated by actually rendering and measuring both hosts' output, not by tuning either constant in isolation.

#### Scenario: OpenRV-drawn text renders at a comparable size in xStudio

- **WHEN** OpenRV draws a native text annotation at a given nominal `fontSize` and it converges to an xStudio peer
- **THEN** the apparent on-screen glyph height xStudio renders for the received `TextAnnotation` SHALL be approximately equal to the glyph height OpenRV itself renders for the same nominal size
- **AND** this SHALL be verified numerically by `sync_test`'s round-trip check (composing OpenRV's reverse text codec, including its `fontSize`/legacy fallback, with xStudio's forward text codec against the real production constants)
