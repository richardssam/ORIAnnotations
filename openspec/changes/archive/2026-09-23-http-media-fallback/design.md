## Context

See [proposal.md](proposal.md) for motivation and the spike findings. Relevant
existing structure this design builds on:

- `otio_event_plugin/plugin_manifest.json` is already an OTIO `PluginManifest.1`
  loaded via `OTIO_PLUGIN_MANIFEST_PATH`, and that env var is already set for
  every `sync_test`-spawned RV/xStudio process (`sync_test/python/sync_test/spawner.py:120`).
  It currently only declares `schemadefs`; `PluginManifest.1` also supports a
  `media_linkers` array in the same file.
- Every `read_from_string`/`read_from_file` call across the sync pipeline
  (`protocol_messages.py`, `patcher.py`, `sequence_sync.py`, `structure_sync.py`,
  `timeline_build.py`, `annotation_sync.py`) already uses OTIO's default
  `media_linker_name=MediaLinkingPolicy.ForceDefaultLinker`, which resolves
  the linker to run by reading `$OTIO_DEFAULT_MEDIA_LINKER` — currently unset
  everywhere, so today this is already a proven no-op path.
- `AppSpawner.launch()` (`sync_test/python/sync_test/spawner.py:80`) already
  accepts a per-instance `env_extra` dict, and `runner.py:1540-1561` already
  builds `env_extra` from parallel per-peer YAML lists (`roles`, `users`) —
  the precedent for a new parallel per-peer list is established, not new
  machinery.
- The spike (see proposal) confirmed both RV (`MovieFFMpeg`) and xStudio's
  media backend can decode a `.mov` over plain HTTP GET, but only against a
  server that answers `Range:` requests with `206 Partial Content`; Python's
  stdlib `http.server` does not, and fails with `Invalid pixel format!`.

## Goals / Non-Goals

**Goals:**
- A `media_linker` plugin that is genuinely host-agnostic — zero changes to
  `rvplugin/ori_sync` or `xstudio_plugin/ori_sync` production code.
- A `sync_test` scenario that exercises the fallback for real, through the
  existing checkpoint/state-assertion machinery, not a bespoke one-off script.
- Made-obvious documentation of the Range-request requirement, since it's the
  one trap that isn't discoverable without the spike.

**Non-Goals:**
- Generalizing `sync_tests.yaml`/runner.py into a full "arbitrary per-peer env
  var" mechanism — one new parallel list, matching the existing `roles`/`users`
  pattern, is enough for this scenario.
- Handling clips whose local media lives under different basenames per peer,
  or media reappearing via anything other than a fresh linker invocation
  (e.g. no filesystem watch/push notification).
- Making the linker a general-purpose production feature — it's scoped as a
  test/demo capability behind env vars that default to fully inert.

## Decisions

### 1. Register the linker in the existing `otio_event_plugin` manifest
Add a `media_linkers` entry to `otio_event_plugin/plugin_manifest.json`
pointing at a new `otio_event_plugin/media_linkers/http_fallback.py`, rather
than creating a new plugin package with its own manifest and env var wiring.
**Alternative considered**: a standalone `otio_media_linker/` package. Rejected
— it would need its own `OTIO_PLUGIN_MANIFEST_PATH` entry threaded through
every spawn site (`spawner.py`, `testchart/`, various `tests/otio_sync/*.py`),
duplicating work the existing manifest already does for free.

### 2. Local/remote path resolution is symmetric and root-based, not scheme-based
The linker does not trust `target_url`'s current scheme to decide what "local"
means. Instead, given `ORI_MEDIA_LOCAL_ROOT` and `ORI_MEDIA_HTTP_BASE_URL` env
vars, it always computes `os.path.basename(target_url)` and checks
`os.path.exists(ORI_MEDIA_LOCAL_ROOT/basename)`. If that exists, the returned
reference points at the local path; otherwise it points at
`ORI_MEDIA_HTTP_BASE_URL/basename`. This runs identically regardless of
whether the clip's current `target_url` is already a local path or an
http URL from a prior substitution — which is what makes spec requirement
"Local-availability check is re-evaluated on every invocation" hold without
needing to stash the original path in clip metadata.

If either env var is unset, `link_media_reference` returns the clip's
existing media reference unchanged (fully inert) — this is what makes it
safe to register the linker globally without affecting any test whose
scenario doesn't opt in.

**Trade-off accepted**: this only works when local and remote media share a
flat basename under one root each (true for `test_media/` today). Real
per-clip arbitrary local paths are out of scope (see Non-Goals).

### 3. Activation: two independent env-var gates, both required
- `OTIO_DEFAULT_MEDIA_LINKER=http_fallback` — names the linker.
- `ORI_MEDIA_LOCAL_ROOT` / `ORI_MEDIA_HTTP_BASE_URL` — the linker's own
  gate; unset means inert regardless of the first var.

Only the new scenario's peers get both set. `spawner.py`'s global env is
untouched; per-peer overrides go through the existing `env_extra` path.
**Alternative considered**: set `OTIO_DEFAULT_MEDIA_LINKER` globally in
`spawner.py` since it's inert without the second gate. Rejected as
unnecessary blast radius for no benefit — scoping both gates to the one
scenario that needs them keeps every other test's env identical to today.

### 4. New parallel YAML list: `media_roots`
Add an optional `media_roots` list to the test schema, following the same
per-peer-by-index convention as `roles`/`users`/`fixtures`
(`runner.py:1540-1561`). When present, `runner.py` sets
`ORI_MEDIA_LOCAL_ROOT` (from the list) and a fixed `ORI_MEDIA_HTTP_BASE_URL`
in that peer's `env_extra`, and also sets `OTIO_DEFAULT_MEDIA_LINKER=http_fallback`.
The new scenario gives the host peer `test_media/source/encoded_notc` (has
the file) and the client peer an empty scratch directory (doesn't) — same
media, different local availability, no real multi-machine setup needed.

### 5. Test HTTP server: new module, not stdlib `http.server`
New `sync_test/python/sync_test/media_server.py`, following the location
convention of `sync_test/python/sync_test/inspector.py`. Implements a
threading `HTTPServer` with a `Range:`-aware handler (validated in the
spike) plus a path-traversal guard. Runnable standalone (`python -m
sync_test.media_server <port> <dir>`) or importable for
programmatic start/stop.

### 6. Server lifecycle: owned by a small wrapper, not `runner.py`
`runner.py` has no concept of an "external service" beyond assuming
RabbitMQ is already up. Rather than teach it to manage a service subprocess
just for this one scenario, a small wrapper script starts
`media_server.py` pointed at `test_media/source/encoded_notc/`, runs the
`http_media_fallback` scenario via the existing runner entry point, and
tears the server down after. This mirrors how RabbitMQ is an assumed
external prerequisite today, but keeps this one dependency
self-contained instead of pushing a manual step onto whoever runs the suite.

### 7. Test assertion: reuse existing inspector state, no new hook fields
`runner.py` already reads a peer's `media_path` from its `/state` response
(`runner.py:79`). Once the client peer's linker substitutes an http URL,
that peer's own `media_path` naturally becomes the http URL — visible for
free through the existing inspector plumbing. The new scenario's checkpoint
just asserts the client's `media_path` starts with `http://` and that both
peers still converge on the same clip/frame, with no changes needed to
`openrv_hook.py` or `xstudio_hook.py`.

## Risks / Trade-offs

- **[Risk]** A test server outage or port clash makes the new scenario flaky
  in a way unrelated to sync logic. → **Mitigation**: the wrapper owns
  server start/stop tightly scoped to the one test run; treat failures to
  bind/serve as a hard setup error, not a `missing_media`-classified sync
  failure (keeps failure-kind classification meaningful).
- **[Risk]** `os.path.exists` on every linker invocation adds filesystem
  I/O on a hot path (the linker fires on every `read_from_string` call
  across the sync pipeline once registered). → **Mitigation**: negligible in
  practice — a handful of clips, one `stat()` each, and it's fully inert
  (skipped before the check) for every process that doesn't set the two
  `ORI_MEDIA_*` env vars, i.e. every test except this one.
- **[Risk]** Range-request support is necessary but was only verified for
  `.mov` via RV's `MovieFFMpeg` and xStudio's default backend — other
  formats (image sequences, other codecs) aren't verified. → **Mitigation**:
  explicitly out of scope; note in the doc as unverified rather than implying
  broader coverage than tested.

## Migration Plan

Mostly additive: a new manifest entry, a new linker module, a new server
module, one new YAML scenario, one new wrapper script, one new doc. Also
three narrow, backward-compatible guard additions to existing code, found
necessary by live end-to-end verification (see tasks.md §6) rather than
anticipated up front:

- `rvplugin/ori_sync/utils.py::_media_path()` gains an `http(s)://`
  early-return, alongside the existing `file://` handling — an `http(s)://`
  string was previously mis-normalized into a corrupted local path.
- `spawner.py`'s OpenRV launch path now sets `OTIO_PLUGIN_MANIFEST_PATH`
  (already set on the xStudio path) — previously any named
  `OTIO_DEFAULT_MEDIA_LINKER` would fail to resolve for OpenRV specifically,
  independent of anything this change added.
- `openrv_hook.py`'s `media_exists` check now recognizes an `http(s)://`
  path as existing rather than running a filesystem `os.path.exists()`
  against it.

None of the three change behavior for any `target_url` that was already a
local path or `file://` URL — each new branch triggers only on a string no
prior code path was designed to receive, so existing tests are unaffected
(confirmed: `run_tests_core.sh` and `run_tests_xstudio.sh` still pass in
full — see tasks.md §5.1). Rollback: revert the new files, plus these three
targeted diffs. Note the `rvplugin/ori_sync` change requires a
`reinstall.csh` to reach a running RV, same as any other edit to that
package (see `docs/testing.md`/project memory).
