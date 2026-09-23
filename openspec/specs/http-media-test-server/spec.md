# http-media-test-server Specification

## Purpose

Provides a small standalone HTTP server for test scenarios that serves
media files with the byte-range support real media readers require, so
fallback and remote-media test scenarios don't silently fail against a
server that can't be seeked.

## Requirements

### Requirement: Server supports HTTP range requests
The server SHALL respond to a `Range: bytes=<start>-<end>` request header
with a `206 Partial Content` response containing only the requested byte
range and a correct `Content-Range` header. The server SHALL advertise
`Accept-Ranges: bytes` on all responses for servable files.

#### Scenario: Ranged request returns partial content
- **WHEN** a client requests a file with `Range: bytes=100-199`
- **THEN** the server responds `206 Partial Content` with
  `Content-Range: bytes 100-199/<total-size>` and a 100-byte body

#### Scenario: Unranged request returns full content
- **WHEN** a client requests a file with no `Range` header
- **THEN** the server responds `200 OK` with the full file body and an
  `Accept-Ranges: bytes` header

### Requirement: Server serves a configurable media directory on a configurable port
The server SHALL be startable with a target directory and a TCP port as
parameters, and SHALL serve files from that directory by filename at the
server's root path, so it can be pointed at the same `test_media/` tree
already used by `sync_test` without duplicating media files.

#### Scenario: Serves canonical test media by filename
- **WHEN** the server is started against `test_media/source/encoded/`
  on a given port
- **THEN** a GET request for `/car_ACES_sRGB.mov` returns that file's
  contents

### Requirement: Server rejects requests for files outside its served directory
The server SHALL NOT serve any path that resolves outside the configured
media directory.

#### Scenario: Path traversal attempt is rejected
- **WHEN** a client requests a path containing `../` that would resolve
  outside the configured media directory
- **THEN** the server responds with an error status and does not return
  file contents from outside that directory
