#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Smoke test for ``make_xstudio_package.sh``.

Runs the real packaging script (``--package``: stage + zip) and checks the
one property that matters most: xStudio's plugin scan probes every top-level
directory of a search folder and has no quiet failure path for one that
isn't a plugin (see docs / design.md for the "why the layout is forced"
account) — so a packaging regression that lets a stray directory onto the
top level would make every install log errors at every xStudio startup.

Also checks that the payload is complete: every ``otio_sync_core`` module
made it in (globbed, not hand-listed — see design.md D5) and ``pika`` was
vendored.

Runs `pip install` on first invocation (to vendor pika) unless
``xstudio_plugin/ori_sync/_vendor/pika`` is already populated from a previous
run; mark slow/network-dependent CI runs accordingly.
"""
import os
import shutil
import subprocess
import tempfile

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_script = os.path.join(_repo_root, "make_xstudio_package.sh")
_stage_dir = os.path.join(_repo_root, "dist", "ori-sync-xstudio")


def _run_packaging():
    result = subprocess.run(
        [_script, "--package"],
        cwd=_repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_staged_tree_top_level_contains_only_the_two_plugins():
    _run_packaging()
    try:
        top_level_dirs = {
            name
            for name in os.listdir(_stage_dir)
            if os.path.isdir(os.path.join(_stage_dir, name))
        }
        assert top_level_dirs == {"ori_sync", "ori_annotations"}
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_staged_payload_is_complete():
    _run_packaging()
    try:
        payload_dir = os.path.join(_stage_dir, "ori_sync", "_ori_payload")

        source_modules = {
            f for f in os.listdir(os.path.join(_repo_root, "python", "otio_sync_core"))
            if f.endswith(".py")
        }
        staged_modules = {
            f for f in os.listdir(os.path.join(payload_dir, "otio_sync_core"))
            if f.endswith(".py")
        }
        assert staged_modules == source_modules

        assert os.path.isfile(os.path.join(payload_dir, "pika", "__init__.py"))
        assert os.path.isfile(os.path.join(payload_dir, "ORIAnnotations.py"))
        assert os.path.isfile(
            os.path.join(payload_dir, "otio_event_plugin", "plugin_manifest.json")
        )
        assert os.path.isfile(
            os.path.join(payload_dir, "otio_event_plugin", "schemadefs", "SyncEvent.py")
        )
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_staged_tree_does_not_carry_the_dev_vendor_cache():
    """ori_sync/_vendor/ (D3 rule 3) is a dev-only cache for iterating
    against a repo checkout directly. The wholesale copy of ori_sync/ used
    to pick it up too, shipping pika twice — once here, once in
    _ori_payload/pika, where a real distribution should get it from. Caught
    live while re-verifying the _vendor/ relocation fix (it used to sit
    outside ori_sync/ entirely, so packaging never touched it)."""
    _run_packaging()
    try:
        assert not os.path.exists(os.path.join(_stage_dir, "ori_sync", "_vendor"))
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_no_pycache_or_ds_store_in_staged_tree():
    _run_packaging()
    try:
        for root, dirs, files in os.walk(_stage_dir):
            assert "__pycache__" not in dirs
            assert ".DS_Store" not in files
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_packaging_rejects_a_stray_top_level_directory():
    """Exercises the real guard (`--verify-only`, the same function every
    staging mode — `--package`, `--install-app`, `--install-dir` — calls
    after staging) against a deliberately corrupted tree, rather than
    duplicating its logic here."""
    _run_packaging()
    try:
        stray = os.path.join(_stage_dir, "not_a_plugin")
        os.makedirs(stray)

        result = subprocess.run(
            [_script, "--verify-only", _stage_dir],
            cwd=_repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "not_a_plugin" in result.stderr
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_verify_only_accepts_a_clean_tree():
    _run_packaging()
    try:
        result = subprocess.run(
            [_script, "--verify-only", _stage_dir],
            cwd=_repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)


def test_no_arguments_shows_help_and_does_nothing():
    """The script has real side effects (a pip install to vendor pika, file
    writes), so it must never act without an explicit mode — running it bare
    should be equivalent to --help, not a silent --package."""
    result = subprocess.run(
        [_script],
        cwd=_repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert not os.path.exists(os.path.join(_repo_root, "dist"))


def test_install_dir_creates_a_persistent_folder_independent_of_any_app():
    """A site can maintain one plugin folder shared across xStudio versions,
    decoupled from any particular app bundle — --install-dir stages into
    whatever path is given, creating it if it doesn't exist yet."""
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "does", "not", "exist", "yet")
        result = subprocess.run(
            [_script, "--install-dir", target],
            cwd=_repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

        top_level_dirs = {
            name for name in os.listdir(target)
            if os.path.isdir(os.path.join(target, name))
        }
        assert top_level_dirs == {"ori_sync", "ori_annotations"}
        assert os.path.isfile(os.path.join(target, "_ori_bootstrap.py"))

        # Re-running against the same target (the "re-run after every
        # plugin change" workflow) must not fail or accumulate cruft.
        result2 = subprocess.run(
            [_script, "--install-dir", target],
            cwd=_repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result2.returncode == 0, result2.stdout + result2.stderr
    shutil.rmtree(os.path.join(_repo_root, "dist"), ignore_errors=True)
