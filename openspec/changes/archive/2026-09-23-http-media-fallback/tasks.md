## 1. Media linker plugin

- [x] 1.1 Create `otio_event_plugin/media_linkers/http_fallback.py` implementing
      `link_media_reference(in_clip, media_linker_argument_map)`: read
      `ORI_MEDIA_LOCAL_ROOT`/`ORI_MEDIA_HTTP_BASE_URL` from the environment,
      return the clip's existing media reference unchanged if either is unset
      or `in_clip.media_reference` is not an `ExternalReference`, otherwise
      compute `os.path.basename(target_url)` and branch on
      `os.path.exists(ORI_MEDIA_LOCAL_ROOT/basename)` per design decision 2.
      Verify by unit-testing the function directly with a temp directory
      standing in for `ORI_MEDIA_LOCAL_ROOT`.
- [x] 1.2 Add a `media_linkers` entry (`MediaLinker.1`, name `http_fallback`,
      pointing at the new file) to `otio_event_plugin/plugin_manifest.json`
      alongside the existing `schemadefs` entry. Verify with
      `otio.media_linker.from_name("http_fallback")` resolving successfully
      when `OTIO_PLUGIN_MANIFEST_PATH` points at this manifest.
- [x] 1.3 Add `tests/otio_sync/test_http_media_linker.py` covering: local
      media present (reference unchanged), local media missing (rewritten to
      `ORI_MEDIA_HTTP_BASE_URL` + basename, `available_range` preserved),
      both env vars unset (inert no-op), and clip metadata (`sync.guid`)
      unchanged across substitution — one test per spec scenario in
      `specs/otio-media-http-fallback/spec.md`. Verify: `./run_tests_core.sh`
      passes with the new tests included.
- [x] 1.4 Add a "re-evaluated on every invocation" test: run the linker twice
      on the same clip with `ORI_MEDIA_LOCAL_ROOT` pointing at a directory
      that gains the file between calls (create it after the first call),
      asserting the second call returns a local reference. Verify: test
      passes, demonstrating no cross-call state/caching.

## 2. Range-capable test media server

- [x] 2.1 Create `sync_test/python/sync_test/media_server.py`: a threading
      `HTTPServer` with a `Range:`-aware handler (base it on the spike's
      `range_server.py` draft — 206 responses with correct `Content-Range`,
      `Accept-Ranges: bytes` on all responses) and a path-traversal guard
      rejecting paths resolving outside the served directory. Runnable via
      `python -m sync_test.media_server <port> <dir>`. Verify by curling both
      a plain GET (200, full body) and a ranged GET (206, correct slice and
      `Content-Range`) against a running instance.
- [x] 2.2 Add a path-traversal test asserting a `../`-containing request path
      is rejected. Verify: test passes and confirms no file outside the
      served directory is returned.
- [x] 2.3 Manually re-verify against real RV/xStudio using this server
      (mirroring the earlier ad hoc spike, now against the checked-in
      module instead of the scratch script): `addSource`/`add_media` on the
      server's URL for `test_media/source/encoded_notc/car_ACES_sRGB.mov`
      resolves with the correct frame range and no decode errors in either
      app's log.

## 3. sync_test scenario wiring

- [x] 3.1 Add an optional `media_roots` parallel list to the test schema in
      `sync_test/python/sync_test/runner.py`, following the existing
      `roles`/`users` pattern (`runner.py:1540-1561`): when present, set
      `ORI_MEDIA_LOCAL_ROOT` (from the per-peer list value),
      `ORI_MEDIA_HTTP_BASE_URL`, and `OTIO_DEFAULT_MEDIA_LINKER=http_fallback`
      in that peer's `env_extra`. Verify: an existing test without
      `media_roots` set runs unaffected (env unchanged for every other
      scenario).
- [x] 3.2 Add an `http_media_fallback` scenario to `sync_test/sync_tests_xstudio.yaml`
      (or `sync_tests.yaml`, matching wherever the `car_ACES_sRGB.mov` fixture
      is already referenced): host peer's `media_roots` entry is
      `test_media/source/encoded_notc`, client peer's is an empty scratch
      directory. Include a required `description` per the suite's convention.
- [x] 3.3 Add a checkpoint/assertion that the client peer's `media_path`
      (already surfaced via the existing inspector `/state` endpoint, per
      design decision 7) starts with the configured `ORI_MEDIA_HTTP_BASE_URL`,
      and that both peers still converge on the same clip/frame. Verify:
      `openspec/changes/http-media-fallback` scenario passes when run
      alongside a live media server.
- [x] 3.4 Write a small wrapper script (e.g.
      `sync_test/run_http_media_fallback_test.py`) that starts
      `media_server.py` pointed at `test_media/source/encoded_notc/`, runs
      the `http_media_fallback` scenario, and tears the server down
      afterward (design decision 6). Verify: running the script end-to-end
      passes without requiring a manually pre-started server.

## 4. Documentation

- [x] 4.1 Write `docs/http_media_fallback.md` (or extend `docs/testing.md`)
      documenting: the spike's finding that plain `http.server` fails
      RV's demuxer with `Invalid pixel format!` and needs Range support: how
      the linker is wired in (manifest + two env-var gates); how to run the
      new scenario via the wrapper script; and the explicit non-goals (same
      media only, no proxy/resolution swap, no reappearance-detection beyond
      re-invocation). Cross-link from `AGENTS.md`'s docs table if that table
      is the established index for this kind of constraints doc.

## 5. End-to-end verification

- [x] 5.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh` and confirm
      no regressions (per `docs/testing.md`'s two-suite/two-interpreter
      requirement).
- [x] 5.2 Run the full `http_media_fallback` scenario via the wrapper script
      end-to-end at least 3 times to check for flakiness before considering
      this change complete, consistent with this project's general practice
      of not trusting a single green run for sync-timing-sensitive tests.

## 6. Fixes discovered during end-to-end verification

Live runs surfaced three pre-existing gaps the proposal did not anticipate —
each is "assumes media is always a local filesystem path," just at a
different layer. None of these are specific to this feature; they were
latent until this was the first thing to exercise an `http://` `target_url`
through these paths. Recorded here since they contradict the proposal's
original "no changes to rvplugin/ori_sync production code" Impact claim —
see the updated proposal.md/design.md.

- [x] 6.1 `spawner.py`'s OpenRV launch path never set
      `OTIO_PLUGIN_MANIFEST_PATH` (unlike its xStudio path) — harmless for
      schemadefs (which degrade to a generic object on a lookup miss) but
      fatal for a named `media_linker`, which raises `NotSupportedError` on
      every `read_from_string` call in the process if the name can't
      resolve. Fixed by setting it unconditionally in the OpenRV branch too,
      mirroring the xStudio branch. Verified: RV no longer throws
      `NotSupportedError` on the fallback clip.
- [x] 6.2 `rvplugin/ori_sync/utils.py::_media_path()` only special-cased
      `file://` URLs; any other string (including `http://`) fell through to
      `os.path.abspath(os.path.normpath(url))`, which corrupts an HTTP URL
      (collapses `http://` to `http:/`, prepends cwd). Fixed with the same
      early-return guard `sequence_sync.py`'s send-side code and xStudio's
      `_uri_to_posix_path` already use. Required a `reinstall.csh` — RV loads
      the installed `.rvpkg`, not the repo source (see `feedback_rvpkg_reinstall_before_test`
      in project memory). Verified: `openrv`'s reported `media_path` is the
      clean HTTP URL, unmangled.
- [x] 6.3 `sync_test/python/sync_test/openrv_hook.py`'s `media_exists` check
      called bare `os.path.exists(path)` with only a `file:` scheme guard —
      always `False` for an HTTP URL, which the runner's `compare_states`
      reports as `missing_media` regardless of whether the clip actually
      loaded. Fixed by treating an `http://`/`https://` path as existing
      without a filesystem check (RV having created a source for it is the
      available existence signal). Verified: the scenario's final coherence
      check passes.
- [x] 6.4 Scenario command ordering: `set_selection` was sent immediately
      after `add_media`, before `wait_for_media` — fine for local media, but
      an HTTP-streamed clip takes measurably longer to resolve, so xStudio's
      selection broadcast could reach RV before RV had registered the clip,
      producing `view_mirror_failed` ("host's clip is in neither the object
      map nor the bin cache"). Fixed by reordering to
      `add_media → wait_for_media → set_selection` in
      `sync_tests_xstudio.yaml`, matching `_wait_for_media`'s own documented
      purpose (`runner.py:840`). Verified: 3/3 clean runs via the wrapper
      script.
