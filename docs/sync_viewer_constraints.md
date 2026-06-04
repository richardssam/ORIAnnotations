# sync_viewer — non-obvious constraints

## Running the sync_viewer

```bash
cd sync_viewer
pip install -r requirements.txt   # fastapi, uvicorn, opentimelineio
python server.py [--host localhost] [--port 8765] \
                 [--rmq-host localhost] [--rmq-port 5672] \
                 [--session otio-sync-demo]
```

Open `http://localhost:8765`. The viewer joins as a non-master passive observer. It does **not** need the RV plugin to be installed.

`server.py` requires `opentimelineio` and `otio_sync_core` on its Python path. `sys.path.insert(0, "../python")` in `server.py` handles this when run from the `sync_viewer/` directory.

## `contentDur()` — outlier-rejection for bogus clip durations

Before the EDL fix was in place, the plugin produced clips with a 10 000-frame fallback duration. The viewer's `contentDur(tl)` function in `index.html` iterates all item `(start + duration)` values and strips top-end outliers where the maximum is ≥ 10× the 25th-percentile value. This keeps the zoom-to-fit from being dominated by a single bad clip.

## Auto-refit when data changes

`autoZoomed` is a one-way latch that suppresses re-fitting after the user manually zooms. It is reset when `contentDur` changes by more than 20% between renders (tracked in `lastFitDur`), so that the view refits when corrected clip durations arrive from a plugin update.

## Annotation clips are 1 frame wide

`_persist_annotation_to_timeline` creates a `source_range` of 1 frame per stroke. At fit-zoom they appear as ~2 px amber marks in the Annotations track row. Zoom in to inspect them.
