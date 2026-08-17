#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""
Shared payload resolver for the ORI Sync xStudio plugins.

This module lives as a *file* at the top of the search folder xStudio scans
(``xstudio_plugin/`` in a repo checkout, the distribution root once packaged)
— never a directory — so xStudio's plugin scan, which probes every top-level
subdirectory as a plugin, never touches it.

It is loaded by filesystem path (see ``_load_bootstrap`` in
``ori_sync/utils.py`` and ``ori_annotations/ori_annotations.py``), not by
``import _ori_bootstrap``, because the two plugins are reached with different
things on ``sys.path``: xStudio puts the search folder there before scanning
it, but some test harnesses put a single plugin directory there instead. Path
loading works either way.

``ensure_payload(plugin_dir)`` locates the supporting payload — ``otio_sync_core``,
``ORIAnnotations``, the ``SyncEvent`` schemadef manifest, and vendored
third-party packages such as ``pika`` — and makes it importable. It tries, in
order:

1. ``<search_folder>/ori_sync/_ori_payload/``       — packaged distribution
2. ``<search_folder>/<this_plugin>/_ori_payload/``  — a self-contained single plugin
3. ``<search_folder>/ori_sync/_vendor/``             — optional dev vendor dir (pika only)
4. ``<repo_root>/python/`` + ``<repo_root>/otio_event_plugin/`` — live checkout

Rules 1 and 3 nest under ``ori_sync/`` rather than sitting at the top of the
search folder alongside it. This is not cosmetic: xStudio's plugin scan
probes every top-level *directory* of a search folder as a plugin candidate,
with no quiet failure path for one that isn't (see rule 2 of
``verify_top_level`` in ``make_xstudio_package.sh``). A ``_vendor/`` sibling
of ``ori_sync/`` — the layout this resolver shipped with initially — is
exactly such a stray directory, and was caught live: a real xStudio pointed
at a repo checkout with a populated dev vendor cache logged ``Failed to load
plugin _vendor ... AttributeError: module '_vendor' has no attribute
'create_plugin_instance'`` at every startup.

Every root that exists is added to ``sys.path``. This is what lets a
repository checkout keep working as a search folder with no packaging step:
rule 4 resolves to the repo's own ``python/`` and ``otio_event_plugin/``
directories exactly as the old per-plugin bootstrap blocks did.

The ``SyncEvent`` schemadef is registered through OTIO's manifest API
(``ActiveManifest().extend(...)``), guarded by ``available_schemadef_names()``
so a second registration — from the other plugin, or a second call from the
same one — is a no-op. This replaces mutating ``OTIO_PLUGIN_MANIFEST_PATH``,
which was both load-order-fragile and, when done twice, the cause of
``SyncEvent`` loading as two distinct module objects (after which
``isinstance`` checks against its types silently return ``False``).
"""

import os
import sys

_DONE = False
_SELECTED_ROOTS = []
_MANIFEST_PATH = None


def _candidate_roots(plugin_dir):
    """Ordered list of directories to add to ``sys.path``.

    ``plugin_dir`` is the calling plugin's own directory (e.g.
    ``.../ori_sync`` or ``.../ori_annotations``); its parent is the search
    folder xStudio scanned to find it.
    """
    search_folder = os.path.dirname(plugin_dir)
    plugin_name = os.path.basename(plugin_dir)
    repo_root = os.path.dirname(search_folder)

    roots = [os.path.join(search_folder, "ori_sync", "_ori_payload")]
    if plugin_name != "ori_sync":
        roots.append(os.path.join(search_folder, plugin_name, "_ori_payload"))
    roots.append(os.path.join(search_folder, "ori_sync", "_vendor"))
    roots.append(os.path.join(repo_root, "python"))
    roots.append(os.path.join(repo_root, "otio_event_plugin"))
    return roots


def _find_manifest(roots):
    """First ``plugin_manifest.json`` found among the selected roots.

    A root may itself contain a nested ``otio_event_plugin/`` (the packaged
    payload layout) or *be* an ``otio_event_plugin`` directory (the repo
    fallback, where it is added to ``sys.path`` directly).
    """
    for root in roots:
        nested = os.path.join(root, "otio_event_plugin", "plugin_manifest.json")
        if os.path.exists(nested):
            return nested
        direct = os.path.join(root, "plugin_manifest.json")
        if os.path.exists(direct):
            return direct
    return None


def _register_schemadef():
    global _MANIFEST_PATH
    import opentimelineio as otio

    if "SyncEvent" in otio.schema.schemadef.available_schemadef_names():
        return

    _MANIFEST_PATH = _find_manifest(_SELECTED_ROOTS)
    if _MANIFEST_PATH is None:
        print("[ORI Sync] warning: no otio_event_plugin manifest found under", _SELECTED_ROOTS)
        return

    manifest = otio.plugins.manifest.manifest_from_file(_MANIFEST_PATH)
    otio.plugins.ActiveManifest().extend(manifest)


def _read_version(plugin_dir):
    version_file = os.path.join(os.path.dirname(plugin_dir), "VERSION")
    if os.path.exists(version_file):
        with open(version_file, encoding="utf-8") as f:
            return f.read().strip()
    return None


def ensure_payload(plugin_dir):
    """Make the shared payload importable and register the SyncEvent schemadef.

    Safe to call from both plugins, in either load order. A second call is a
    no-op — sys.path only grows with roots not already present, and schemadef
    registration is itself idempotent.
    """
    global _DONE
    if _DONE:
        return
    _DONE = True

    for root in _candidate_roots(plugin_dir):
        if os.path.isdir(root):
            _SELECTED_ROOTS.append(root)
            if root not in sys.path:
                sys.path.insert(0, root)

    _register_schemadef()

    version = _read_version(plugin_dir)
    roots_display = ", ".join(_SELECTED_ROOTS) or "(none found)"
    version_display = f"  [{version}]" if version else "  [dev checkout]"
    print(f"[ORI Sync] payload roots: {roots_display}{version_display}")
    if _MANIFEST_PATH:
        print("[ORI Sync] SyncEvent manifest:", _MANIFEST_PATH)
