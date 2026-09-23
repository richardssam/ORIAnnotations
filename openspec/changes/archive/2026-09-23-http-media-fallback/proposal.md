## Why

OTIO's `media_linker` plugin mechanism is the intended extension point for
substituting a clip's `MediaReference` when local media can't be found, but
nothing in this repo has ever exercised it, and it was unverified whether
OpenRV or xStudio's native media readers can even open a clip streamed over
plain HTTP. A same-machine spike (2026-09-23) confirmed both apps can decode
an HTTP-streamed `.mov` correctly — but only against a server that supports
byte-range requests; Python's stock `http.server` fails outright
(`MovieFFMpeg: Invalid pixel format!`) because RV's demuxer can't seek to
read the moov atom. This change builds the plugin and a purpose-built test
server to demonstrate and regression-test that fallback path end to end.

## What Changes

- Add an OTIO `media_linker` plugin (manifest + `link_media_reference`
  implementation) that checks whether a clip's local media resolves, and if
  not, rewrites its `ExternalReference.target_url` to an HTTP URL serving
  the identical file from a configured base URL.
- Add a small Range-request-capable Python HTTP media server (`Accept-Ranges: bytes`,
  `206 Partial Content`) for serving test media — a plain `http.server` is
  not sufficient, as confirmed by the spike.
- Add a `sync_test` scenario where one peer's local media is deliberately
  unavailable and it must fall back to the HTTP copy while its peer (which
  has local media) continues normally, and both converge on the same
  synced state.
- Document the spike findings and the resulting setup (server, plugin,
  test scenario) — the Range-request gotcha in particular, since it's the
  one non-obvious trap someone would otherwise hit blind.

Out of scope for this change: proxy/mixed-resolution fallback media (the
fallback here serves byte-identical media to the local copy), and any
runtime/lazy re-resolution if local media reappears mid-session — the linker
fires at the existing `read_from_string`/`read_from_file` boundaries already
present in the sync pipeline, per OTIO's one-shot linking model.

## Capabilities

### New Capabilities
- `otio-media-http-fallback`: An OTIO `media_linker` plugin that detects
  unresolvable local media on a clip and substitutes an `ExternalReference`
  pointing at an equivalent HTTP URL, wired in via the
  `MediaLinkingPolicy.ForceDefaultLinker` default already used by every
  `read_from_string`/`read_from_file` call in the sync pipeline.
- `http-media-test-server`: A Range-request-capable Python HTTP server for
  serving test media to peers, used by the fallback test scenario and
  documented as a reusable diagnostic tool.

### Modified Capabilities
(none — no existing spec's requirements change)

## Impact

- New: `otio_event_plugin/media_linkers/http_fallback.py` + a `media_linkers`
  entry in the existing `otio_event_plugin/plugin_manifest.json`.
- New: `sync_test/python/sync_test/media_server.py`, a Range-request-capable
  test HTTP server, following the `sync_test/python/sync_test/inspector.py`
  convention.
- New: `sync_test/sync_tests_xstudio.yaml`'s `http_media_fallback` scenario,
  a `media_roots` parallel-list schema addition in `runner.py`, and a new
  `expect_media_path_prefix` scenario command.
- New: `sync_test/run_http_media_fallback_test.py` wrapper script that owns
  the test server's lifecycle.
- New: `docs/http_media_fallback.md`, cross-linked from `docs/testing.md`.
- **Revised from the original plan**: live end-to-end verification (see
  `tasks.md` §6) found this does, in fact, require small fixes in existing
  production/harness code — each a pre-existing "assumed media is always a
  local filesystem path" gap that nothing had exercised with an `http://`
  `target_url` before:
  - `rvplugin/ori_sync/utils.py::_media_path()` — added an `http(s)://`
    early-return guard (mirrors an existing guard already used elsewhere in
    the same file for `file://`).
  - `sync_test/python/sync_test/spawner.py` — OpenRV's launch path was
    missing `OTIO_PLUGIN_MANIFEST_PATH` (xStudio's already had it); added
    for the media_linker to resolve at all.
  - `sync_test/python/sync_test/openrv_hook.py` — its `media_exists` check
    did a bare filesystem `os.path.exists()` with no HTTP-URL guard; fixed
    to treat a resolved `http://` path as existing.
  All three are narrowly scoped, backward-compatible one-off guards, not a
  broader redesign — see `tasks.md` §6 for what each was verified against.
