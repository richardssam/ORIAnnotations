# session-config-file Specification

## Purpose
Lets a site pre-define its RabbitMQ server by dropping a config file into the plugin installation, rather than requiring every user's environment to carry an environment variable set before launch — the same practical-reachability gap `session-role-config` already closed for role policy.
## Requirements
### Requirement: A plugin-folder config file pre-defines the RabbitMQ host

The plugin SHALL support an optional config file, placed in its installation folder, that pre-defines the RabbitMQ host (and port) used as the default connection target and to pre-fill the connect dialog.

#### Scenario: A config file sets the default host

- **WHEN** a config file defining the RabbitMQ host is present in the plugin folder, and no environment variable or user input overrides it
- **THEN** the connect dialog SHALL pre-fill the host field with the config file's value
- **AND** that value SHALL be used as the connection target if the user does not change it

### Requirement: Explicit overrides take precedence over the config file

An environment variable already understood to set the RabbitMQ host SHALL continue to take precedence over the config file's value, and a value the user types into the dialog SHALL take precedence over both.

#### Scenario: Environment variable overrides the config file

- **WHEN** both a plugin-folder config file and the host environment variable are present, with different values
- **THEN** the environment variable's value SHALL be used

#### Scenario: A typed value overrides both

- **WHEN** a user types a host into the connect dialog
- **THEN** the typed value SHALL be used regardless of the config file or environment variable

### Requirement: A missing or malformed config file does not block connecting

The absence of a config file, or a config file that cannot be parsed, SHALL NOT prevent a user from creating or joining a session — the plugin SHALL fall back to the existing default (environment variable, else the hardcoded default) without surfacing an error to the user for this alone.

#### Scenario: No config file is present

- **WHEN** no config file exists in the plugin folder
- **THEN** the plugin behaves exactly as it does today, using the environment variable or hardcoded default

#### Scenario: The config file is malformed

- **WHEN** a config file is present but cannot be parsed
- **THEN** the plugin SHALL fall back to the environment variable or hardcoded default
- **AND** SHALL NOT fail to open the connect dialog because of it

### Requirement: The config file is read independently by each plugin installation

Because the RV and xStudio plugins are separate installations, a config file dropped into one plugin's folder SHALL configure only that installation.

#### Scenario: Configuring one host application does not affect the other

- **WHEN** a config file is placed in the xStudio plugin folder only
- **THEN** the RV plugin's default host SHALL be unaffected

