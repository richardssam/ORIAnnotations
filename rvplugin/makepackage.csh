#!/bin/bash

rm oriannotations.zip
zip oriannotations.zip ori_annotations_plugin.py PACKAGE 
cd ..
# Adding the files that are effectively ../python and ../otio_event_plugin so we dont have to assume symlinks or anything.
zip rvplugin/oriannotations.zip python/ORIAnnotations.py otio_event_plugin/* otio_event_plugin/schemadefs/* PACKAGE 

