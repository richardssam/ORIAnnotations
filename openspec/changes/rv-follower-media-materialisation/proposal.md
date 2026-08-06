## Why

An OpenRV peer that receives media as part of a **whole-timeline** structural
message does not create the RV source group for it. The clip is present in the
peer's OTIO — `target_url` and all — but `_path_to_source_group_map()` has no
entry, so nothing can display it.

The gap is in how sources get materialised. `plugin.py`'s `on_hierarchy_changed`
calls `rv.commands.addSource(...)` for a clip arriving via **`INSERT_CHILD`**,
and `sequence.rebuild_rv_session()` covers the **snapshot** a peer applies on
join. Media that arrive by `REPLACE_TIMELINE` — which is how an RV master pushes
structure, via `check_otio_snapshots` → `broadcast_replace_timeline` — fall
between the two: the timeline is replaced wholesale, no per-child insert fires,
and no rebuild is triggered.

Found by `openrv_hosts_selection` (added by `host-owned-visibility` §6.3), where
the host isolates onto `graphic_ACES_sRGB.mov` and the follower reports:

```
MIRROR FAILED: host's clip 2e5f1ec8 (…/graphic_ACES_sRGB.mov) has no local source group
```

**This is pre-existing, not caused by `host-owned-visibility`.** That change only
made it legible: `_switch_to_source_view` used to log the miss and return,
leaving the follower on whatever it was already showing. Two peers then reported
the same timeline name while displaying different media — the silent divergence
`host-owned-visibility` D4 exists to eliminate. The failure is newly *visible*,
not newly *present*.

It has gone unnoticed because the existing RV↔RV tests do not need it: they
either assert structure only (`delete_media_openrv`, `otio_import_rv_to_rv`) or
put xStudio on the receiving side, where media materialisation is xStudio's own.
A follower being asked to *display* a clip that arrived by whole-timeline
replace is a path only the new host-owned test exercises.

## What Changes

- Materialise missing sources when a timeline arrives by `REPLACE_TIMELINE`,
  covering the same ground `on_hierarchy_changed` covers for `INSERT_CHILD`.
- Prefer one shared "ensure every clip in this timeline has a source group"
  operation over a third hand-written copy of the walk — the three entry points
  (insert, replace, snapshot rebuild) should not be able to drift on what
  counts as materialised.
- Decide whether an unmaterialisable clip (media genuinely absent from this
  machine) is reported the way `host-owned-visibility` reports an unmirrorable
  view, rather than silently absent.

## Capabilities

### Modified Capabilities
- `openrv-sync-plugin`: source-group materialisation covers whole-timeline
  replacement, not only child inserts and join-time rebuilds.

## Impact

- `rvplugin/ori_sync/plugin.py`: the `replace_timeline` action path.
- `rvplugin/ori_sync/sequence_sync.py`: the shared materialisation operation.
- `sync_test/sync_tests.yaml`: unblocks `openrv_hosts_selection`, currently
  `known_broken` against this change.
