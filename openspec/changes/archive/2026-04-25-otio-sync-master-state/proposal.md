# Proposal: Master-Client State Transfer

## Goal
Implement a robust state synchronization mechanism for late-joining peers in an OTIO sync session.

## Why
Currently, new clients join with a blank state. They miss all previous timeline mutations, annotations, and playback state. To enable collaborative review, any peer joining an active session must be automatically brought up to speed.

## What Changes
- **Master Election**: Implement logic to designate the eldest peer as the "Master" (source of truth).
- **Handshake Protocol**: Add `WHO_IS_MASTER` and `STATE_REQUEST` events.
- **State Snapshot**: Master serializes the entire OTIO timeline and session state (playback/selection) into a single JSON payload.
- **Client Buffering**: Joining clients queue incoming deltas during the transfer process to ensure no data is lost between the snapshot and the "live" stream.
