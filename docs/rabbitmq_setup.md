---
layout: default
title: How to configure rabbitmq
parent: ORI Sync Tools
nav_order: 3.9
---

# RabbitMQ Setup and Security Guide

This document outlines the setup, configuration, and security practices for running RabbitMQ as the backend messaging broker for the ORIAnnotations sync plugins.

---

## 1. Production Deployment (OCI Always Free)

For a production or shared environment, stand up a secure, public-facing RabbitMQ broker on Oracle Cloud Infrastructure (OCI) Always Free tier.

Key constraints for this setup:

* **Protocol**: **AMQPS (AMQP over TLS/SSL)** is exposed publicly on port **5671**. Plaintext AMQP (port 5672) is bound to localhost only.
* **Credentials**: The default `guest` user is disabled. Access requires configuring a strong custom administrative username and password.
* **Firewall Ingress**: Access to port 5671 (AMQPS) and port 443 (HTTPS Management UI) is restricted by IP address range (CIDR) in the OCI Security List.
* **Reverse Proxy**: Caddy handles Let's Encrypt certificate issuance and reverse-proxies the HTTP management UI.

A step-by-step build plan for the OCI VM is detailed in:

* **[OCI RabbitMQ Runbook](oci-rabbitmq-runbook.md)**

---

## 2. Local Secure Setup (macOS / Homebrew)

If you need to test the secure client-connection path locally, you can enable TLS/SSL (AMQPS) on your local Homebrew RabbitMQ server.

### A. Generate Self-Signed Certificates

Run the following commands to create a local Certificate Authority (CA) and a server certificate for `localhost`:

```bash
# Create certs folder
mkdir -p /opt/homebrew/etc/rabbitmq/certs
cd /opt/homebrew/etc/rabbitmq/certs

# 1. Generate local CA private key and certificate
openssl req -new -x509 -nodes -keyout ca.key -out ca.crt -days 3650 -subj "/CN=Local-RabbitMQ-CA"

# 2. Generate Server private key and CSR
openssl req -new -nodes -keyout server.key -out server.csr -subj "/CN=localhost"

# 3. Sign the Server certificate using the local CA
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 365
```

### B. Configure RabbitMQ to Listen on Port 5671

Create `/opt/homebrew/etc/rabbitmq/rabbitmq.conf` and populate it with the following configuration:

```ini
# Maintain plaintext AMQP on loopback for local CLI tools
listeners.tcp.default = 127.0.0.1:5672

# Enable secure AMQPS listener on port 5671
listeners.ssl.default = 5671

# Configure SSL/TLS paths
ssl_options.cacertfile = /opt/homebrew/etc/rabbitmq/certs/ca.crt
ssl_options.certfile   = /opt/homebrew/etc/rabbitmq/certs/server.crt
ssl_options.keyfile    = /opt/homebrew/etc/rabbitmq/certs/server.key

# Do not force client-side certificates
ssl_options.verify     = verify_none
ssl_options.fail_if_no_peer_cert = false
```

### C. Restart the Service

Restart the broker via Homebrew:

```bash
brew services restart rabbitmq
```

### D. Trust the Local CA (Required for Python Clients)

Because Python/Pika verifies the server certificate against the system trust store, self-signed certificates will cause connection errors. On macOS, trust the local CA root by adding it to your system keychain:

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /opt/homebrew/etc/rabbitmq/certs/ca.crt
```

---

## 3. Configuring Application Connections

With the client connection improvements in place, both plugins (OpenRV and xStudio) and tools support passing full AMQP/AMQPS URLs as the host.

### Connection URL Format

`amqps://<username>:<password>@<host>:<port>/<virtual_host>`

Examples:

* **Local Secure Connection**: `amqps://guest:guest@localhost:5671/`
* **Production Secure Connection**: `amqps://admin:securepass@rabbit.yourdomain.com:5671/`
* **Plaintext Fallback**: `localhost` (connects to local port 5672 without credentials)

### Environment Variable (`ORI_SESSION`)

If utilizing the `ORI_SESSION` environment variable to auto-join a review session, format the string as `[connection_url:]session_name`.

* **To connect securely (AMQPS)**:
  `export ORI_SESSION="amqps://guest:guest@localhost:5671/:samtest"`
* **To connect via plaintext fallback**:
  `export ORI_SESSION="localhost:samtest"`

#### Testing Self-Signed Certificates

If you are testing a remote secure server using self-signed certificates (where your hostname is not `localhost` or `127.0.0.1`), you can bypass certificate validation by adding `verify=ignore` or `ssl_verify=false` query parameters to your URL:
`export ORI_SESSION="amqps://guest:guest@test-server-domain:5671/?verify=ignore:samtest"`
*(Note: Verification is automatically bypassed for `localhost` and `127.0.0.1` addresses).*
