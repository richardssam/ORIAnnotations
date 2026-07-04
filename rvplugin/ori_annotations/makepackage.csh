#!/bin/bash

rm -f oriannotations.zip
zip oriannotations.zip ori_annotations_plugin.py PACKAGE
cd ../..
# Adding the files that are effectively ../../python and ../../otio_event_plugin so we dont have to assume symlinks or anything.
zip rvplugin/ori_annotations/oriannotations.zip python/ORIAnnotations.py otio_event_plugin/* otio_event_plugin/schemadefs/* PACKAGE

# Bundle the shared annotation codec so the plugin renders via the same code
# path as the batch/sync plugins. Only the leaf modules are needed: the guarded
# otio_sync_core __init__ degrades gracefully without manager/network/pika, and
# the codec has no dependency on them.
cd python
zip ../rvplugin/ori_annotations/oriannotations.zip \
    otio_sync_core/__init__.py \
    otio_sync_core/coords.py \
    otio_sync_core/shapes.py \
    otio_sync_core/rv_annotation_codec.py \
    otio_sync_core/rv_paint_applier.py

