## Purpose

Stops a join from silently succeeding against a session name that doesn't exist, so a typo or a session that hasn't started yet is reported instead of leaving the user in an empty, unconfirmed session.

## ADDED Requirements

### Requirement: Join validates that the named session exists before connecting

When a user attempts to join a session by name, the plugin SHALL check whether a session with that name currently exists before completing the join.

#### Scenario: Joining a nonexistent session is rejected

- **WHEN** a user attempts to join a session name that no peer has created
- **THEN** the join SHALL NOT complete
- **AND** the plugin SHALL show an error naming the session that could not be found

#### Scenario: Joining an existing session is unaffected

- **WHEN** a user attempts to join a session name that currently exists
- **THEN** the join SHALL proceed as it does today

### Requirement: The existence check is side-effect free

Checking whether a session exists SHALL NOT itself bring that session into existence or otherwise alter server-side state.

#### Scenario: Checking a nonexistent session leaves no trace

- **WHEN** the existence check runs against a session name that does not exist
- **THEN** no session, exchange, or queue is created as a result of the check

### Requirement: An unreachable server is reported distinctly from a missing session

A failure to reach the RabbitMQ server SHALL be reported as a connection failure, not as "session not found" — the two are different problems with different fixes.

#### Scenario: The MQ server is unreachable

- **WHEN** a user attempts to join or create a session and the RabbitMQ server cannot be reached
- **THEN** the plugin SHALL show a connection error
- **AND** SHALL NOT report that the session was not found
