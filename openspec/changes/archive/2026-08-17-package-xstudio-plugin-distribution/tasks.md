## 1. Rescue the constraint knowledge before deleting anything

- [x] 1.1 Move `xstudio_plugin/ori_sync/README.md:77-115` ("Acquire the playhead from the viewport, never from `current_playhead()`") into `docs/xstudio_constraints.md` as its own section, keeping the `playhead_global_events_actor.cpp` line references and the symptom description (sequence built from a bin → playback/selection sync silently dead, masked by selection sync continuing to work). Widened in the doing: README:53-76 (why `subscribe_to_playhead_events()` is never called — the two compounding causes) is the direct rationale for the workaround and was migrated alongside it rather than left orphaned.
- [x] 1.2 Fold the README's `pr/python-per-subscription-listeners` warning into the constraints doc's existing per-subscription-listeners passage (now `docs/xstudio_constraints.md:478`, shifted by 1.1's insertion) rather than duplicating it
- [x] 1.3 Confirmed: with 1.1/1.2 done, everything in README:53-115 that is still true now lives in the constraints doc. Only README:32-52 (branch name, fork point, the five-commit rebuild table, the "dropped patch" note) remains — that content is obsolete now that the patches are upstream on `develop`, not migration-worthy. **Safe to delete in task 6.1: README:32-135 in full** (the entire "xStudio build + required patches" section through "### Build (macOS, as used here)").

## 2. Shared payload resolver

- [x] 2.1 Create `xstudio_plugin/_ori_bootstrap.py` exposing an idempotent `ensure_payload()`; a second call, from either plugin, must be a no-op
- [x] 2.2 Implement the candidate-root walk in D3 order: `<search_folder>/ori_sync/_ori_payload/` → `<search_folder>/<this_plugin>/_ori_payload/` → `<search_folder>/_vendor/` → repo `python/` + `otio_event_plugin/`; add every root that exists to `sys.path`
- [x] 2.3 Resolve the schemadef manifest to the **first** `otio_event_plugin/plugin_manifest.json` found while walking, never all of them
- [x] 2.4 Register the schemadef via `ActiveManifest().extend(manifest_from_file(...))`, guarded by `"SyncEvent" not in otio.schema.schemadef.available_schemadef_names()`; do not write `OTIO_PLUGIN_MANIFEST_PATH`
- [x] 2.5 Log the selected roots, the manifest path, and the package `VERSION` when present, in the style of the RV plugin's startup banner
- [x] 2.6 Add the path-based loader (`spec_from_file_location` against `<plugin_dir>/../_ori_bootstrap.py`) that both plugins use, so resolution does not depend on who put what on `sys.path`. Necessarily duplicated in both plugin files rather than shared (D2: importing it from one plugin package would execute that package eagerly).

## 3. Wire both plugins to the resolver

- [x] 3.1 Delete the path-setup block at `xstudio_plugin/ori_sync/utils.py:13-28` and call `ensure_payload()` in its place, preserving the "imports of utils get the side-effect" property the module docstring promises
- [x] 3.2 Delete the path-setup block at `xstudio_plugin/ori_annotations/ori_annotations.py:24-40` and call `ensure_payload()` before the `import ORIAnnotations` and `module_from_name("SyncEvent")` lines that depend on it
- [x] 3.3 Verify no module outside the resolver still computes a repo root from `__file__`, or still mutates `OTIO_PLUGIN_MANIFEST_PATH`, anywhere under `xstudio_plugin/`. Only a stale comment in `ori_sync_plugin.py:43` referenced the old mechanism; reworded.

## 4. Packaging script

- [x] 4.1 Add `make_xstudio_package.sh` staging into `dist/ori-sync-xstudio/`: both plugin packages with their `qml/` trees, `_ori_bootstrap.py`, `README.md`, `VERSION`
- [x] 4.2 Stage the payload into `ori_sync/_ori_payload/` by **globbing** `python/otio_sync_core/` — no hand-maintained module list — plus `python/ORIAnnotations.py` and `otio_event_plugin/`
- [x] 4.3 Vendor `pika` via `pip install --target`; do not vendor `opentimelineio`
- [x] 4.4 Write `VERSION` at stage time (D8: stamped and logged, not broadcast)
- [x] 4.5 Fail packaging if the staged tree's top level contains any directory other than `ori_sync/` and `ori_annotations/` — verified: injecting a stray dir aborts packaging with exit 1
- [x] 4.6 Add `--vendor-only` (populate `xstudio_plugin/ori_sync/_vendor/` for live development, no staging) and `--install-app <path/to/xSTUDIO.app>` (stage, then copy the tree's *contents* into `Contents/Resources/plugin-python/`) — both exercised manually against a fake bundle; `--install-app` also rejects a non-bundle path
- [x] 4.8 (added post-implementation, user feedback) No-arguments and `--help` now print usage and exit cleanly rather than silently performing the previous default staging action — the script has real side effects (a `pip install` on first vendoring, file writes) and had no explicit "just build it" flag before. Added `--package` as that explicit flag. Added `--install-dir <path>` for a persistent, version-independent plugin folder shared across xStudio builds, decoupled from any single app bundle — creates the target if it doesn't exist, safe to re-run. Both share an `install_into()` helper with `--install-app`. Tests: `test_no_arguments_shows_help_and_does_nothing`, `test_install_dir_creates_a_persistent_folder_independent_of_any_app` (includes a re-run-is-idempotent check). Both READMEs and design.md D6 updated; all bare `./make_xstudio_package.sh` invocations in docs/tests updated to `--package`.
- [x] 4.7 Gitignore `xstudio_plugin/_vendor/` and `dist/`

**Verified live**: staged tree loaded under the xStudio-bundled interpreter with `env -i` (all environment variables cleared, including `PYTHONPATH`/`XSTUDIO_PYTHON_PLUGIN_PATH`/`OTIO_PLUGIN_MANIFEST_PATH`) — both `ori_sync` and `ori_annotations` imported via `find_spec`/`exec_module` exactly as xStudio's scanner does, `SyncEvent` registered, and `pika`/`otio_sync_core`/`ORIAnnotations` all resolved from the packaged payload, not any system location.

## 5. Tests

- [x] 5.1 Resolver picks the repo roots when no packaged payload is present, and the packaged payload when one is (`tests/xstudio_plugin/test_ori_bootstrap.py`)
- [x] 5.2 Resolver is reachable and correct when only a plugin directory is on `sys.path`, matching how `tests/otio_sync/test_xstudio_annotations.py:8` imports
- [x] 5.3 Schemadef registration is idempotent across two `ensure_payload()` calls in either plugin order, and `isinstance` against `SyncEvent` types holds for objects from both plugins — tested via subprocess isolation since OTIO's active manifest is a process-wide singleton that earlier tests in the same session would otherwise have already populated
- [x] 5.4 Registration still succeeds when OTIO's active manifest was already built before the plugins load, and when `OTIO_PLUGIN_MANIFEST_PATH` is also set to the packaged manifest
- [x] 5.5 Packaging smoke test asserting exactly two directories at the staged tree's top level, and that `pika` and every `otio_sync_core` module are present (`tests/xstudio_plugin/test_packaging.py`). Added `make_xstudio_package.sh --verify-only DIR` so the "stray directory" test exercises the script's real guard against a deliberately corrupted tree rather than duplicating its logic.
- [x] 5.6 `./run_tests_xstudio.sh` and `./run_tests_core.sh` pass unchanged — 113/113 and 724/724 (+4 subtests)

**Bug caught by 5.1**: the first version of `ensure_payload()` only recorded a root in `_SELECTED_ROOTS` (used for the startup log and by these tests) when it was *newly* added to `sys.path` — a root already present from an unrelated earlier `sys.path.insert` (which happens routinely in the shared-process test suite) silently vanished from the diagnostic output while still functioning correctly for imports. Fixed in `_ori_bootstrap.py` to record every existing root regardless of prior `sys.path` state.

## 6. Documentation

- [x] 6.1 Rewrite `xstudio_plugin/ori_sync/README.md` installation around placing one folder; delete the `xstudio_sync_fixes` build section now that task 1 rescued its surviving content
- [x] 6.2 Delete the three stale "Known limitations": `draw_remote` / `_apply_remote_annotation` exists nowhere in the code, `aspect_half` is computed per-media by `python/otio_sync_core/coords.py:51`, and TLS is supported at `python/otio_sync_core/rabbitmq_network.py:53-90` — section removed entirely, nothing currently documented needed to survive
- [x] 6.3 Reduce the environment-variable table to the genuinely optional entries (`ORI_SYNC_LOG_FILE`, `ORI_ANNOTATIONS_LOG_FILE`) and state plainly that none is required
- [x] 6.4 Rewrite `xstudio_plugin/ori_annotations/README.md` installation the same way
- [x] 6.5 State the xStudio floor as "a build from `develop`" (D9), and add a `TODO.md` entry to replace it with a version number once a release containing PRs #299 / #302 / #303 ships
- [x] 6.6 Document the install routes without prescribing one — app bundle `plugin-python/` as the suggested default, `XSTUDIO_PYTHON_PLUGIN_PATH` and the Preferences → Python → "Python Plugins Search Paths" field as equally valid
- [x] 6.7 Close `TODO.md:85` ("README says it needs a OTIO_PLUGIN_MANIFEST_PATH for xstudio. Can we make it part of the plugin.") — also closed its neighbour, the pika-vendoring TODO this change also resolves

## 7. Live verification

- [ ] 7.1 `--install-app` into a stock `develop` build, launch with **no** ORI environment variables set and no `pika` in the bundled interpreter; both plugins load and the console shows no `Failed to load plugin` lines — **not yet re-confirmed against the real GUI post-fix**; see correction below
- [ ] 7.2 Two-peer session (xStudio + RV) against that install: playback sync, structure sync, and an annotation round-trip in both directions — the passing two-xStudio-peer `sync_test` (structure + playback + annotations) actually exercised 7.3's target (the repo checkout), not "that install" (the packaged `--install-app` bundle); RV pairing also still open
- [x] 7.3 Confirm a repo checkout still works directly as a search folder with no packaging step, and that its startup log shows the repo roots — confirmed: the user's passing two-xStudio-peer `sync_test` ran with `XSTUDIO_PYTHON_PLUGIN_PATH` pointed at `xstudio_plugin/` (`spawner.py`'s unmodified default, confirmed via `git diff` showing no change), covering structure, playback, and annotation sync against the checkout directly
- [ ] 7.4 Re-run `--install-app` after a `cmake --build --target install` and confirm the documented need to reinstall is accurate

**Correction**: 7.1/7.2 were initially marked done based on the `sync_test` confirmation above, but that test ran against the repo checkout (`spawner.py`'s default `XSTUDIO_PYTHON_PLUGIN_PATH`, never repointed at `dist/`), not the packaged `--install-app` bundle 7.1/7.2 are specifically about. The only thing ever run against the packaged distribution was the automated `env -i` module-load simulation (not a real GUI, not `sync_test`) plus one manual GUI launch *before* the `_vendor`/`__pycache__` fixes (the one that surfaced the original bug). 7.1/7.2 reverted to open pending an actual `--install-app` + real xStudio GUI + two-peer session, post-fix.

**Bug found and fixed during a first live attempt at 7.1** (real xStudio, `dist/ori-sync-xstudio` on `XSTUDIO_PYTHON_PLUGIN_PATH`): `ori_sync` loaded and registered `SyncEvent` correctly, then the console showed `Failed to load plugin __pycache__ ... AttributeError: module '__pycache__' has no attribute 'create_plugin_instance'`. Root cause: loading `_ori_bootstrap.py` via `importlib.util.spec_from_file_location(...).loader.exec_module(...)` goes through `SourceFileLoader`, which writes a bytecode cache next to the source file as an ordinary import side effect — and `_ori_bootstrap.py` deliberately sits at the top of the search folder specifically so xStudio's scan skips it, so the `__pycache__/` that load created *was* a stray top-level directory, and the scan choked on it exactly as designed.

Fixed in both `xstudio_plugin/ori_sync/utils.py` and `xstudio_plugin/ori_annotations/ori_annotations.py`: the loader now reads and `exec()`s the bootstrap's source directly instead of going through `SourceFileLoader`, which never touches bytecode caching.

**Two more bugs surfaced re-verifying that fix against a real two-xStudio-peer `sync_test` run**, not caught by the module-load simulation above:

1. **`_vendor/` was itself a stray top-level directory.** D3 rule 3 (the dev pika cache) originally lived at `xstudio_plugin/_vendor/` — a *sibling* of `ori_sync/`/`ori_annotations/`, i.e. exactly the top level xStudio's scanner probes. `_vendor` (a namespace package, no `__init__.py`) got scanned and failed: `Failed to load plugin _vendor ... AttributeError`. Same bug class as the `__pycache__` one, self-inflicted the same way. Fixed by nesting it at `xstudio_plugin/ori_sync/_vendor/`, matching where `_ori_payload` already lives. Packaging also had to stop the wholesale `ori_sync/` copy from picking it up (was shipping pika twice).
2. **The `__pycache__` fix was incomplete.** `tests/xstudio_plugin/test_ori_bootstrap.py`'s own test helper, plus three subprocess-isolated tests, still used the old `SourceFileLoader` pattern — against the *real* `xstudio_plugin/_ori_bootstrap.py`, not a copy. Every test run was silently recreating the exact landmine in the live checkout. All four loaders in the test file rewritten to match production.

Added `test_dev_vendor_dir_resolves_and_stays_nested_under_ori_sync`, `test_repo_checkout_search_folder_top_level_contains_only_the_two_plugins` (pins the live checkout's shape directly — the configuration that actually broke, which no packaging test could reach), and `test_staged_tree_does_not_carry_the_dev_vendor_cache`. Re-verified by simulating xStudio's exact `iterdir()` scan against **both** a freshly staged `dist/ori-sync-xstudio` and the live `xstudio_plugin/` checkout, under `env -i` — clean in both, confirmed the checkout stays clean after a full test suite run. Full suites: 117/117, 724/724+4 subtests. User then confirmed live: 7.1 passes, and a single automated two-xStudio-peer `sync_test` passes.
