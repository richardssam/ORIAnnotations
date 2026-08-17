#!/bin/bash
# Package the xStudio ORI Sync / ORI Annotations plugins as a single
# self-contained folder tree — no environment variables, no `pip install`
# into xStudio's interpreter required to use it.
#
# See openspec/changes/package-xstudio-plugin-distribution/design.md for the
# layout rationale (D1) and why the top level may contain plugin package
# directories only (xStudio's plugin scan has no quiet failure path for
# anything else it finds there — confirmed live: a vendor cache that used to
# sit at xstudio_plugin/_vendor/, a sibling of ori_sync/ and ori_annotations/,
# got scanned as a plugin and logged an error at every startup. It now nests
# under xstudio_plugin/ori_sync/_vendor/, same as _ori_payload/.)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$REPO_ROOT/xstudio_plugin/ori_sync/_vendor"
DIST_ROOT="$REPO_ROOT/dist"
STAGE_DIR="$DIST_ROOT/ori-sync-xstudio"

print_help() {
    cat <<'EOF'
Package the xStudio ORI Sync / ORI Annotations plugins as a single
self-contained folder tree — no environment variables, no `pip install`
into xStudio's interpreter required to use it.

Usage:
  make_xstudio_package.sh --package
      Stage the tree and zip it into dist/ori-sync-xstudio-<version>.zip.
      This is the plain build-an-artifact-to-hand-someone action.

  make_xstudio_package.sh --install-app PATH
      Stage, then install into an xSTUDIO.app bundle's own plugin directory
      (PATH is the .app itself; xStudio scans that directory unconditionally,
      so this needs no further configuration). Re-run after every rebuild —
      `cmake --build --target install` repopulates that directory and wipes
      what was installed there.

  make_xstudio_package.sh --install-dir PATH
      Stage, then install into an arbitrary folder of your choosing. Use this
      for a persistent, version-independent plugin location shared across
      multiple xStudio builds — point `XSTUDIO_PYTHON_PLUGIN_PATH` or the
      xStudio Preferences → Python → "Python Plugins Search Paths" field at
      PATH once, then just re-run this after every plugin change. PATH is
      created if it doesn't exist yet. Use a folder dedicated to this plugin
      rather than one shared with unrelated third-party xStudio plugins.

  make_xstudio_package.sh --vendor-only
      Populate the dev pika cache (xstudio_plugin/ori_sync/_vendor/) only,
      without staging anything. Lets a repo checkout used directly as a
      search folder (no packaging step) still have pika available.

  make_xstudio_package.sh --help
      Show this help.

Running with no arguments does the same as --help. This script has real
side effects on first use — a `pip install` to vendor pika, file writes —
so it never acts without an explicit mode.

See openspec/changes/package-xstudio-plugin-distribution/design.md for the
layout rationale.
EOF
}

MODE=""
INSTALL_APP_PATH=""
INSTALL_DIR_PATH=""
VERIFY_DIR=""

if [ $# -eq 0 ]; then
    print_help
    exit 0
fi

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --package)
            MODE="package"
            shift
            ;;
        --vendor-only)
            MODE="vendor-only"
            shift
            ;;
        --install-app)
            MODE="install-app"
            INSTALL_APP_PATH="${2:?--install-app requires a path to an xSTUDIO.app bundle}"
            shift 2
            ;;
        --install-dir)
            MODE="install-dir"
            INSTALL_DIR_PATH="${2:?--install-dir requires a destination folder path}"
            shift 2
            ;;
        --verify-only)
            # Internal: run just the top-level guard against an arbitrary
            # directory. Used by tests/xstudio_plugin/test_packaging.py to
            # exercise the real guard against a deliberately corrupted tree
            # without duplicating its logic in the test.
            MODE="verify-only"
            VERIFY_DIR="${2:?--verify-only requires a directory to check}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

version() {
    git -C "$REPO_ROOT" describe --always --dirty 2>/dev/null \
        || git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null \
        || echo "unknown"
}

# pika is pure Python, so a pip --target copy is portable across interpreters
# (mirrors rvplugin/ori_sync/makepackage.csh's vendoring of the same
# dependency for the RV package). opentimelineio is deliberately never
# vendored here — xStudio bundles a working copy; see design.md D5.
vendor_pika() {
    if [ -f "$VENDOR_DIR/pika/__init__.py" ]; then
        echo "Using existing vendored pika/ ($VENDOR_DIR/pika)"
        return
    fi
    echo "Vendoring pika into $VENDOR_DIR ..."
    rm -rf "$VENDOR_DIR/_pika_tmp"
    mkdir -p "$VENDOR_DIR"
    pip install --target "$VENDOR_DIR/_pika_tmp" pika
    if [ ! -d "$VENDOR_DIR/_pika_tmp/pika" ]; then
        echo "ERROR: could not vendor pika (pip install failed)" >&2
        exit 1
    fi
    rm -rf "$VENDOR_DIR/pika"
    cp -R "$VENDOR_DIR/_pika_tmp/pika" "$VENDOR_DIR/pika"
    rm -rf "$VENDOR_DIR/_pika_tmp"
    echo "Vendored pika via pip"
}

# Copy a directory tree, pruning Python/OS cruft that would otherwise ride
# along (__pycache__, .pyc, .DS_Store).
copy_clean() {
    local src="$1" dst="$2"
    mkdir -p "$dst"
    cp -R "$src/." "$dst/"
    find "$dst" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    find "$dst" -name "*.pyc" -delete
    find "$dst" -name ".DS_Store" -delete
}

stage() {
    echo "Staging into $STAGE_DIR ..."
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"

    # Plugin packages — copied whole (including qml/), not enumerated file by
    # file. See design.md D5 for why the payload below is globbed rather than
    # hand-listed: a hand-maintained module list that falls behind produces a
    # plugin that fails to import silently, not loudly.
    copy_clean "$REPO_ROOT/xstudio_plugin/ori_sync" "$STAGE_DIR/ori_sync"
    copy_clean "$REPO_ROOT/xstudio_plugin/ori_annotations" "$STAGE_DIR/ori_annotations"
    # The payload lives under ori_sync/, never duplicated into both plugins —
    # two copies of plugin_manifest.json on disk is how SyncEvent ends up
    # registered twice. See design.md D1.
    rm -rf "$STAGE_DIR/ori_sync/_ori_payload" "$STAGE_DIR/ori_annotations/_ori_payload"
    # ori_sync/_vendor/ (D3 rule 3) is a *dev-only* cache for iterating
    # against a repo checkout — strip it from the wholesale copy above so the
    # distribution doesn't ship pika twice (once here, once staged into
    # _ori_payload/pika below).
    rm -rf "$STAGE_DIR/ori_sync/_vendor"

    cp "$REPO_ROOT/xstudio_plugin/_ori_bootstrap.py" "$STAGE_DIR/_ori_bootstrap.py"

    local payload="$STAGE_DIR/ori_sync/_ori_payload"
    mkdir -p "$payload"
    copy_clean "$REPO_ROOT/python/otio_sync_core" "$payload/otio_sync_core"
    cp "$REPO_ROOT/python/ORIAnnotations.py" "$payload/ORIAnnotations.py"
    copy_clean "$REPO_ROOT/otio_event_plugin" "$payload/otio_event_plugin"

    vendor_pika
    copy_clean "$VENDOR_DIR/pika" "$payload/pika"

    version >"$STAGE_DIR/VERSION"

    cat >"$STAGE_DIR/README.md" <<EOF
# ORI Sync — xStudio Plugins (packaged $(cat "$STAGE_DIR/VERSION"))

Self-contained install: copy this folder's **contents** into a directory
xStudio scans for Python plugins, or point xStudio at this folder itself.
No environment variables and no \`pip install\` into xStudio's interpreter
are required.

## Suggested install (recommended default)

Copy the contents of this folder into your xStudio build's bundled plugin
directory, which xStudio scans unconditionally:

\`\`\`bash
./make_xstudio_package.sh --install-app /path/to/xSTUDIO.app
\`\`\`

Re-run this after rebuilding xStudio — \`cmake --build --target install\`
repopulates that directory.

## Alternative install routes (equally valid)

- \`./make_xstudio_package.sh --install-dir /path/to/a/persistent/folder\` —
  a location of your choosing that survives across xStudio rebuilds and
  versions, rather than being tied to one app bundle. Then either:
  - \`export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/that/folder\`, or
  - xStudio Preferences → Python → "Python Plugins Search Paths" — add the
    path in the settings UI (persists across restarts, no environment
    variable needed)

## Requirements

- An xStudio build from \`develop\` (the plugin's dependencies are upstream
  but not yet in a tagged release)
- A running RabbitMQ broker reachable on the network (default:
  \`localhost:5672\`)

Nothing else. \`opentimelineio\` is used from xStudio's own bundled copy;
\`pika\` is vendored inside \`ori_sync/_ori_payload/\`.

See \`ori_sync/README.md\` and \`ori_annotations/README.md\` for plugin
details, session connection settings, and logging.
EOF

    echo "Staged tree:"
    find "$STAGE_DIR" -maxdepth 1 -mindepth 1 -print
}

# xStudio's plugin scan probes every top-level directory as a plugin and has
# no quiet failure path (see design.md — "why the layout is forced"). A
# packaging bug that leaves a stray directory at the top level would make
# every install log errors at every startup, so fail loudly here instead.
# Takes the tree to check as an argument (rather than reading $STAGE_DIR
# directly) so tests/xstudio_plugin/test_packaging.py can exercise this exact
# guard via `--verify-only DIR` against a deliberately corrupted tree.
verify_top_level() {
    local dir="$1"
    local bad=0
    for entry in "$dir"/*/; do
        name="$(basename "$entry")"
        if [ "$name" != "ori_sync" ] && [ "$name" != "ori_annotations" ]; then
            echo "ERROR: unexpected top-level directory in staged tree: $name" >&2
            bad=1
        fi
    done
    if [ "$bad" -ne 0 ]; then
        echo "Packaging aborted: top level must contain only ori_sync/ and ori_annotations/." >&2
        exit 1
    fi
    if [ ! -d "$dir/ori_sync" ] || [ ! -d "$dir/ori_annotations" ]; then
        echo "ERROR: staged tree is missing ori_sync/ or ori_annotations/" >&2
        exit 1
    fi
}

archive() {
    local zip_path="$DIST_ROOT/ori-sync-xstudio-$(cat "$STAGE_DIR/VERSION").zip"
    rm -f "$zip_path"
    (cd "$DIST_ROOT" && zip -rq "$(basename "$zip_path")" "$(basename "$STAGE_DIR")")
    echo "Wrote $zip_path"
}

# Copy the staged tree's contents into an arbitrary destination, creating it
# if needed. Shared by --install-app (destination is a validated app-bundle
# subpath, see install_app below) and --install-dir (destination is whatever
# the caller asked for — the whole point being that it need not exist yet,
# and need not be tied to any particular app bundle or xStudio version).
install_into() {
    local target="$1"
    echo "Installing into $target ..."
    mkdir -p "$target"
    cp -R "$STAGE_DIR/." "$target/"
    echo "Installed. Contents of $target:"
    find "$target" -maxdepth 1 -mindepth 1 -print
}

install_app() {
    local app_path="$1"
    local target="$app_path/Contents/Resources/plugin-python"
    if [ ! -d "$target" ]; then
        echo "ERROR: $target not found — is $app_path an xSTUDIO.app bundle?" >&2
        exit 1
    fi
    install_into "$target"
}

case "$MODE" in
    vendor-only)
        vendor_pika
        ;;
    verify-only)
        verify_top_level "$VERIFY_DIR"
        echo "OK: $VERIFY_DIR top level contains only ori_sync/ and ori_annotations/"
        ;;
    package)
        stage
        verify_top_level "$STAGE_DIR"
        archive
        ;;
    install-app)
        stage
        verify_top_level "$STAGE_DIR"
        install_app "$INSTALL_APP_PATH"
        ;;
    install-dir)
        stage
        verify_top_level "$STAGE_DIR"
        install_into "$INSTALL_DIR_PATH"
        ;;
esac
