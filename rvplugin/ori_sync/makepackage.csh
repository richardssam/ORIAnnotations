#!/bin/bash

rm -f otiosyncdemo-1.2.rvpkg

# Vendor pika into the package via pip (pika is pure python, so this is
# portable across interpreters), unless it's already present locally.
if [ ! -f "pika/__init__.py" ]; then
    rm -rf _pika_tmp
    pip install --target=_pika_tmp pika
    if [ -d "_pika_tmp/pika" ]; then
        cp -r _pika_tmp/pika .
        rm -rf _pika_tmp
        echo "Vendored pika via pip"
    else
        echo "ERROR: could not vendor pika (pip install failed)" >&2
        exit 1
    fi
else
    echo "Using existing vendored pika/"
fi

# Zip plugin files from this directory
zip -r otiosyncdemo-1.2.rvpkg plugin.py utils.py sequence_sync.py playback_sync.py display_sync.py annotation_sync.py color_sync.py PACKAGE pika

# From the repo root, zip in the otio_sync_core library.
cd ../..
cd python
# NOTE: this list is hand-maintained, and __init__.py imports these eagerly
# inside a try/except ImportError. A module added to the library but not added
# here therefore does not fail loudly — it makes `from .manager import
# SyncManager` raise, the except swallows it, and the whole sync plugin goes
# quietly inert. Add new otio_sync_core modules here in the same commit.
zip ../rvplugin/ori_sync/otiosyncdemo-1.2.rvpkg \
    otio_sync_core/__init__.py \
    otio_sync_core/authority.py \
    otio_sync_core/color.py \
    otio_sync_core/coords.py \
    otio_sync_core/shapes.py \
    otio_sync_core/rv_annotation_codec.py \
    otio_sync_core/rv_paint_applier.py \
    otio_sync_core/network.py \
    otio_sync_core/rabbitmq_network.py \
    otio_sync_core/manager.py \
    otio_sync_core/patcher.py \
    otio_sync_core/protocol_messages.py \
    otio_sync_core/proxy.py \
    otio_sync_core/state_projection.py \
    otio_sync_core/inspection.py

echo "Built otiosyncdemo-1.2.rvpkg"
