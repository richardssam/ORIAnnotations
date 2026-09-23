---
layout: default
title: HTTP media fallback
has_children: true
nav_order: 5
---


# HTTP media fallback

When a peer's local copy of a clip's media is missing, an OTIO `media_linker`
plugin can substitute an HTTP-served copy instead. This doc walks through
seeing that work in a real session first, then the smaller pieces and the
automated coverage. For the design rationale (why it's built this way, what
was considered and rejected), see
`openspec/changes/http-media-fallback/design.md`.

## Try it in a real, live session

This is the actual feature — a client whose local media is missing picks up
an HTTP-served copy automatically, mid-session, with no special handling on
the host's side. This is the quickest way to see that.

**1. Start the media server**, pointed at whatever directory the host's real
media lives in:

```bash
export PYTHONPATH="$(pwd)/sync_test/python:$PYTHONPATH"
python -m sync_test.media_server 8899 test_media/source/encoded
```

**2. Start (or use an already-running) host peer** with the real media
loaded, exactly as you normally would — nothing about the host needs to
change for this feature.

**3. Start the client peer** with three extra env vars beyond your normal
launch. All three matter — see "Configuration reference" below for what
each one does and why leaving one out fails differently:

```bash
export ORI_MEDIA_LOCAL_ROOT=/tmp/does_not_exist   # a directory that does NOT have the media
export ORI_MEDIA_HTTP_BASE_URL=http://127.0.0.1:8899
export OTIO_DEFAULT_MEDIA_LINKER=http_fallback

ORI_SYNC_USER=sam2 ORI_SESSION=localhost:samtest \
  ORI_SYNC_LOG_FILE=xstudio_plugin/ori_sync/xstudio_client.log \
  xstudio -n
```

(For an OpenRV client instead, the same three env vars apply — OpenRV additionally
needs `OTIO_PLUGIN_MANIFEST_PATH` set explicitly; see the reference table.)

**4. Confirm it actually happened**, two ways:

- **Check what got registered**, before even looking at media:
  ```bash
  grep "ORI Sync" xstudio_plugin/ori_sync/xstudio_client.log
  ```
  Look for `[ORI Sync] SyncEvent manifest: .../otio_event_plugin/plugin_manifest.json`
  pointing at your actual checkout (not a stale packaged copy). If you see
  `NotSupportedError: Could not find plugin: 'http_fallback'` anywhere in
  the log instead, the manifest wasn't found — the linker's name was asked
  for but never resolved.

- **Check the clip's actual media reference**, once the client has joined
  and picked up a clip from the host:
  ```bash
  export PYTHONPATH=/path/to/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages
  /path/to/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 - <<'EOF'
  from xstudio.connection import Connection
  XSTUDIO = Connection(auto_connect=False)
  XSTUDIO.connect_remote("127.0.0.1", 14442)  # client's API port — check its startup log for the real one
  for media in XSTUDIO.api.session.media:
      print(media.name, "->", media.media_source().media_reference)
  EOF
  ```
  If it worked, `media_reference` reads
  `http://127.0.0.1:8899/car_ACES_sRGB.mov 24.000000 101 1` instead of a
  local path. `debug/xstudio_inspect.py` gives the same information for a
  whole playlist/track view if you want more than one clip at a time.

## Quick sanity check: can the app even read HTTP media at all?

Skips the linker entirely — useful for isolating "is this an app/network
problem" from "is this a linker/sync problem" before debugging the real
scenario above. With the media server running (previous section):

**OpenRV**, via `rvpush`:

```bash
RVPUSH=/Applications/OpenRV.app/Contents/MacOS/rvpush
$RVPUSH py-exec "rv.commands.addSource('http://127.0.0.1:8899/car_ACES_sRGB.mov')"
$RVPUSH py-eval-return "rv.commands.sourceMediaInfo(rv.commands.nodesOfType('RVFileSource')[0])"
```

If `sourceMediaInfo` comes back with real width/height/frame numbers and no
`ERROR: MovieFFMpeg` lines in RV's console, it worked.

**xStudio**, via its embedded Python (adjust the interpreter/site-packages
paths for your build — see `debug/README.md`):

```bash
export PYTHONPATH=/path/to/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages
/path/to/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 - <<'EOF'
from xstudio.connection import Connection
XSTUDIO = Connection(auto_connect=True)
playlist = XSTUDIO.api.session.create_playlist("manual_test")[1]
media = playlist.add_media("http://127.0.0.1:8899/car_ACES_sRGB.mov")
print("status:", media.status)
print("is_online:", media.is_online)
XSTUDIO.disconnect()
EOF
```

`status` should read `MediaStatus.MS_ONLINE`.

## Try the linker directly, no apps at all

The plugin is a plain function — you can call it straight from a Python
shell to see exactly what it does:

```bash
# Use the repo's `python` (pyenv), not a system python3 — see docs/testing.md
export ORI_MEDIA_LOCAL_ROOT=/tmp/does_not_exist
export ORI_MEDIA_HTTP_BASE_URL=http://127.0.0.1:8899
python - <<'EOF'
import sys, opentimelineio as otio
sys.path.insert(0, "otio_event_plugin/media_linkers")
import http_fallback

clip = otio.schema.Clip(
    media_reference=otio.schema.ExternalReference(
        target_url="/some/local/path/car_ACES_sRGB.mov",
    ),
)
result = http_fallback.link_media_reference(clip, {})
print(result.target_url)  # -> http://127.0.0.1:8899/car_ACES_sRGB.mov
EOF
```

Point `ORI_MEDIA_LOCAL_ROOT` at a directory that *does* have
`car_ACES_sRGB.mov` and re-run — the result should be that local path
instead, unchanged.

## Automated coverage

Once you've seen it work manually, these are the repeatable versions —
what CI/regression runs actually check.

**The same scenario above, scripted** — spawns xStudio and OpenRV, starts
the media server, drives it, asserts the client's media path is the HTTP
URL and both peers converge, tears everything down. Requires RabbitMQ
running locally (`localhost:5672`), same as any other `sync_test` scenario:

```bash
python sync_test/run_http_media_fallback_test.py
```

**Unit tests for the linker and server in isolation** (no live apps):

```bash
./run_tests_core.sh -k "http_media_linker or media_server" -v
```

## Configuration reference

The linker is inert (a pure pass-through) unless all of these are set. This
is deliberate: registering the plugin in the shared manifest has zero effect
on any process that doesn't opt in.

| Variable | Required for | Meaning |
| --- | --- | --- |
| `OTIO_PLUGIN_MANIFEST_PATH` | **OpenRV only** | Points OTIO at `otio_event_plugin/plugin_manifest.json`, where the linker is registered. `spawner.py` sets this automatically for every `sync_test`-spawned app. A manual OpenRV launch needs it exported. |
| *(none — self-registered)* | **xStudio** | xStudio's own `xstudio_plugin/_ori_bootstrap.py` finds and registers the manifest itself (`ActiveManifest().extend(...)`), independent of this env var — that's deliberate (see its docstring). Nothing to set; check its log for `[ORI Sync] SyncEvent manifest: ...` if you want to confirm what it found. |
| `OTIO_DEFAULT_MEDIA_LINKER` | Both | Must be `http_fallback` — names it as the linker to actually run. Without this, the linker is registered but never invoked, and OTIO silently no-ops on every clip. |
| `ORI_MEDIA_LOCAL_ROOT` | Both | Directory to check a clip's basename against. |
| `ORI_MEDIA_HTTP_BASE_URL` | Both | Where to serve a basename from on a local miss. |

In `sync_test`, `runner.py` sets the last three (and `OTIO_PLUGIN_MANIFEST_PATH`
for OpenRV) per peer, from a scenario's `media_roots`/`media_http_base_url`
fields — see below. For a manual launch, export them yourself as shown
above.

In a `sync_tests_xstudio.yaml` scenario, this is just two fields:

```yaml
media_roots:
  - "test_media/source/encoded"     # host: has the file
  - "test_media/source/frames/car"  # client: doesn't
media_http_base_url: "http://127.0.0.1:8899"
```

## The one gotcha: a naive HTTP server does not work

Both OpenRV's `MovieFFMpeg` reader and xStudio's media backend need to issue
byte-range (`Range:`) requests to seek a `.mov`'s header — Python's stock
`python -m http.server` doesn't implement `Range:` at all (always answers
`200` with the full body), and RV fails outright against it:

```text
ERROR: MovieFFMpeg: Invalid pixel format! http://127.0.0.1:8899/car_ACES_sRGB.mov
ERROR: Open of 'http://127.0.0.1:8899/car_ACES_sRGB.mov' failed: unsupported media type.
```

`sync_test/python/sync_test/media_server.py` implements the minimum needed
(`Accept-Ranges: bytes`, correct `206 Partial Content` responses) — use it,
not a bare `http.server`, for anything testing this.

## Limitations

- **Same media only.** The HTTP copy is byte-identical to the local copy —
  swapping in a different-resolution proxy on fallback isn't built.
- **`.mov` only, verified.** Image sequences and other codecs weren't
  exercised.
- **No push/watch.** The linker re-checks local availability every time
  OTIO calls it (i.e. on the next sync event that round-trips the clip) —
  nothing re-resolves media that reappears locally with no new event to
  trigger a check.
