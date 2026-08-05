## Why

Currently, display state synchronization (pan and zoom) only works from xStudio to OpenRV, and remote pan/zoom changes from other peers are ignored in xStudio. This is because xStudio's Python API previously lacked setters for viewport pan and zoom, and the coordinate systems between the two applications are different. Enabling bi-directional pan/zoom synchronization is essential for a consistent, shared review session.

## What Changes

- Implement viewport scale (zoom) and pan conversion in xStudio to align with OpenRV's normalized, height-based coordinate system.
- Add support for applying received pan and zoom updates to the active xStudio viewport.
- Leverage the compiled `scale` and `pan` viewport attributes in the xStudio Python API.
- Automatically toggle fit-mode attribute `"Fit (F)"` to `"Off"` when applying remote pan/zoom adjustments to prevent them from being overridden by xStudio's auto-fitting system.
- Dynamically calibrate the base scale (`self._xs_base_scale`) when in fit-mode to handle local window resizes properly.

## Capabilities

### New Capabilities
- `xstudio-viewport-sync`: Provides bi-directional display scale (zoom) and translation (pan) synchronization for xStudio.

### Modified Capabilities

## Impact

- **Affected Components**: `DisplaySyncController` inside [display_sync.py](file:///Users/sam/git/ORIAnnotations/xstudio_plugin/ori_sync/display_sync.py).
- **APIs Used**: `xstudio.api.intrinsic.viewport.Viewport` attributes (`scale`, `pan`, and `"Fit (F)"`).
- **Dependencies**: No new external dependencies.
