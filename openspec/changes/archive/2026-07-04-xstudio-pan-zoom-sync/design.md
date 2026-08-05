## Context

Currently, `xstudio_plugin/ori_sync/display_sync.py` implements a viewport synchronization controller (`DisplaySyncController`) which reads and writes exposure and channel settings. However, zoom (scale) is only synchronized one-way (from xStudio to OpenRV), and pan synchronization is disabled entirely. 
Ted Waine's patch is already compiled into the local build, providing `viewport_scale_atom`, `viewport_pan_atom`, and Python properties `vp.scale` and `vp.pan` (which accept/return `Vec2f` for pan).
Furthermore, fit modes in xStudio (such as `"Best"`, `"Width"`, `"Height"`) dynamically override viewport scale and translation coordinates during matrix calculation. When setting custom zoom and pan values, the viewport's fit mode must be set to `"Off"` (which maps to the unconstrained `FitMode::Free`) so the custom adjustments are not immediately discarded on the next redraw.

## Goals / Non-Goals

**Goals:**
- Implement two-way viewport pan and zoom synchronization for xStudio.
- Standardize viewport scale/zoom relative to a dynamically computed base scale to support local window resizing.
- Support normalized translation coordinates, translating from xStudio's width-normalized coordinate system (with Y-down) to OpenRV's height-normalized coordinate system (with Y-up).
- Correctly toggle fit mode to `"Off"` when applying remote pan/zoom updates.

**Non-Goals:**
- Supporting viewport sync for multi-viewport layouts (e.g. split viewports); we target only the active viewport.
- Syncing crop/mask parameters.

## Decisions

### 1. Viewport Coordinate Scaling via Aspect Ratio
- **Choice**: Scale xStudio translation values ($translate_{xs}$) by aspect ratio when broadcasting, and divide received values by aspect ratio when applying.
- **Rationale**: xStudio uses width-normalized coordinate ranges, whereas RV uses height-normalized ranges. Scaling by aspect ratio ($width/height$) bridges this difference. The Y-axis must also be negated due to opposing direction vectors (Y is down in xStudio, up in RV).
- **Alternatives Considered**: Sending raw values and performing scaling on the receiver side. Rejected because RV is the standard reference peer, and all other peers expect height-normalized coords on the RabbitMQ network.

### 2. Auto-disabling Fit Mode on Incoming Updates
- **Choice**: Set `"Fit (F)"` to `"Off"` when applying remote `pan` or `zoom`.
- **Rationale**: Active fit modes overwrite the scale and translate attributes during redraw, making custom assignments no-ops.

### 3. Dynamic Base Scale Calibration
- **Choice**: Calibrate `self._xs_base_scale` to the current `vp.scale` when the local viewport is in any active fit mode (i.e., `"Fit (F)"` is not `"Off"`).
- **Rationale**: This captures the fit-to-window scale. If the window is resized, the scale changes automatically. Updating the baseline ensures subsequent relative zoom computations remain correct.

## Risks / Trade-offs

- **[Risk]**: A remote peer with a different image aspect ratio causes viewport mismatches.
  - **Mitigation**: This is a general limitation of viewport sync in heterogeneous aspect ratio environments. Both OpenRV and xStudio will still fit the image relative to their viewport sizing.
- **[Risk]**: High frequency of updates flooding the network.
  - **Mitigation**: The plugin already limits scans to `0.5s` intervals (`DISPLAY_SCAN_INTERVAL`), which acts as an effective throttle.
- **[Risk]**: Python-thread timeouts if viewport actors go stale.
  - **Mitigation**: We will wrap all viewport reads and writes in the existing `bounded_timeout(self.plugin.connection, _DISPLAY_TIMEOUT_MS)` context manager.
