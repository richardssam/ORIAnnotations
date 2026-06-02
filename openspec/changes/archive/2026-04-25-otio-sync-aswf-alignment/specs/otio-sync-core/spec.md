# OTIO Sync Core Specification (Delta)

## MODIFIED Requirements

### Requirement: RabbitMQ Messaging
The core SHALL support RabbitMQ fanout exchanges.

#### Scenario: Declare exchange
- **WHEN** network starts
- **THEN** exchange is declared.

### Requirement: ASWF Command Routing
The core SHALL implement standard command/event routing logic.

#### Scenario: Route message
- **WHEN** message arrives
- **THEN** it is routed to the correct handler.
