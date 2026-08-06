## Why

Pan/zoom sync between RV and xStudio has gone through several rounds of trial-and-error fixes (wrong axis direction, a spurious aspect factor, a flat-but-unexplained `×2` scale) and still has a small, pan-distance-proportional drift. The root cause is that the sync protocol's `pan`/`zoom` values have never had an explicit, documented coordinate-space definition — each host's adapter encodes its own guess about what the values mean, and those guesses have been reverse-engineered from source code and live testing rather than derived from a stated convention.

A team reference sheet (coordinate systems used by xStudio, OpenRV/OTIO, OFX, Nuke, Resolve, Avid, Premiere, FCPXML) already documents the native conventions of the tools we integrate with, and independently confirms things we only found empirically this session (xStudio: centre-origin, Y-down, **width**-normalized, ±1.0; OpenRV/OTIO: centre-origin, Y-up, **height**-normalized, ±1.0). It also surfaces a concrete discrepancy: this repo's own `otio_sync_core/coords.py` (the existing host-neutral annotation coordinate module) normalizes to `±0.5`, not the `±1.0` the sheet documents for "OpenRV/OTIO". We should resolve that before extending host-neutral coordinates to cover viewport pan/zoom too, rather than build a second neutral space that also drifts from either the sheet or the code.

## What Changes

- Formally define a single **neutral viewport coordinate space** for the sync protocol's `pan`/`zoom` fields — explicit origin, axis directions, normalization basis, and bounds — referencing the team's coordinate-systems sheet and OFX's canonical coordinates (`https://openfx.readthedocs.io/en/latest/Reference/ofxCoordSystem.html#canonicalcoordinates`) as prior art, not inventing conventions ad hoc.
- Investigate and resolve the `otio_sync_core/coords.py` `±0.5` vs. the sheet's documented `±1.0` discrepancy for the OpenRV/OTIO convention. Depending on findings, this may be **BREAKING** for annotation sync if `coords.py`'s normalization constants change.
- Document xStudio's native viewport-pan convention explicitly (centre-origin, Y-down, width-normalized, ±1.0) and RV's native viewport-pan convention explicitly (currently only assumed to equal the protocol space via zero conversion in `rvplugin/ori_sync/display_sync.py` — this has never actually been verified against RV's own coordinate documentation/behavior).
- Rewrite `xstudio_plugin/ori_sync/display_sync.py`'s pan read/write conversion to derive from the documented neutral space (principled aspect-ratio conversion) instead of the current empirically-tuned `_XS_PAN_UNITS_PER_PROTOCOL_UNIT` constant. **BREAKING** change to the pan sync conversion formula.
- Add an empirical calibration test to `sync_test`: a script-driven action to set a known raw pan/zoom value directly on one app's viewport (bypassing mouse drag and the live sync session), capture a frame, and measure the actual pixel shift of a known test-chart feature — used to validate the derived conversion against real rendered output for both hosts, and to catch future regressions.

## Capabilities

### New Capabilities
- `neutral-viewport-coordinate-space`: the documented definition of the sync protocol's pan/zoom coordinate space (origin, axes, normalization, bounds) and each host's (RV, xStudio) explicit conversion to/from it, with justification tracing back to the team's coordinate-systems reference and OFX's canonical coordinates.
- `viewport-pan-zoom-calibration-test`: the `sync_test` harness addition (direct pan/zoom-setting action + frame capture + pixel-shift measurement) that empirically validates a host's pan/zoom conversion against its actual rendered output.

### Modified Capabilities
- `xstudio-viewport-sync`: the pan read/write conversion requirements change from the current ad hoc aspect/scale handling to the formula derived from `neutral-viewport-coordinate-space`.
- `annotation-coord-transform`: pending the `coords.py` ±0.5-vs-±1.0 investigation — only if that investigation concludes the implemented range should change. (To be confirmed in design; not committed here.)

## Impact

- `xstudio_plugin/ori_sync/display_sync.py` — pan conversion rewritten.
- `rvplugin/ori_sync/display_sync.py` — currently zero-conversion pass-through; needs its assumption verified/documented against the new neutral space definition, and updated if it doesn't actually hold.
- `python/otio_sync_core/coords.py` — possible normalization-constant change (pending investigation), which would ripple into `rv_annotation_codec.py` and `xs_annotation_codec.py`, both of which already depend on it.
- `sync_test/python/sync_test/openrv_hook.py`, `sync_test/python/sync_test/xstudio_hook.py`, `sync_test/python/sync_test/runner.py` — new script-driven command(s) for direct pan/zoom setting, and a new pixel-shift measurement utility (no existing generic image-alignment utility in the repo; closest prior art is `sync_test/python/sync_test/visual_geometry.py`'s ground-truth-driven centroid technique).
- `tests/otio_sync/test_display_coords.py` — existing unit tests will need to be re-derived against the new formula (again).
- Documentation: `docs/rv_vs_xstudio_api.md` (currently stale on pan/zoom — still describes write-side as "blocked") and the OpenSpec spec files above.
