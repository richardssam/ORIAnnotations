---
layout: default
title: ORI Sync Recorder
parent: ORI Sync Tools
---
# Sync Session Recorder & Player

A utility package for recording and playing back network events broadcast in an OTIO sync session. This allows for testing, debugging, and simulating active review sessions.

## Features

- **Session Recording**: Capture all messages sent over a session's RabbitMQ exchange (or UDP broadcast).
- **Session Playback**: Replay recorded events with accurate relative delays.
- **Timestamp Updating**: Automatically updates all payload timestamp fields (e.g. `sync_timestamp`) to the current system time during playback.
- **Procedural and CLI APIs**: Can be used as command-line tools or integrated directly into other applications' event loops.

---

## Command Line Usage

Ensure your Python virtual environment is active and `sys.path` is configured correctly, or run from the repository root.

### Recording a Session

To record all events on a session named `review-session` to a file:

```bash
python -m sync_recorder.recorder --session review-session --output my_recording.jsonl
```

Options:

- `--session`: The ID of the session to record (default: `otio-sync-demo`).
- `--host`: RabbitMQ host (default: `127.0.0.1`).
- `--port`: RabbitMQ port (default: `5672`).
- `-o`, `--output`: Path to write the output JSON Lines file (Required).
- `--no-handshake`: Disables initial state capture. By default, when starting, the recorder requests the current timeline snapshot from the session master and records it as the first event.
- `--periodic-state`: Periodically request a fresh `STATE_SNAPSHOT` from the master at settle points. Intended for the `sync_test` framework to validate live client state. Off by default.
- `--min-silence SECONDS`: Stream-silence required before an active periodic state request is issued (default: `1.5`). Only relevant with `--periodic-state`.
- `--min-interval SECONDS`: Minimum seconds between active periodic state requests (default: `5.0`). Only relevant with `--periodic-state`.

### Replaying a Recording

To play back a recording into a session:

```bash
python -m sync_recorder.player --session review-session --input my_recording.jsonl
```

Options:

- `--session`: The ID of the session to play back to (default: `otio-sync-demo`).
- `--host`: RabbitMQ host (default: `127.0.0.1`).
- `--port`: RabbitMQ port (default: `5672`).
- `-i`, `--input`: Path to the recording file to play back (Required).
- `--speed`: Playback speed multiplier, e.g. `2.0` plays twice as fast (default: `1.0`).
- `--loop`: Loops playback indefinitely.
- `--keep-guids`: Keeps original source GUIDs instead of replacing them with the player's own unique GUID.
- `--wait-for-peer`: Hold playback until a peer has joined and received the `STATE_SNAPSHOT`, then wait `--post-snapshot-delay` seconds before sending the first recorded event. The player will also start early if it detects peer activity before the delay expires.
- `--post-snapshot-delay SECONDS`: Seconds to wait after delivering the state snapshot before playback begins (default: `3.0`). Only used with `--wait-for-peer`.

### Session Initialization & Replay Handshake

For a joining peer (like an empty OpenRV session) to successfully apply replayed events, its internal timeline structure and GUIDs must match the recording. The package handles this automatically using a Master/Joiner handshake:

1. **Initial State Capture**: When `SyncRecorder` starts, it automatically queries the active master for a `STATE_SNAPSHOT` and records it at the beginning of the file.
2. **Master Simulation**: When `SyncPlayer` plays back a recording that contains a `STATE_SNAPSHOT`, it runs as a master simulator. It listens for `WHO_IS_MASTER` and `STATE_REQUEST` messages from new peers. When a joining peer requests state, the player dynamically intercepts the request and serves the recorded `STATE_SNAPSHOT` targeted to the peer's GUID with updated timestamps. This initializes the peer with the correct timelines, tracks, and GUIDs, allowing subsequent annotations and playhead updates to apply perfectly.

---

## Procedural API Usage

The tools can be integrated directly into other Python scripts, event loops, or plugins.

### Using the Recorder

#### 1. Background Thread Mode (Non-blocking)

```python
import time
from sync_recorder import SyncRecorder

# Initialize the recorder
recorder = SyncRecorder(session_id="review-session")

# Start recording to a file in a background thread
recorder.start(output_file="session_log.jsonl")

# Let it run for a while
time.sleep(10.0)

# Stop recording and clean up network resources
recorder.stop()
```

#### 2. Manual Tick Mode (Integrates with GUI/App Loops)

```python
from sync_recorder import SyncRecorder

recorder = SyncRecorder(session_id="review-session")
# Start recording without a background thread
recorder.start(output_file="session_log.jsonl")

# Call in your application's idle or timer loop:
def on_idle_or_timeout():
    new_events = recorder.tick()
    for event in new_events:
        print(f"Recorded event: {event['payload']['command']}")
```

### Using the Player

#### 1. Blocking Playback

```python
from sync_recorder import SyncPlayer

player = SyncPlayer(session_id="review-session")
player.load_recording("session_log.jsonl")

# Plays back all events, blocks until finished
player.play(speed=1.0, loop=False)
```

#### 2. Non-blocking Tick Mode (Integrates with GUI/App Loops)

```python
import time
from sync_recorder import SyncPlayer

player = SyncPlayer(session_id="review-session")
player.load_recording("session_log.jsonl")

# Initialize the playback state
player.start_playback(speed=1.0, loop=False)

# Call repeatedly in your application's idle or timer loop:
# Return value is True if playback is still active, False if complete.
playing = True
while playing:
    playing = player.tick()
    time.sleep(0.01)
```

---

## Running Unit Tests

Run the package tests using Python's unit test runner from the repository root:

```bash
.venv/bin/python -m unittest tests/otio_sync/test_sync_recorder.py
```
