
Launching rabbitmq on macOS:

```bash
CONF_ENV_FILE="/opt/homebrew/etc/rabbitmq/rabbitmq-env.conf" /opt/homebrew/opt/rabbitmq/sbin/rabbitmq-server
```

Launching rabbitmq on macOS:

```bash
CONF_ENV_FILE="/opt/homebrew/etc/rabbitmq/rabbitmq-env.conf" /opt/homebrew/opt/rabbitmq/sbin/rabbitmq-server
```


# Carried out of fix-visibility-authority-bypass (archived 2026-08-09)

Defect 1 was withdrawn on evidence, so these outlived the change that found
them. Reasoning is in that change's `evidence.md`, soaks 6–8.

* [ ] **Scan-through guard exemptions are dormant, not closed.** `_playing_started_at`
  is set only by a *remote* play, so the 0.3s `_playing_just_started` exemption
  opens only when a peer induced playback — which is exactly what made the
  2026-08-06 symptom possible. Peer-initiated playback is a legitimate feature;
  when it works on purpose, the hole reopens. Fix: key the exemption on a recent
  deliberate selection (`max(_last_source_atom_at, _last_selection_change_at)`,
  as the PSM freshness gate already does), not on recent play.
* [ ] **Does xStudio playback itself flip Pinned Source Mode?** If so,
  `_in_single_clip` is not evidence of user intent during playback, and it is the
  other exemption that let 08-06 through. Untested.
* [ ] **The host forces `frame=0` + loop on every isolation.** Implicated in three
  separate symptoms: it armed the scan-through guard, it is the frame-0 bounce in
  RV, and it leaves playback running after every selection. A behaviour decision —
  make it deliberately.
* [ ] **Bin-only media cannot be isolated across peers.** No shared OTIO clip exists
  to name, so RV declines (honestly, since the `_forget_current_clip` fix). Making
  it work means minting and announcing a clip timeline for it.
* [ ] **8 bin sources stay loaded** alongside the sequence's per-clip source groups.
  Not duplicates; whether RV should hold the whole bin is a product question.
* [ ] **Rethink xStudio→RV sync: "create what is viewed" rather than replicate
  xStudio's setup.** Would dissolve bin-vs-sequence guid normalization, the
  object-map lookup, and bin-only media having nothing to name. Own change.
* [ ] **`docs/visibility_authority_guards.md` §5.1 is unblocked but undecided.** The
  objection was retracted 2026-08-09; the zero-fire counts still are not evidence.
  Needs a fresh soak against current code.

# Todo Critical

* [ ] Need to figure out how to handle regular playlists reordering for xstudio playlists in rv.
* [ ] Test a media handler, so missing media can be substituted.
* [ ] Handle x, y flip
* [ ] Handle play backwards. (xstudio doesnt have a button for this)
* [ ] Changes to frame rate.

* Creating prototype C++ changes to OTIO, currently its failing running the tests.

# TODO Cleanup

- [ ] Add a version number for the xstudio plugin (`VERSION`, stamped at package time) once an xStudio release ships containing the plugin's upstream dependencies (PRs #299 / #302 / #303) — replace the README's "a build from `develop`" floor with that version

# Sync Security and Session Users

* [ ] Handle encryption and possibly compression.

# openrv_sync_plugin

* [ ] Figure out redraw issue where text gets visually duplicated for a moment. - created test for this - text_annotations_xstudio_to_rv duplicate text not in rv file.
* [ ] Revisit testing rvio for annotation rendering.

# XStudio Plugin

* [ ] Handle Shapes as OTIO objects: draw a Square/Circle/Arrow/Line; confirm **no** mid-drag partial is broadcast and the shape appears only on pen-up.
* [ ] When selecting a clip, the very first time it will play-once, rather than loop.

## Known Limitations

## API Improvement Tasks (from xstudio_questions.md replies)

# Test Charts

* Brush widths are possibly wrong on xstudio version.
* Missing brush width for test sizes.
* Need to add more opacity brushes
* Need to add some test annotations.
* See if we can get an SVG version too of the annotations for verification purposes.
* Need a test harness that can load the OTIO Annotation file for openRV, and then render it out.
* Need a test harness that can load the OTIO Annotation file for xSTUDIO, and then render it out.

# General questions

* Exposure is currently applied to all clips (all RVColor nodes normalized on change). This avoids spurious re-broadcasts when navigating between clips.

# Questions for openrv

How to lock out parts of the interface

33/42 tasks done. The remaining 9 are all live verification — no more implementation code needed.

The code work is complete. What's left is you running live tests in RV (+xStudio) to confirm:

Task What to verify
6.3 Paint a stroke on an OTIO-origin clip; confirm it reaches the peer
7.2 Run an existing native-timeline test (add/delete/reorder clip); confirm no regression
8.1 Already covered by otio_import_rv_to_rv passing — just needs checkbox
8.2 Swap a clip's source file; confirm a whole-OTIO push arrives on peer (not a per-property patch)
8.3 Trim a cut in/out; confirm it reaches peer via REPLACE_TIMELINE
8.4 Change OCIO colorspace on an OTIO clip; confirm it syncs
8.5 Add a clip to the OTIO sequence; confirm REPLACE_TIMELINE reaches peer
8.6 Connect an older peer with no sync.origin; confirm treated as native
8.7 Reorder in a native session; confirm MOVE_CHILD still fires, not snapshot
8.2 and 8.3 description in the tasks says "property patch" — worth noting those actually fall back to whole-OTIO push (per the §5 implementation choices), so the assertions should say "REPLACE_TIMELINE arrives" not "SET_PROPERTY arrives". Once you've done the live run, I can check those off and we can archive the change.

ORI_SESSION="amqps://aswf:29a5953658144449ac73385b9e3144eedb76@146.235.219.100:5671/aswf?cacertfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/ca.pem&certfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.crt&keyfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.key:samtest"  ORI_SYNC_LOG_FILE=/Users/sam/git/ORIAnnotations/rvplugin/ori_sync/xstudio_host2.log xstudio test_media/source/encoded/*.mov

ORI_SESSION="amqps://aswf:29a5953658144449ac73385b9e3144eedb76@146.235.219.100:5671/aswf?cacertfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/ca.pem&certfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.crt&keyfile=/Users/sam/git/ORIAnnotations/scratch/RabbitMQCertsandsecrets/aswf-amqps-kit/client.key:samtest"  ORI_SYNC_LOG_FILE=/Users/sam/git/ORIAnnotations/rvplugin/ori_sync/xstudio_client2.log xstudio -n

# Openspec changes


5 retire-position-structure-echo-guards
6 session-role-administration

Later:

* define-neutral-viewport-coordinates - Long term future task - need to define proper viewport coordinates that correctly work across OTIO.
* network-send-robustness - suspected RV network blocking.

