## ADDED Requirements

### Requirement: Authoritative Empty Replace Semantics

A `REPLACE_ANNOTATION_COMMANDS` message whose command list is completely empty for a given annotation clip SHALL be treated by all receivers as an authoritative statement that the clip now has zero annotations, distinct from a non-empty replace that merely omits a kind (e.g. a text-only edit that says nothing about pen strokes, which SHALL continue to leave that kind untouched).

#### Scenario: Empty command list clears the clip everywhere

- **WHEN** the Master peer broadcasts `REPLACE_ANNOTATION_COMMANDS` for an annotation clip with an empty `annotation_commands` list
- **THEN** every receiving peer SHALL end up with zero rendered annotations for that clip's frame
- **AND** the local OTIO state tree's copy of that clip's `annotation_commands` SHALL also become empty

#### Scenario: Non-empty partial replace does not imply other kinds are empty

- **WHEN** a `REPLACE_ANNOTATION_COMMANDS` message contains only text or only shape commands for a clip that also has pen strokes
- **THEN** receivers SHALL NOT interpret the absence of pen-stroke commands in that message as "delete all pen strokes"
