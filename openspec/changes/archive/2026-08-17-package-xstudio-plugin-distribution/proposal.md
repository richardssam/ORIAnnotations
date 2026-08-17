## Why

Installing the two xStudio plugins currently costs a studio three environment variables and a `pip install`:

```bash
export XSTUDIO_PYTHON_PLUGIN_PATH=$REPO/xstudio_plugin
export PYTHONPATH=$REPO/python:$PYTHONPATH
export OTIO_PLUGIN_MANIFEST_PATH=$REPO/otio_event_plugin/plugin_manifest.json
pip install pika        # into whichever interpreter xStudio uses
```

Only the first buys anything xStudio cannot get another way. The other two exist because the plugins locate their own payload by walking `__file__` up to a **repo checkout** — `xstudio_plugin/ori_sync/utils.py:13-28` and the near-identical block in `xstudio_plugin/ori_annotations/ori_annotations.py:24-40` both compute `_repo_root` as two/three levels up, then inject `python/` and `otio_event_plugin/`. The READMEs describe `PYTHONPATH` and `OTIO_PLUGIN_MANIFEST_PATH` as "recommended … to avoid ordering issues", which is an admission that the self-bootstrap is load-order-fragile rather than a real requirement. Worse, the manifest bootstrap works by *appending to* `OTIO_PLUGIN_MANIFEST_PATH`, which is the documented cause of the double-load bug where `SyncEvent` is registered twice and every `isinstance()` against it silently returns `False`.

The result is that there is no artifact to hand a studio. There is a repo, and a paragraph of shell. This blocks the plugin being dropped into an xStudio app bundle (`<app>/Contents/Resources/plugin-python/`, which xStudio scans unconditionally via `XSTUDIO_LOCAL_PLUGIN_PATH`) — the lowest-effort install path available.

This is TODO.md:85 (*"README says it needs a OTIO_PLUGIN_MANIFEST_PATH for xstudio. Can we make it part of the plugin."*).

## What Changes

- A **single self-contained folder tree** becomes the distribution artifact. Its top level is shaped like an xStudio plugin *search folder*, so it can be copied to a studio-chosen location **or** have its contents copied straight into an app bundle's `plugin-python/`. How a studio points xStudio at it stays the studio's business; the README suggests defaults but the package does not depend on any particular route.
- **Dependency payload moves inside the tree.** `otio_sync_core`, `ORIAnnotations`, the `otio_event_plugin` manifest + schemadefs, and a pip-vendored `pika` ship inside the package. `opentimelineio` is deliberately *not* vendored — xStudio bundles 0.18.1 already, and that is the version known to work.
- **Payload placement is constrained by xStudio's scanner, not by taste.** `Connection.load_plugins_in_path` probes *every* top-level subdirectory of a search folder as a plugin and prints a `Failed to load plugin …` traceback for each one that is not. So no bare `pika/` or `otio_sync_core/` directory may appear at the top level. Payload lives inside a plugin package directory; the resolver that finds it is a top-level *file* (files are never enumerated by the scan).
- **The OTIO schemadef registers through the OTIO API**, not an environment variable: `ActiveManifest().extend(manifest_from_file(...))`, guarded by `available_schemadef_names()` so it is idempotent. This is order-independent and retires the double-load failure mode rather than working around it.
- **Live development keeps working with no packaging step.** The same resolver detects the repo layout and falls back to `<repo>/python` + `<repo>/otio_event_plugin`, so a checkout remains directly usable as a search folder exactly as today. Packaging is only needed to produce a hand-off artifact.
- A packaging script stages the tree by **globbing** `python/otio_sync_core/`, not by enumerating modules.
- **Both plugin READMEs are rewritten around the folder, and their stale content removed.** `xstudio_plugin/ori_sync/README.md` carries a ~100-line "xStudio build + required patches" section describing the local `xstudio_sync_fixes` branch and five commits to rebase; those changes are upstream and the section is now actively misleading. Its three "Known limitations" are each contradicted by the current code: `_apply_remote_annotation` / `"draw_remote"` appears nowhere outside the README, `aspect_half` is computed per-media by `python/otio_sync_core/coords.py:51` rather than hardcoded to `0.8889`, and `RabbitMQNetwork` gained `amqps://` / `SSLOptions` support (`python/otio_sync_core/rabbitmq_network.py:53-90`). Installation instructions in both READMEs collapse to placing one folder.

## Capabilities

### New Capabilities

- `xstudio-plugin-packaging`: the distributable tree layout, the payload-resolution order (dist → optional dev vendor dir → repo checkout), the constraint that only plugin package directories may appear at a search folder's top level, the vendoring policy (pika yes, OTIO no), and the packaging script's contract.

### Modified Capabilities

- `xstudio-plugin-module-structure`: adds the top-level bootstrap module to the layout, and replaces the two ad-hoc `sys.path`/`OTIO_PLUGIN_MANIFEST_PATH` bootstrap blocks with a single shared resolver invoked by both plugins.
- `xstudio-annotation-export`: the exporter plugin SHALL resolve `ORIAnnotations` and the `SyncEvent` schemadef through the shared resolver rather than its own `__file__`-relative repo-root walk.
- `xstudio-plugin-packaging` additionally carries a requirement that the install documentation describe the folder and stay consistent with the code — the trigger for this change being READMEs that had drifted far enough to describe a build procedure and three limitations that no longer exist.

## Impact

- **Code**: new `xstudio_plugin/_ori_bootstrap.py`; `xstudio_plugin/ori_sync/utils.py` and `xstudio_plugin/ori_annotations/ori_annotations.py` lose their bootstrap blocks and call the resolver; new packaging script.
- **Docs**: both plugin READMEs rewritten around the folder rather than the variables; the `xstudio_sync_fixes` build section and the three stale "Known limitations" removed; `TODO.md:85` closed. The build section's *surviving* content — `xstudio_plugin/ori_sync/README.md:77-115`, "Acquire the playhead from the viewport, never from `current_playhead()`" — migrates to `docs/xstudio_constraints.md` **before** deletion. It is the only part of that section not already covered there (the constraints doc has `subscribe_to_playhead_events` at :49 and the `70aaaa3f` / per-subscription-listeners state at :409, but no mention of `_viewport_playhead`, `global_active_playhead_`, or the `DummyPlayhead` trap), and it documents a failure that silently kills playback sync while selection sync keeps working and masks it.
- **Protocol**: none. No wire-format, message, or timeline change.
- **Runtime behaviour**: none intended. The plugins import the same modules from the same code; only *how those modules are located* changes.
- **No patched xStudio build is required.** The changes the plugin depends on (PRs #299 / #302 / #303) are upstream — `xstudio` is on `develop` at `271c87c3`, which contains `70aaaa3f Fix python actor event group callback routing`. The package targets a stock build; the README states a version floor rather than a rebase recipe.
- **Explicitly out of scope**: writing to `~/.config/DNEG/xstudio/preferences/application.json` (xStudio rewrites that file wholesale on exit, so an installer that merges into it races the app), and converging the RV packager (`rvplugin/ori_sync/makepackage.csh`) onto the shared staging step. The RV packager's hand-maintained module list has caused two silent-inert-plugin incidents; this change avoids inheriting that trap by globbing, and leaves converging the two packagers to a follow-up.
- **Testing**: unit coverage that the resolver picks the right roots under both layouts and that schemadef registration is idempotent; a packaging smoke test that the staged tree contains exactly two directories at its top level. Live verification is a `cp -R` into an app bundle followed by a two-peer session with no env vars set.
