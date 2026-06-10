#!/bin/bash

rm -f otiosyncdemo-1.2.rvpkg
rm -rf pika

# Copy pika from the pyenv site-packages to vendor it into the package
PIKA_PATH="/Users/sam/.pyenv/versions/3.10.13/lib/python3.10/site-packages/pika"
if [ -d "$PIKA_PATH" ]; then
    cp -r "$PIKA_PATH" .
    echo "Vendored pika"
else
    echo "Warning: pika not found at $PIKA_PATH"
fi

# Zip plugin files from this directory
zip -r otiosyncdemo-1.2.rvpkg plugin.py utils.py sequence_sync.py playback_sync.py display_sync.py annotation_sync.py PACKAGE pika

# From the repo root, zip in the otio_sync_core library.
cd ../..
cd python
zip ../rvplugin/ori_sync/otiosyncdemo-1.2.rvpkg \
    otio_sync_core/__init__.py \
    otio_sync_core/network.py \
    otio_sync_core/rabbitmq_network.py \
    otio_sync_core/manager.py \
    otio_sync_core/patcher.py \
    otio_sync_core/protocol_messages.py \
    otio_sync_core/proxy.py

echo "Built otiosyncdemo-1.2.rvpkg"
