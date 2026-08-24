#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""``xstudio_plugin/_ori_bootstrap.py`` — shared payload resolution and
``SyncEvent`` schemadef registration.

Covers openspec/changes/package-xstudio-plugin-distribution requirements:
repo-checkout fallback, packaged-payload precedence, reachability when only
a bare plugin directory (not the search folder) is on ``sys.path``, and that
schemadef registration is idempotent regardless of call order or whether
something already built OTIO's active manifest.

``_ori_bootstrap.py`` imports only ``os``/``sys``/``opentimelineio`` — no
``xstudio`` bindings — so, unlike most of this directory, these tests do not
require the xStudio-bundled interpreter to *run*. They are kept here anyway
because ``run_tests_xstudio.sh`` is what supplies OTIO 0.18.1, the version
this resolver targets (0.19.0.dev1 has an unrelated "bad any cast" defect —
see docs/xstudio_constraints.md — that would make any OTIO-touching test
here unreliable under the wrong interpreter).

Every helper that loads ``_ori_bootstrap.py`` in this file — including
inside the ``_run_isolated`` subprocess scripts — uses exec()/compile(),
never ``importlib``'s ``SourceFileLoader``. ``_bootstrap_path`` here is the
*real* ``xstudio_plugin/_ori_bootstrap.py``, not a copy, so
``SourceFileLoader.exec_module()`` would write a bytecode cache directly
into the live checkout on every test run — recreating, in the checkout a
developer actually points xStudio at, the exact stray top-level
``__pycache__/`` this whole change exists to prevent. Caught live: it kept
reappearing between edits until every loader in this file (not just the
production code) was brought in line.
"""
import os
import subprocess
import sys
import tempfile
import types

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_bootstrap_path = os.path.join(_repo_root, "xstudio_plugin", "_ori_bootstrap.py")
_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")


def _fresh_bootstrap(name):
    """Load an independent copy of _ori_bootstrap.py, bypassing sys.modules
    caching, so each test gets its own _DONE / _SELECTED_ROOTS state."""
    with open(_bootstrap_path, "r", encoding="utf-8") as f:
        source = f.read()
    module = types.ModuleType(name)
    module.__file__ = _bootstrap_path
    exec(compile(source, _bootstrap_path, "exec"), module.__dict__)
    return module


def test_repo_checkout_search_folder_top_level_contains_only_the_two_plugins():
    """The live-checkout counterpart to the packaging smoke test's top-level
    guard (tests/xstudio_plugin/test_packaging.py) — but this one checks the
    actual xstudio_plugin/ directory on disk, not a staged dist/ tree.

    A packaged dist/ never had this problem (the vendor cache is copied into
    ori_sync/_ori_payload/pika, not left as a sibling of ori_sync/), so
    nothing in the packaging tests could have caught it. What actually broke
    live was xStudio pointed straight at a repo checkout used for
    development, with xstudio_plugin/_vendor/ (the dev pika cache, D3 rule 3)
    sitting at the search folder's top level next to ori_sync/ and
    ori_annotations/ — exactly the shape xStudio's scanner has no quiet
    failure path for. Fixed by nesting it at
    xstudio_plugin/ori_sync/_vendor/ instead; this test pins the checkout's
    top-level shape so a future addition can't reintroduce a sibling
    directory the same way, regardless of whether a vendor cache happens to
    be populated when the test runs.
    """
    search_folder = os.path.join(_repo_root, "xstudio_plugin")
    top_level_dirs = {
        name for name in os.listdir(search_folder)
        if os.path.isdir(os.path.join(search_folder, name))
    }
    assert top_level_dirs == {"ori_sync", "ori_annotations"}, (
        f"stray top-level directory in xstudio_plugin/: "
        f"{top_level_dirs - {'ori_sync', 'ori_annotations'}}"
    )


def test_repo_checkout_resolves_to_repo_roots():
    bootstrap = _fresh_bootstrap("test_bootstrap_repo")
    bootstrap.ensure_payload(_ori_sync_dir)

    assert os.path.join(_repo_root, "python") in bootstrap._SELECTED_ROOTS
    assert os.path.join(_repo_root, "otio_event_plugin") in bootstrap._SELECTED_ROOTS
    # No packaged payload exists in a plain checkout.
    assert not any("_ori_payload" in r for r in bootstrap._SELECTED_ROOTS)


def test_packaged_payload_takes_precedence_over_repo_roots():
    with tempfile.TemporaryDirectory() as tmp:
        search_folder = os.path.join(tmp, "ori-sync-xstudio")
        ori_sync_dir = os.path.join(search_folder, "ori_sync")
        payload_dir = os.path.join(ori_sync_dir, "_ori_payload")
        os.makedirs(payload_dir)

        bootstrap = _fresh_bootstrap("test_bootstrap_packaged")
        bootstrap.ensure_payload(ori_sync_dir)

        assert bootstrap._SELECTED_ROOTS[0] == payload_dir


def test_single_plugin_payload_used_when_not_named_ori_sync():
    """D3 rule 2: a self-contained single plugin falls back to its own
    _ori_payload/ when there is no sibling ori_sync/_ori_payload/."""
    with tempfile.TemporaryDirectory() as tmp:
        search_folder = os.path.join(tmp, "ori-annotations-only")
        plugin_dir = os.path.join(search_folder, "ori_annotations")
        payload_dir = os.path.join(plugin_dir, "_ori_payload")
        os.makedirs(payload_dir)

        bootstrap = _fresh_bootstrap("test_bootstrap_single_plugin")
        bootstrap.ensure_payload(plugin_dir)

        assert payload_dir in bootstrap._SELECTED_ROOTS


def test_dev_vendor_dir_resolves_and_stays_nested_under_ori_sync():
    """D3 rule 3: the dev vendor cache (pika, when developing against a repo
    checkout) resolves to <search_folder>/ori_sync/_vendor/ — nested inside
    the plugin package, never a sibling of ori_sync/ at the top of the search
    folder. That placement isn't cosmetic: a vendor dir sitting directly at
    the search folder's top level, alongside ori_sync/ and ori_annotations/,
    is itself a stray directory xStudio's scanner probes as a plugin
    candidate and fails on — caught live as
    'Failed to load plugin _vendor ... AttributeError: module \"_vendor\" has
    no attribute \"create_plugin_instance\"' when xstudio_plugin/_vendor/ (the
    layout this resolver originally shipped with) sat at that top level."""
    with tempfile.TemporaryDirectory() as tmp:
        search_folder = os.path.join(tmp, "ori-sync-xstudio")
        ori_sync_dir = os.path.join(search_folder, "ori_sync")
        vendor_dir = os.path.join(ori_sync_dir, "_vendor")
        os.makedirs(vendor_dir)

        bootstrap = _fresh_bootstrap("test_bootstrap_vendor")
        bootstrap.ensure_payload(ori_sync_dir)

        assert vendor_dir in bootstrap._SELECTED_ROOTS
        # Not a sibling of ori_sync/ at the search folder's top level.
        assert os.path.join(search_folder, "_vendor") not in bootstrap._SELECTED_ROOTS
        top_level_dirs = {
            name for name in os.listdir(search_folder)
            if os.path.isdir(os.path.join(search_folder, name))
        }
        assert top_level_dirs == {"ori_sync"}


def test_resolver_reachable_from_a_bare_plugin_directory_on_sys_path():
    """Mirrors how tests/otio_sync/test_xstudio_annotations.py puts the
    plugin directory itself on sys.path, rather than the search folder."""
    sys.path.insert(0, _ori_sync_dir)
    try:
        bootstrap = _fresh_bootstrap("test_bootstrap_bare_syspath")
        bootstrap.ensure_payload(_ori_sync_dir)
        assert bootstrap._SELECTED_ROOTS
    finally:
        sys.path.remove(_ori_sync_dir)


def test_loading_bootstrap_does_not_create_a_top_level_pycache():
    """Regression test for a bug caught live: loading _ori_bootstrap.py via
    importlib's SourceFileLoader (spec_from_file_location + exec_module)
    writes a bytecode cache next to the source file as an ordinary import
    side effect. Since the bootstrap deliberately sits at the top of the
    search folder — the one place xStudio's plugin scan does NOT probe as a
    plugin — that __pycache__/ becomes a stray top-level directory the scan
    DOES probe on its next pass, and xStudio has no quiet failure path for
    it:

        Failed to load plugin __pycache__ from location .../ori-sync-xstudio
        AttributeError: module '__pycache__' has no attribute
        'create_plugin_instance'

    utils.py / ori_annotations.py now load the bootstrap by reading and
    exec()-ing its source directly, bypassing SourceFileLoader entirely.
    This test reproduces the same load pattern (via _fresh_bootstrap, the
    same helper every other test here uses) against a clean temp search
    folder and asserts the side effect is gone.
    """
    with tempfile.TemporaryDirectory() as tmp:
        search_folder = os.path.join(tmp, "ori-sync-xstudio")
        os.makedirs(search_folder)
        bootstrap_copy = os.path.join(search_folder, "_ori_bootstrap.py")
        with open(_bootstrap_path, "r", encoding="utf-8") as src:
            source = src.read()
        with open(bootstrap_copy, "w", encoding="utf-8") as dst:
            dst.write(source)

        module = types.ModuleType("_ori_bootstrap_pycache_check")
        module.__file__ = bootstrap_copy
        exec(compile(source, bootstrap_copy, "exec"), module.__dict__)

        top_level_entries = os.listdir(search_folder)
        assert "__pycache__" not in top_level_entries, (
            "loading _ori_bootstrap.py created a top-level __pycache__/ — "
            "this is exactly what xStudio's plugin scan chokes on"
        )
        assert top_level_entries == ["_ori_bootstrap.py"]


def _run_isolated(script):
    """Run a snippet in a fresh interpreter of the same kind running this
    test, so OTIO's process-wide ActiveManifest singleton starts empty
    regardless of what earlier tests in this session already registered."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Shared by every _run_isolated script below: load _ori_bootstrap.py via
# exec()/compile(), not SourceFileLoader — see the module docstring.
_LOAD_BOOTSTRAP_SNIPPET = f"""
import types
def load():
    with open({_bootstrap_path!r}, "r", encoding="utf-8") as f:
        source = f.read()
    m = types.ModuleType("b")
    m.__file__ = {_bootstrap_path!r}
    exec(compile(source, {_bootstrap_path!r}, "exec"), m.__dict__)
    return m
"""


def test_registration_is_idempotent_across_independent_module_instances():
    # Two fully independent bootstrap module objects register through the
    # same OTIO process — as if the two plugins had somehow ended up with
    # distinct module instances rather than sharing one via sys.modules.
    # This is what actually prevents the SyncEvent double-load bug: the
    # OTIO-level available_schemadef_names() guard, not module identity.
    script = _LOAD_BOOTSTRAP_SNIPPET + f"""
import opentimelineio as otio
a = load()
a.ensure_payload({_ori_sync_dir!r})
SyncEvent_a = otio.schema.schemadef.module_from_name("SyncEvent")

b = load()
b.ensure_payload({_ori_sync_dir!r})
SyncEvent_b = otio.schema.schemadef.module_from_name("SyncEvent")

assert SyncEvent_a is SyncEvent_b, "SyncEvent loaded as two distinct module objects"
obj = SyncEvent_a.PaintStart()
assert isinstance(obj, SyncEvent_b.PaintStart)
print("OK")
"""
    _run_isolated(script)


def test_registration_succeeds_when_active_manifest_already_built():
    script = _LOAD_BOOTSTRAP_SNIPPET + f"""
import opentimelineio as otio
otio.plugins.ActiveManifest()  # force manifest construction before the plugin loads
m = load()
m.ensure_payload({_ori_sync_dir!r})
assert "SyncEvent" in otio.schema.schemadef.available_schemadef_names()
print("OK")
"""
    _run_isolated(script)


def test_registration_succeeds_with_env_var_also_set():
    manifest_path = os.path.join(_repo_root, "otio_event_plugin", "plugin_manifest.json")
    script = f"""
import os
os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = {manifest_path!r}
""" + _LOAD_BOOTSTRAP_SNIPPET + f"""
import opentimelineio as otio
m = load()
m.ensure_payload({_ori_sync_dir!r})
assert "SyncEvent" in otio.schema.schemadef.available_schemadef_names()
print("OK")
"""
    _run_isolated(script)
