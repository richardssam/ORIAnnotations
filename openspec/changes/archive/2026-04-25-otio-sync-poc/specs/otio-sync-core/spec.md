## ADDED Requirements

### Requirement: Unique Object Identification
The system SHALL ensure that every OTIO object managed by the sync manager has a unique identifier to facilitate reliable targeting of delta patches.

#### Scenario: Object without GUID is registered
- **WHEN** the sync manager ingests an OTIO object that lacks `metadata["sync"]["guid"]`
- **THEN** it generates a UUID and assigns it to that property.

### Requirement: Property Mutation Delta Generation
The system SHALL generate an `otio-delta` payload when an object's property is explicitly modified via the SyncManager.

#### Scenario: Setting an object's name
- **WHEN** `SyncManager.set_property(target_uuid, path, value)` is called
- **THEN** the local OTIO object's property is updated
- **AND THEN** a JSON payload with `action: set_property` is emitted.

### Requirement: Silent Patch Application
The system SHALL apply incoming network patches to the local OTIO graph without triggering subsequent outgoing delta payloads (preventing echo loops).

#### Scenario: Applying an incoming patch
- **WHEN** `SyncManager.apply_patch(payload)` is called
- **THEN** the local OTIO object is updated based on the payload action
- **AND THEN** no outgoing `otio-delta` payload is emitted for this mutation.
