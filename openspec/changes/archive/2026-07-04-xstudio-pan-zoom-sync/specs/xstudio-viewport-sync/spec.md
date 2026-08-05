## ADDED Requirements

### Requirement: Read Viewport display settings
The system SHALL read scale (zoom) and translation (pan) from the active xStudio viewport alongside exposure and channel settings.
- The scale SHALL be normalized relative to a fit-to-window base scale ($zoom = scale / base\_scale$) to match RV's convention.
- The translation SHALL be normalized from xStudio's width-normalized coordinate system to RV's height-normalized coordinate system using the viewport aspect ratio ($pan_{rv} = pan_{xs} \times aspect$, with Y-axis inverted).
- If the viewport fit mode is active (not `"Off"`), the reported zoom SHALL be `1.0`, the reported pan SHALL be `[0.0, 0.0]`, and the base fit-to-window scale SHALL be dynamically updated to the current viewport scale.
- If the viewport fit mode is `"Off"`, the reported zoom and pan SHALL be calculated using the active base scale and viewport aspect ratio.

#### Scenario: Read display state in fit mode
- **WHEN** the viewport fit mode is `"Best"` with a raw scale of `5.0` and translation `[0.0, 0.0]`
- **THEN** the read zoom is `1.0` and pan is `[0.0, 0.0]`, and the base scale is updated to `5.0`

#### Scenario: Read display state when zoomed and panned
- **WHEN** the viewport fit mode is `"Off"`, raw scale is `10.0`, raw translation is `[0.2, -0.1]`, viewport size is `1920x1080` (aspect `1.7778`), and the base scale is `5.0`
- **THEN** the read zoom is `2.0` and pan is `[0.3556, 0.1778]`

### Requirement: Apply Viewport display settings
The system SHALL apply received display settings (zoom, pan, exposure, and channel) to the local xStudio viewport.
- If the incoming display settings contain a non-null `zoom` or `pan` value, the system SHALL check if the viewport fit mode is active and, if so, set the `"Fit (F)"` viewport attribute to `"Off"`.
- If `zoom` is not null, it SHALL be converted back to raw scale by multiplying by the baseline scale ($scale = zoom \times base\_scale$) and written to the viewport scale.
- If `pan` is not null, it SHALL be converted back to raw translation by dividing by the viewport aspect ratio ($pan_{xs} = pan_{rv} / aspect$, with Y-axis inverted) and written to the viewport pan.
- Applied values SHALL be saved to the local cached display state to prevent echo-broadcasting.

#### Scenario: Apply display state containing pan and zoom
- **WHEN** display settings are received with zoom `1.5`, pan `[0.3556, -0.1778]`, viewport size is `1920x1080` (aspect `1.7778`), base scale is `5.0`, and fit mode is `"Best"`
- **THEN** the viewport fit mode is changed to `"Off"`, viewport scale is set to `7.5`, and viewport pan is set to `(0.2, 0.1)`
