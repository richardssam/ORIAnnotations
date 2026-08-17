# Design — Single-folder xStudio plugin distribution

## Context

The design is almost entirely dictated by two pieces of xStudio behaviour, both in
`xstudio/python/src/xstudio/connection/__init__.py`. Everything else follows from them.

### Discovery: four routes, all equivalent to us

```
Connection.load_python_plugins()                       (:548)
  ├─ 1. $XSTUDIO_PYTHON_PLUGIN_PATH
  ├─ 2. XSTUDIO_LOCAL_PLUGIN_PATH   = <app>/Contents/Resources/plugin-python
  │                                   always scanned, needs no configuration
  ├─ 3. pref /core/python/python_plugin_search_folders
  └─ 4. pref /core/python/user_python_plugin_search_folders
            (GUI: Preferences → Python → "Python Plugins Search Paths";
             values pass through os.path.expandvars)
```

All four end at `load_plugins_in_path(search_folder)`. **The package therefore targets
"a search folder", not any one route.** A studio picks the route; route 2 is the
zero-configuration one and is what the README will recommend.

### The scan, and why it dictates the layout

```python
def load_plugins_in_path(self, path):          # :580
    for p in Path(path).iterdir():
        if p.is_dir():                          # ← FILES ARE NEVER TOUCHED
            self.load_plugin_from_path(path, p.name)

def load_plugin_from_path(self, path, plugin_name):   # :598
    try:
        sys.path.insert(0, path)                # ← search folder joins sys.path
        spec = importlib.util.find_spec(plugin_name)
        if spec is not None:
            ...
            plugin_instance = module.create_plugin_instance(self)   # ← raises here
        else:
            print("Error loading plugin ... not python importable.")
    except Exception as e:
        print("Failed to load plugin {0} from location {1} with error {2}")
        print(traceback.format_exc())
```

Three consequences, in order of how much they constrain us:

1. **Every top-level subdirectory gets probed, and there is no quiet failure path.**
   A directory that is not a plugin either hits the `else` branch or raises
   `AttributeError: create_plugin_instance` — both print, one with a full traceback.
   Hidden (`.`-prefixed) names do not help; `iterdir()` yields them and `find_spec`
   fails on them. So: **the top level of the tree may contain plugin directories and
   nothing else.**
2. **Files at the top level are invisible to the scan but importable**, because the
   search folder is inserted into `sys.path`. This is the escape hatch the layout uses.
3. **Subdirectories of a plugin package are never enumerated.** The scan only reads the
   top level. So a payload directory nested inside `ori_sync/` is completely inert.

## Decisions

### D1 — Layout

```
ori-sync-xstudio/                       ← copy this dir to a search folder location,
│                                          OR copy its *contents* into an app bundle's
│                                          Contents/Resources/plugin-python/
├── ori_sync/                           ← plugin (scanned, valid)
│   ├── __init__.py  ori_sync_plugin.py  utils.py  …
│   ├── qml/ORISyncPlugin.1/            ← already relocatable: plugin_base.py:88
│   │                                     "This allows plugins with qml to be relocatable"
│   └── _ori_payload/                   ← inert: inside a package, never enumerated
│       ├── otio_sync_core/
│       ├── ORIAnnotations.py
│       ├── pika/                       ← pip-vendored
│       └── otio_event_plugin/
│           ├── plugin_manifest.json    ← must stay a real file on disk (see D4)
│           └── schemadefs/SyncEvent.py
├── ori_annotations/                    ← plugin (scanned, valid)
│   ├── __init__.py  ori_annotations.py
│   └── qml/ORIAnnotationsExporter.1/
├── _ori_bootstrap.py                   ← FILE → skipped by the scan, importable
├── README.md
└── VERSION
```

Exactly two directories at the top level, so the scan is silent. `README.md`, `VERSION`
and `_ori_bootstrap.py` are files and cost nothing.

**Why the payload lives under `ori_sync/` rather than being duplicated into both
plugins:** two copies of `otio_event_plugin/plugin_manifest.json` on disk invites two
registrations of the `SyncEvent` schemadef — the exact double-load failure this change
is trying to close. One payload, one manifest, one registration.

The cost is that `ori_annotations` shipped *alone* would have no payload. Accepted: the
two plugins ship together, and the resolver's candidate list (D3) makes a
`ori_annotations/_ori_payload/` work if that ever changes.

### D2 — `_ori_bootstrap.py`, loaded by path

Both plugins today carry near-identical ~20-line bootstrap blocks. Those collapse into
one module that exposes a single `ensure_payload()` — idempotent, safe to call from both
plugins in either load order.

It is loaded **by filesystem path**, via `spec_from_file_location` against
`<plugin_dir>/../_ori_bootstrap.py`, *not* by `import _ori_bootstrap`.

Rationale: an ordinary import would work under xStudio (the search folder is on
`sys.path` by then) but not for the parts of `tests/` that put the *plugin* directory on
`sys.path` rather than the search folder — e.g. `tests/otio_sync/test_xstudio_annotations.py:8`
appends `xstudio_plugin/ori_sync`. Path-based loading is independent of who put what on
`sys.path` and costs about six lines.

Rejected alternative: having `ori_annotations` do `from ori_sync import _payload`. Any
import of `ori_sync` executes `ori_sync/__init__.py`, which pulls in the whole plugin and
the xStudio API; xStudio then re-executes the package into a fresh module object during
its own scan. Double execution of the plugin package, for a path lookup.

### D3 — Payload resolution order

`ensure_payload()` walks candidate roots and adds every one that exists to `sys.path`:

```
1. <search_folder>/ori_sync/_ori_payload/     ← distribution
2. <search_folder>/<this_plugin>/_ori_payload/ ← a self-contained single plugin
3. <search_folder>/_vendor/                    ← optional dev vendor dir (pika only)
4. <repo_root>/python/  +  <repo_root>/otio_event_plugin/   ← live development
```

Rule 4 is what preserves today's workflow: a checkout stays directly usable as a search
folder with no packaging step. Rules 1 and 4 are the two that matter; 2 and 3 are cheap
insurance.

`_vendor/` (rule 3) exists so a developer can get `pika` without polluting xStudio's
bundled interpreter — `make_xstudio_package.sh --vendor-only` populates it, and it is
gitignored. Continuing to `pip install pika` into the bundled interpreter stays valid.

The manifest is the **first** `otio_event_plugin/plugin_manifest.json` found while walking
the list — never all of them.

### D4 — Register the schemadef through the OTIO API

Replace the `os.environ["OTIO_PLUGIN_MANIFEST_PATH"] += …` mutation with:

```python
import opentimelineio as otio
if "SyncEvent" not in otio.schema.schemadef.available_schemadef_names():
    otio.plugins.ActiveManifest().extend(
        otio.plugins.manifest.manifest_from_file(manifest_path)
    )
```

Verified against the OTIO 0.18.1 that ships inside xStudio
(`vcpkg_installed/arm-osx/lib/python3.11/site-packages/opentimelineio/`):

- `plugins/manifest.py:134` `Manifest.extend()` and `:327` `ActiveManifest()` are public.
- `manifest_from_file` (`:46`) calls `_update_plugin_source(absfilepath)`, so the
  manifest's relative `"filepath": "schemadefs/SyncEvent.py"` resolves against the
  manifest's own location. This is why the manifest must remain a real file on disk and
  cannot be zipped into the payload.
- `schema/schemadef.py:85` `available_schemadef_names()` gives the idempotence guard.

Two properties the env var never had: it is **order-independent** (works even if
something already built the active manifest), and it is **idempotent** (a second plugin,
or a second load, cannot double-register).

Scope note: the RV plugin and `tests/` keep their existing `OTIO_PLUGIN_MANIFEST_PATH`
handling. This change touches the xStudio bootstrap only.

### D5 — Vendoring policy

| Dependency | Ships in package? | Why |
|---|---|---|
| `pika` | **Yes** | Confirmed absent from xStudio's bundled Python 3.11 (`import pika` → `ImportError`). Pure Python, so a `pip install --target` copy is interpreter-portable. Same technique as `rvplugin/ori_sync/makepackage.csh:5-20`. |
| `opentimelineio` | **No** | Compiled extension — not portable by copying. Already bundled at 0.18.1, which is the version that *works*: 0.19.0.dev1 throws `bad any cast` on nested metadata and on every `deepcopy`. Shipping our own is a way to regress that by accident. |
| `otio_sync_core`, `ORIAnnotations`, `otio_event_plugin` | **Yes** | Ours; pure Python. |

The staging step **globs** `python/otio_sync_core/` rather than listing modules. The RV
packager's hand-maintained list has twice produced a silently inert plugin (a missing
module makes `__init__.py`'s eager imports raise, and the bare `except ImportError`
swallows it). Not repeating that here.

### D6 — Packaging script

`make_xstudio_package.sh`, staging into `dist/ori-sync-xstudio/`:

| Mode | Effect |
|---|---|
| *(no arguments)*, `--help` | Print usage, exit 0. Does nothing else. |
| `--package` | Stage the full tree, then archive it into `dist/ori-sync-xstudio-<version>.zip`. |
| `--vendor-only` | Populate `xstudio_plugin/ori_sync/_vendor/` for live development; do not stage. |
| `--install-app <path/to/xSTUDIO.app>` | Stage, then `cp -R` the tree's *contents* into `Contents/Resources/plugin-python/`. |
| `--install-dir <path>` | Stage, then `cp -R` the tree's *contents* into an arbitrary folder, creating it if needed. |
| `--verify-only <dir>` | Internal — run just the top-level guard against an arbitrary directory. Used by tests to exercise the guard without duplicating its logic. |

The script has real side effects on first use — a `pip install` to vendor `pika`,
file writes — so no-arguments is deliberately *not* an alias for the previous default
staging behaviour. It shows help and exits cleanly instead; every action requires an
explicit flag.

`--install-app` is the whole point of the app-bundle route: install reduces to one
command and zero configuration. It must be re-run after an xStudio rebuild, since
`cmake --build --target install` repopulates that directory — the README says so plainly.

`--install-dir` exists for sites that maintain one plugin folder shared across
multiple xStudio builds or versions, decoupled from any single app bundle — the
mirror image of `--install-app`'s per-bundle install, sharing the same `install_into()`
helper and the same `verify_top_level` guard on the staged tree before it ever reaches
the destination. Unlike `--install-app` (which validates the target is a real bundle by
requiring `Contents/Resources/plugin-python` to already exist), `--install-dir` creates
its target unconditionally — the entire point is a location that need not exist yet.

### D7 — Deliberately not done

- **Writing the search path into user preferences.** xStudio rewrites
  `~/.config/DNEG/xstudio/preferences/application.json` wholesale on exit, so an installer
  that merges a key into it races the running app. The settings UI already exposes the
  field; a README line beats a racy file writer.
- **A `preferences/` folder in the package.** `global_store.cpp:87-99` merges
  `$XSTUDIO_PLUGIN_PATH/*/preferences/*.json` as *default* preferences, which would let
  the package declare its own search path. It needs `XSTUDIO_PLUGIN_PATH` set to work —
  trading one environment variable for another — and it is a bootstrap loop. Noted in
  case the app-bundle route ever proves insufficient.
- **Zipping the payload.** Attractive (a `.zip` is a file, so the scan ignores it, and
  `zipimport` handles pure Python), but D4 shows the manifest and `SyncEvent.py` must be
  real files. A split payload — some zipped, some not — is more machinery than a nested
  directory that the scanner already ignores.

## Risks

| Risk | Mitigation |
|---|---|
| A future xStudio change makes `load_plugins_in_path` recurse, or enumerate files | The "two directories at the top level" property is asserted by a packaging test, so the shape is at least pinned on our side. Re-check the scanner when moving to a newer xStudio. |
| Vendored `pika` drifts from the version the RV package vendors, so the two hosts run different clients | Pin the version in one place and have both packagers read it. Cheap now, annoying later. |
| The resolver silently picks the wrong root (e.g. a stale `_ori_payload` shadowing a checkout during development) | `ensure_payload()` logs the roots it selected at startup, in the same style as the RV plugin's banner — which is what made the RV vendoring bugs diagnosable. |
| Behaviour change smuggled in via import-order differences | Nothing about *which* modules get imported changes, only how they are located. The two-peer sync suite is the check. |

### D8 — `VERSION` is stamped and logged, not broadcast

Packaging writes a `VERSION` file; `ensure_payload()` logs it at startup alongside the
payload roots it selected. Nothing on the wire changes and peers do not compare versions.

A mixed-version session therefore stays invisible on the wire, but is one `grep` away in
each peer's log — which is the diagnostic that matters, and it costs nothing. Exchanging
the version during the session handshake would be genuinely useful once studios
self-install, but it is protocol work and does not belong in a packaging change. Left as a
follow-up.

### D9 — The README states the xStudio floor as `develop`, for now

The plugin's dependencies (PRs #299 / #302 / #303) are upstream but not in a tagged
release, so the README says a build from `develop` is required and names nothing more
precise. This is honest today and knowingly temporary: it is replaced with a version number
once the first release containing those changes ships.

Because that is a known-temporary line, it gets a `TODO.md` entry rather than relying on
someone noticing. Naming the merge commit (`70aaaa3f`) was considered and rejected — it is
checkable only by someone with a checkout, and meaningless to a studio running a packaged
xStudio.

## Open questions

None outstanding. See D8 and D9 for the two that were resolved.
