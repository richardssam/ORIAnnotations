## 1. Implement Viewport State Reading

- [x] 1.1 Read size, scale, and translate from `serialise_atom()` in `read_xs_display_state()` and compute viewport aspect ratio
- [x] 1.2 Query `"Fit (F)"` attribute value to determine if a fit mode is active
- [x] 1.3 Calibrate and update the baseline scale (`self._xs_base_scale`) dynamically when fit mode is active
- [x] 1.4 Normalize and populate the zoom and pan values in `read_xs_display_state()` (zoom = scale / baseline, pan = translate * aspect with Y negated) when fit mode is Off

## 2. Implement Viewport State Applying

- [x] 2.1 Retrieve the current viewport size and aspect ratio in `apply_display_state()`
- [x] 2.2 Toggle `"Fit (F)"` to `"Off"` before setting custom scale or pan if a fit mode is currently active
- [x] 2.3 Convert incoming zoom and pan back to xStudio coordinates (scale = zoom * baseline, translate = pan / aspect with Y negated) and write them to the viewport properties
- [x] 2.4 Re-read and cache the viewport state in `_last_display_state` to prevent broadcast echo loops

## 3. Testing and Verification

- [x] 3.1 Manually verify pan/zoom display synchronization with a running peer or `sync_viewer`
- [x] 3.2 Run the test suite to ensure no display or viewport sync regressions
