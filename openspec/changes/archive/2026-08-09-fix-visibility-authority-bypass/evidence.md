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


# Soak 2 — 2026-08-08 17:08, first run with provenance logging

Session: xStudio host `76836f42`, OpenRV follower `f6fdfeaa`. Instrumentation
from tasks 1.1-2.6 confirmed live on both peers (package installed 16:56, RV
session started 16:57).

**Neither defect reproduced.** The value of this run is a negative result about
the instrumentation itself, and a note on why the scenario missed.

## The provenance window as first built is unusable

All 12 provenance-tagged events blamed the same thing:

```
17:08:27.275  [SEL] Selection event fired (source_atom) [PROVENANCE remote-induced?
              source=f6fdfeaa LiveSession.1/PEER_ANNOUNCE settling+1.55s age=1.55s]
```

`PEER_ANNOUNCE` is the 5-second liveness heartbeat, and the settle window was
5.0 s. 158 heartbeats arrived over the session, so the window was open
essentially all the time. Every one of the 12 was a **false positive** — the
host's own user clicking media in their bin (`[SEL] Playlist.playhead_selection
changed`, `container=Playlist`), with no structural message from the follower
anywhere in the session to cause it.

A window that never closes is worse than no window: acted on rather than logged,
it would have the host reverting its own user's actions.

Fixed by `NON_DISPLAY_EVENTS` — plumbing and lease bookkeeping open no window.
Replaying the denylist against this soak's own log:

| event | attribution after the fix |
|---|---|
| 17:08:27 selection + broadcast | `PLAYBACK_SETTINGS/SET` at −4.14 s / −4.31 s |
| 17:08:32 selection + broadcast | no window (clean local action) |
| 17:08:45 selection + broadcast | `PLAYBACK_SETTINGS/SET` at −3.51 s / −3.62 s |
| 17:08:49 selection + broadcast | no window (clean local action) |

Half now read clean. The other half are attributed to a position message three
to four seconds earlier, which did not cause the host user's bin click either.

**This puts a real tension on the settle window (open question 2).** Defect 1's
observed delay was 3.3 s, so the window must span at least that; these false
positives sit at 3.5-4.1 s. A single global duration cannot separate them.

The available refinement is that the two cases differ in kind, not just
duration: a *structural* message plausibly changes the display seconds later
(the host has to ingest it, and its poll has to notice), while a *position*
message moves the playhead within milliseconds. A per-class settle window —
generous for `TIMELINE_1.0` / `OTIO_SESSION_1.0`, near-zero for
`PLAYBACK_SETTINGS_1.0` — separates them on the axis that actually differs.
Not implemented: it changes design D2/D3 and should be decided, not assumed.

## Why the scenario missed

This session used a **flat playlist**, not a sequence — the earlier soak had
`container=timeline`, this one `container=Playlist`. The host drove all four
isolations from its own bin, and OpenRV followed all five view instructions
correctly.

OpenRV did diverge locally twice (isolating `seq_B` `a4a89785` at 17:08:40 and
`seq_A` `b4a12aae` at 17:08:42), correctly stripping visibility both times, and
it did create clip timelines for them (`e9673446`, `d6e8ba9b`). But it
broadcast **no `ADD_TIMELINE` at all** — zero in either log — so defect 1's
structural route never fired. The host's later isolations were of different
clips (`graphic`, `warp`), not the follower's.

Defect 2 did not reproduce either, and this run is mild evidence *against* the
crude reading of it: after OpenRV locally isolated `seq_A`, the host's next
instruction was adopted (`17:08:46.060 apply view-state: source →
sourceGroup000001`). The stale-cache bug needs the incoming view to *equal* the
last one adopted from a peer while the display differs; here the clip differed,
so the comparison found a change and switched.

## What a reproducing run needs

- A **sequence/timeline** session, not a flat playlist.
- The follower isolating clips **from the sequence**, so a clip timeline is
  created *and broadcast* as `ADD_TIMELINE` (the step this run never reached).
- For defect 2: the host re-sending a view the follower previously adopted,
  *after* the follower has locally moved away from it.

| measure | value |
|---|---|
| host `apply_patch` by type | 158 `PEER_ANNOUNCE`, 124 `CLAIM_OWNERSHIP`, 122 `PLAYBACK_SETTINGS/SET`, 3 `WHO_IS_MASTER`, 2 `STATE_REQUEST` |
| `ADD_TIMELINE`, either peer | 0 |
| OpenRV `stripped visibility fields` | 122 |
| OpenRV remote view switches performed | 5 (all correct) |
| `MIRROR FAILED` / `VIEW DECLINED`, either peer | 0 |
| provenance tags emitted | 12, all false positives |

---

# Soaks 3–5 — 2026-08-09 (xStudio host, OpenRV follower, real sequence)

Three sessions run back-to-back while clearing follower-side blockers. **None
reproduced defect 1**, and the reason is recorded per session below rather than
inferred once. What they did produce is four confirmed defects on the path
between a local isolation and the peer seeing it — the path 3.1 depends on.

Sessions: `rvplugin/ori_sync/{rv_client,xstudio_host}.log` at 11:35, 12:07 and
12:45. Session structure throughout: a real `Sequence 1` (Video Track with
car/graphic/laser, Audio Track with car/graphic) plus 8 bin media, of which
`seq_A`–`seq_D` and `warp` appear in no sequence.

## Confirmed: RV's OTIO reader builds a source per clip *per track*

The 11:35 rebuild produced 13 source groups over 8 distinct media. Fully
attributable, not accumulation:

| groups | origin |
|---|---|
| `000000`–`000007` | the 8 bin media |
| `000008`–`000010` | Video Track clips (car, graphic, laser) |
| `000011`–`000012` | **Audio Track** clips (car, graphic) |

`otio_reader._create_media` appends a blank movieproc to each audio clip and
`_create_stack` wires a second `RVSequenceGroup` of those blank-video sources
into the same stack. An RV source already plays its own movie's audio, so the
track is pure duplication.

A post-hoc dedup pass was considered and rejected: the nodes are not duplicates
(`[car.mov]` vs `[car.mov, blank]`), per-clip sources are load-bearing for
annotations, and rewiring EDL inputs after the fact is strictly more invasive
than not creating them. Stripping mirroring audio tracks before the reader took
the 12:07 rebuild to **11 groups**, as predicted.

## Confirmed: the scan-through guard fired only on the events it should pass

Every `show_atom` in the 12:07 session, by container and disposition:

| container | total | suppressed as "playing through sequence" |
|---|---|---|
| `timeline` | 26 | **0** |
| `playlist` (bin) | 20 | **8** |

The guard exists to drop sequence scan-through, which is the playhead advancing
and reports the Timeline container. It never caught one. All 8 were bin clicks,
each within ~2 s of an isolation:

```
12:07:49.743  → broadcast view-state clip ae96b76a mode=source
12:07:49.893  queuing playback state broadcast frame=0 playing=True   ← forced play
12:07:51.349  show_atom container=playlist name='car_ACES_sRGB'
12:07:51.354  normalize bin→sequence clip guid 3d581080→f9c30e11
12:07:51.362  → suppressed (playing through sequence)
```

Root cause: the bin→sequence normalization rewrites the clip guid so the peer
can match it, which flips `_is_seq_media` true. The guard read that as
provenance. `view_mode` a few lines above had already learned the same lesson
("keying off `_is_seq_media` here mislabelled bin clicks"); the guard had not.

## Confirmed: an unresolvable isolation left OpenRV naming the wrong clip

12:45, isolating bin-only `seq_C`:

```
12:27:48.604 SEND playback ... view=sourceGroup000005 ... mode=source clip=7b7fd1f4
12:27:48.607 view-change sourceGroup000005: no clip in the object map matches
             seq_C.mov — cannot broadcast this isolation
```

`sourceGroup000005` is seq_C; `7b7fd1f4` is laser. One line contradicting
itself. `_cur_clip_guid` is written only where a view change *resolves*, so an
isolation that resolves to nothing left the previous clip standing — wrong for
11 s across three isolations.

**Only the follower visibility strip kept this off the wire.** The sent payload
carried no `view_mode`/`clip_guid` and the host did not move. This is
enforcement catching not merely an unauthorised assertion but a false one — and
it is the reason the bug was invisible: as host, or holding the lease, OpenRV
would have told the peer to show laser.

## Why each session missed defect 1

- **11:35** — follower-side announcement was still gated. Fixed by 3.0.
- **12:07** — the user drove selections from xStudio (the host); the follower
  never isolated.
- **12:45** — the follower *did* isolate three clips locally, but all three
  were `seq_B`/`seq_C`/`seq_D`: **bin-only media with no clip in the shared
  sequence**. No clip timeline can be minted for them, so there was never an
  `ADD_TIMELINE` to trace. Well-formed run, structurally incapable of
  reproducing.

Only one `ADD_TIMELINE` occurred across all three sessions, and it went
host → follower (`source=3bffdb8b`).

## Amendment to "What a reproducing run needs"

The requirement above — "the follower isolating clips **from the sequence**" —
is load-bearing and was missed twice. Stated operationally:

> The follower must isolate `car`, `graphic` or `laser` — a clip **on Sequence
> 1's Video Track**. Isolating `seq_A`–`seq_D` or `warp` cannot produce
> `ADD_TIMELINE`, however deliberate the isolation looks in the log.

## Open, not addressed

- **The host forces `frame=0` + loop on every isolation.** Now implicated in
  three symptoms: it armed the scan-through guard, it is the frame-0 bounce in
  RV, and it leaves playback running after every selection. A behaviour
  decision, not a bug — but it should be made deliberately.
- **8 bin sources stay loaded** alongside the sequence's per-clip sources.
  Legitimate media, not duplicates; whether RV should hold the whole bin is a
  product question.
- **Bin-only media cannot be isolated across peers at all.** There is no shared
  clip to name. Making it work means minting and announcing a clip timeline —
  which *is* defect 1's route, so it needs the §5 guard designed first.
- **`Dequeue timeout` on the PSM poll** cost 2.1 s at 12:09:32 and delayed a
  `show_atom` past its selection event. The stale-actor read hazard; unrelated
  to this change.

---

# Soaks 6–8 — 2026-08-09 14:57–15:10 — defect 1 withdrawn

Three runs closed the question this change was opened on. **Defect 1 does not
exist as described.** A follower's structural message does not move the host's
view, and did not on 2026-08-06 either. Defect 2 was real and is fixed (4.2).

## Soak 6 (14:57) — the follower's route, exercised properly

First run meeting every condition in "What a reproducing run needs": real
sequence, follower isolating three clips **on Sequence 1's Video Track**.

```
14:57:18.110  [rv] view-change sourceGroup000001 → clip 'graphic' e15a5c8b
14:57:18.121  [rv] announce clip timeline 1f6fd428 → SENT
14:57:18.207  [xs] ADD_TIMELINE: registered clip_tl=1f6fd428 for seq_clip=e15a5c8b
              (same for car 14:57:30 and laser 14:57:34)
```

The host's last selection activity was **14:56:51.697**, 26 s before the first
isolation. After it: zero `[SEL]` lines, zero view-state broadcasts, zero
provenance tags. Display unmoved.

Why it cannot move: `SyncManager._h_add_timeline` returns `None` for any
timeline carrying `clip_timeline_for`, so the host application is never
notified and no viewer container is built. Only a *full sequence* timeline
reaches `return ("add_timeline", tl)`. That branch dates to `a6c697a`
(2026-05-25) — **already in place when the 08-06 evidence was recorded**, so it
is neither the cause nor a later fix.

## Soak 7 (15:04) — the host's scan-through, follower idle

```
15:04:52.267  playing=True
15:04:58.461  [SEL] show_atom name='graphic_ACES_sRGB' container=timeline
15:04:58.465  [SEL] → suppressed (playing through sequence)
15:05:01.905  [SEL] show_atom name='laser_ACES_sRGB'   container=timeline
15:05:01.911  [SEL] → suppressed (playing through sequence)
```

**graphic, then laser** — the identical pair, in the identical order, that the
08-06 session recorded as the host following the follower. Produced here with
the follower doing nothing at all. It is the order those clips sit on the Video
Track. The correlation was coincidence.

## Soak 8 (15:10) — and why it is currently silent

Play at 15:10:06.764, edit crossed at 15:10:06.953 — **189 ms**, inside the
0.3 s `_playing_just_started` exemption — and still suppressed.

`_playing_started_at` has exactly one assignment outside init/reset
(`xstudio_plugin/ori_sync/playback_sync.py`, in the **`RECV playback`** apply
path). A local user pressing play never sets it, so the subtraction is against
0.0 and the exemption cannot open. **The 0.3 s window is reachable only when a
peer induced the playback.**

## Reconstruction of 2026-08-06

Consistent with everything above and with the user's own recollection — *"as
soon as the client started picking things, it was affecting the frame (but not
the clip) of the host"*:

1. The follower's position messages drove the **host's play state**.
2. Being remote-induced, that set `_playing_started_at`, opening the 0.3 s
   exemption.
3. 22 ms later (`20:38:24.218` play → `20:38:24.240` show_atom) the host
   crossed an edit. Exempted, so it broadcast as
   `new source-clip isolation — forcing frame=0 + loop`.
4. The second crossing at `20:38:24.932` escaped via the other exemption:
   `PSM True→False` 17 ms earlier made `_in_single_clip` true.
5. Read from the outside: the host followed the follower's clips.

Step 1 is reconstruction — only the excerpt in this file survives from that
session. Steps 2–5 are confirmed against current code and the soaks above.

The chain no longer starts, because `d652343` ("Fixing playback position echo
loop", 2026-08-06) and the position write leases in `82c0a16` removed the
remote-induced play. **Not fixed by this change, and not fixed by the guard —
the trigger was removed from underneath it.**

## Carried forward — the exemptions are dormant, not closed

Both remain, and one is wired to something that will legitimately return:

- **A peer starting playback is a feature, not a bug.** The moment it works on
  purpose, the 0.3 s window reopens and the first edit crossing after it
  broadcasts as a deliberate isolation.
- **The guard cannot do what it documents.** Its comment describes a *local*
  race — "the poll may already have set `_last_polled_playing` before the
  show_atom for the user's own click arrives" — yet only a *remote* play arms
  it. The right signal is a recent deliberate selection
  (`max(_last_source_atom_at, _last_selection_change_at)`, as the PSM freshness
  gate already uses), which is independent of who pressed play.
- **`_in_single_clip` is not evidence of user intent during playback** if
  playback itself can flip PSM. Untested.

## Disposition

| defect | verdict |
|---|---|
| 1 — follower's structure moves the host's view | **withdrawn**; route inert since 2026-05-25, symptom was a position echo surfacing through the scan-through guard |
| 2 — host cannot pull a diverged follower back | **real, fixed** by 4.2 (`_verified_view` written only on adopted/already-displayed) |

Groups 5–7 lapse with defect 1: §5 would have placed a provenance guard at a
`return None`. Groups 1, 2, 4 and 3.0 are implemented and landed in `6b17f8a` —
they stand on their own merits regardless of the defect that motivated them.
