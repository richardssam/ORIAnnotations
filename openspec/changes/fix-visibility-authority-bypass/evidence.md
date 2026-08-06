# Soak evidence — 2026-08-06

Full logs are gitignored and local-only at `rvplugin/ori_sync/{xstudio_host,rv_client}.log`.
Session: xStudio host+master `44f3accf`, OpenRV follower `0cdfaa1f`.


## Defect 1 — follower's structure moves the host's view

### OpenRV (follower) 20:38:13–20:38:21
```
20:38:13.594 SEND playback playing=False frame=100 base=100 fps=24.0 view=sourceGroup000004 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence clip
20:38:13.594 broadcast_playback_state: stripped visibility fields (mode='sequence' clip=0090c5d3) — host is 44f3accf
20:38:16.087 SEND playback playing=False frame=119 base=100 fps=24.0 view=sourceGroup000005 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence clip
20:38:16.087 broadcast_playback_state: stripped visibility fields (mode='sequence' clip=e80a51cc) — host is 44f3accf
20:38:17.843 SEND playback playing=False frame=100 base=100 fps=24.0 view=sourceGroup000006 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence clip
20:38:17.843 broadcast_playback_state: stripped visibility fields (mode='sequence' clip=e80a51cc) — host is 44f3accf
20:38:19.018 SEND view-state [source]: clip 'graphic_ACES_sRGB' guid=0090c5d3 view=sourceGroup000001
20:38:19.018 SEND playback playing=False frame=89899 base=89899 fps=24.0 view=sourceGroup000001 tl=62812af9-e968-529d-8ab3-5798755c1973 mode=source cl
20:38:19.018 broadcast_playback_state: stripped visibility fields (mode='source' clip=0090c5d3) — host is 44f3accf
20:38:19.018 SEND view-state suppressed (not host): mode=source clip=0090c5d3 — position still sent
20:38:20.477 SEND view-state [source]: clip 'laser_ACES_sRGB' guid=a407f8c8 view=sourceGroup000002
20:38:20.489 SEND playback playing=False frame=91699 base=91699 fps=24.0 view=sourceGroup000002 tl=927c7206-17ca-5dea-b2e8-901301c11ed3 mode=source cl
20:38:20.489 broadcast_playback_state: stripped visibility fields (mode='source' clip=a407f8c8) — host is 44f3accf
20:38:20.489 SEND view-state suppressed (not host): mode=source clip=a407f8c8 — position still sent
```

### xStudio (host) 20:38:19–20:38:25
```
20:38:19.019
20:38:19.060  apply_patch: command_schema=PLAYBACK_SETTINGS_1.0 event=SET source=0cdfaa1f
20:38:19.062  RECV playback state: mismatched timeline_guid (local=dabec88d, target=dabec88d, incoming=62812af9)
20:38:19.062  RECV playback state: mismatched timeline_guid — ignoring (not playing)
20:38:19.062  Event: playback_settings
20:38:20.490
20:38:20.491
20:38:20.556  apply_patch: command_schema=TIMELINE_1.0 event=ADD_TIMELINE source=0cdfaa1f
20:38:20.563  ADD_TIMELINE: registered clip_tl=927c7206 for seq_clip=a407f8c8
20:38:20.564  apply_patch: command_schema=PLAYBACK_SETTINGS_1.0 event=SET source=0cdfaa1f
20:38:20.565  RECV playback state: mismatched timeline_guid (local=dabec88d, target=dabec88d, incoming=927c7206)
20:38:20.565  RECV playback state: mismatched timeline_guid — ignoring (not playing)
20:38:20.565  Event: playback_settings
20:38:23.824  [SEL] Selection event fired (source_atom) — queuing resolution
20:38:24.218  Event: queuing playback state broadcast frame=43 playing=True (source_attr=playing)
20:38:24.221
20:38:24.240  [SEL] show_atom media-change: name='graphic_ACES_sRGB' uuid=27189f26 container=timeline raw=len=7 types=[event_atom, show_atom, UuidActo
20:38:24.245  [SEL] normalize bin→sequence clip guid 4332045e→0090c5d3
20:38:24.255  [SEL] broadcast_view_state: new source-clip isolation 0090c5d3 — forcing frame=0 (discarding position 43) + loop
20:38:24.256  [SEL] → broadcast view-state clip 0090c5d3 mode=source
20:38:24.256
20:38:24.882  [SEL] Selection event fired (source_atom) — queuing resolution
20:38:24.915  [SEL] Pinned Source Mode: True → False
20:38:24.922  [SEL] PSM True→False: broadcast view-state 0090c5d3 mode=source playing=True
20:38:24.929
20:38:24.932  [SEL] show_atom media-change: name='laser_ACES_sRGB' uuid=25e48126 container=timeline raw=len=7 types=[event_atom, show_atom, UuidActor,
20:38:24.938  [SEL] normalize bin→sequence clip guid 4a018c2e→a407f8c8
20:38:24.947  [SEL] broadcast_view_state: new source-clip isolation a407f8c8 — forcing frame=0 (discarding position 29) + loop
20:38:24.948  [SEL] → broadcast view-state clip a407f8c8 mode=source
20:38:24.948
```

## Defect 2 — host cannot pull the follower back

### OpenRV 20:38:24–20:38:33
```
20:38:24.266 RECV playback playing=True playback_mode=play-once frame=143 base=100 value=43.0 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence
20:38:24.292 RECV playback playing=True playback_mode=loop frame=89899 base=89899 value=0.0 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=source
20:38:24.292 RECV playback: set playMode=0 (playback_mode=loop)
20:38:24.950 RECV playback playing=True playback_mode=play-once frame=89928 base=89899 value=29.0 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=source
20:38:24.950 RECV playback: set playMode=1 (playback_mode=play-once)
20:38:25.006 RECV playback playing=True playback_mode=loop frame=91699 base=91699 value=0.0 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=source
20:38:25.006 RECV playback: set playMode=0 (playback_mode=loop)
20:38:26.899 RECV playback playing=False playback_mode=loop frame=1 base=1 value=0.0 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence
20:38:32.906 SEND playback playing=False frame=89929 base=89899 fps=24.0 view=sourceGroup000001 tl=dabec88d-9015-44b8-a5d3-e65d01b00416 mode=sequence 
20:38:32.906 broadcast_playback_state: stripped visibility fields (mode='sequence' clip=-) — host is 44f3accf
20:38:32.908 SEND view-state [source]: clip 'graphic_ACES_sRGB' guid=0090c5d3 view=sourceGroup000001
20:38:32.908 SEND playback playing=False frame=89929 base=89899 fps=24.0 view=sourceGroup000001 tl=62812af9-e968-529d-8ab3-5798755c1973 mode=source cl
20:38:32.908 broadcast_playback_state: stripped visibility fields (mode='source' clip=0090c5d3) — host is 44f3accf
20:38:32.908 SEND view-state suppressed (not host): mode=source clip=0090c5d3 — position still sent
```


## Counts across the whole session

| measure | value |
|---|---|
| OpenRV `stripped visibility fields` | 284 |
| OpenRV `view_mode` in SEND blocks | 0 |
| OpenRV `clip_guid` in SEND blocks | 73 (all `PARTIAL` / `INSERT_CHILD` — annotation and structure targets, not visibility) |
| OpenRV remote view switches performed | 13 |
| `MIRROR FAILED` records, either peer | 0 |
| §5.1 candidate guards fired, either peer | 0 |
