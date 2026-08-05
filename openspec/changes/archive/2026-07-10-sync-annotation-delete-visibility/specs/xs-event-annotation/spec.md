## ADDED Requirements

### Requirement: AnnotationsUI event type discrimination

The xStudio plugin's `on_annotation_event` handler SHALL inspect the `data["event"]` string of each `(event_atom, annotation_atom, JsonStore)` payload received from the `AnnotationsUI` plugin, rather than treating every such payload identically as a generic "schedule a bookmark scan" trigger. `PaintClear`, `HideDrawings`, and `ShowDrawings` SHALL each be handled according to their own requirements (deletion detection, visibility broadcast) in addition to — or instead of — the existing generic scan scheduling.

#### Scenario: PaintClear still schedules the debounced scan

- **WHEN** `on_annotation_event` receives a payload with `data["event"] == "PaintClear"`
- **THEN** the plugin SHALL schedule the existing debounced flush scan, as it does today for any annotation event

#### Scenario: HideDrawings/ShowDrawings broadcast visibility instead of scanning bookmarks

- **WHEN** `on_annotation_event` receives a payload with `data["event"]` equal to `"HideDrawings"` or `"ShowDrawings"`
- **THEN** the plugin SHALL broadcast the corresponding `annotations_visible` boolean via `display_settings`
- **AND** SHALL NOT schedule a bookmark scan for this event

#### Scenario: Unrecognised events keep today's behavior

- **WHEN** `on_annotation_event` receives a payload whose `data["event"]` is not one of the recognised values (e.g. a tool-switch or display-mode change)
- **THEN** the plugin SHALL continue to schedule the generic debounced flush scan, unchanged from current behavior
