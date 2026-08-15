import time

import rv.commands

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

try:
    from otio_sync_core.authority import (
        SUPPRESSED, CHANNEL_POSITION, POSITION_FIELDS, CHANNEL_VISIBILITY,
        position_frame,
    )
except ImportError:
    SUPPRESSED = "SUPPRESSED"
    CHANNEL_POSITION = "position"
    POSITION_FIELDS = ("current_time", "playing", "playback_mode")
    CHANNEL_VISIBILITY = "visibility"

    def position_frame(state):
        if not any(f in state for f in POSITION_FIELDS):
            return None
        ct = state.get("current_time")
        if not isinstance(ct, dict):
            return None
        value = ct.get("value")
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

from utils import _log, _log_exc, _media_path, _clip_effective_range

def _same_view(a, b):
    """Whether two view-outcome records describe the same outcome for the same view.

    Used to collapse a run of identical outcomes into one log line: in sequence
    mode every position message carries the view too, so an unchanged view
    otherwise reports itself dozens of times a second.
    """
    if not a or not b:
        return False
    return all(
        a.get(k) == b.get(k)
        for k in ("outcome", "view_mode", "clip_guid", "timeline_guid")
    )


# rv.commands.playMode()/setPlayMode() <-> wire playback_mode string.
_PLAY_MODE_TO_WIRE = {0: "loop", 1: "play-once", 2: "ping-pong"}
_WIRE_TO_PLAY_MODE = {v: k for k, v in _PLAY_MODE_TO_WIRE.items()}


class PlaybackSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._last_broadcast_frame = -1
        self._last_selection = []
        # Current local view (what we are broadcasting); mirrors the xStudio
        # plugin's _cur_view_mode/_cur_clip_guid so every PLAYBACK_SETTINGS
        # message — including pure position updates — carries the view too.
        self._cur_view_mode = "sequence"
        self._cur_clip_guid = None
        # Last playback mode received from a peer.
        self._cur_playback_mode = "loop"
        # Shortcut for "this instruction is already satisfied":
        # ``(view_mode, clip_guid, timeline_guid, view_node)`` for the last
        # instruction that demonstrably landed.  Not a record of what RV shows —
        # the fourth element is re-checked against the live ``viewNode()`` before
        # the shortcut is taken, so a local view change invalidates it, and it is
        # cleared outright when an instruction is declined or fails.
        self._verified_view = None
        # What became of the last remote view instruction, and why — surfaced
        # through the test inspector's /state.  A follower mirrors the host
        # rather than approximating it (D4), so an unshowable view has to be
        # reported: silently displaying the nearest local match is what made the
        # wrong-clip divergence invisible.
        #
        # Reporting only *failures* left the same hole one step over.  A
        # follower that received the host's view and quietly did nothing looked
        # exactly like one that complied — in the 2026-08-06 soak a peer ignored
        # `mode=sequence` for six seconds and produced no record at either end.
        # So every exit from the view block records one of four outcomes, and
        # "did nothing" is one of them.
        self.view_outcome = None
        # Backs the :attr:`mirror_failure` property.  Sticky: set on a failure,
        # cleared only when the follower next agrees with the host (D6).
        self._mirror_failure_reason = None

    def _frame_base(self):
        """Return the RV frame that corresponds to protocol position 0.

        The wire protocol carries a **0-based offset into the current view**
        (xStudio's playhead position).  RV's equivalent is ``frame - frameStart``,
        where ``frameStart`` is the first frame of the view's range:

        * a normal no-timecode source/sequence starts at frame 1 (the historic
          hard-coded ``- 1``);
        * an OTIO-imported sequence is built from an EDL whose ``frame`` array
          starts at 0;
        * a source view of timecode-bearing media (e.g. ``seq_D.mov`` spanning
          frames 100-119) starts at 100.

        Reading ``frameStart()`` makes the conversion correct in every case and
        is identical to the old ``- 1`` for the no-timecode media the native
        tests use.  Falls back to 1 if the range can't be read.
        """
        try:
            return int(rv.commands.frameStart())
        except Exception:
            return 1

    #: The four things that can become of a remote view instruction.  Exactly
    #: one is recorded per instruction; there is deliberately no fifth meaning
    #: "nothing happened", because that is what the soak could not distinguish
    #: from compliance.
    VIEW_ADOPTED = "adopted"                      # a view switch was performed
    VIEW_ALREADY_DISPLAYED = "already-displayed"  # already showing it
    VIEW_DECLINED = "declined"                    # deliberately not actioned
    VIEW_FAILED = "failed"                        # attempted, could not complete

    #: Outcomes that mean the follower and the host agree about what is shown.
    _VIEW_OK = (VIEW_ADOPTED, VIEW_ALREADY_DISPLAYED)

    @property
    def mirror_failure(self):
        """Reason the host's view could not be shown, or ``None``.

        Kept so the pre-existing readers — the inspector, ``openrv_hook``, the
        suite's ``VIEW_MIRROR_FAILED`` check — see exactly what they saw before:
        a string on a hard failure, ``None`` otherwise.

        A *declined* instruction deliberately does not surface here.  Declining
        can be correct (a sequence-mode clip change under a moving playhead is
        not an isolation request), so routing it into the failure channel would
        turn every scrub into a suite failure.  It is reported as its own
        outcome instead, on :attr:`view_outcome`.

        This is sticky across a decline: it survives until the follower next
        actually agrees with the host, because a failure followed by a decline
        is still a peer that cannot show what the host is showing.
        """
        return self._mirror_failure_reason

    def _record_view_outcome(self, outcome, reason=None, view_mode=None,
                             clip_guid=None, timeline_guid=None):
        """Record what became of the host's view instruction, and why.

        The record is read back by the sync_test inspector, so a test can catch
        "same timeline name, different clip" — or "instruction quietly ignored"
        — instead of the two peers silently disagreeing.

        :param outcome: One of the four ``VIEW_*`` constants.
        :param reason: Human-readable detail; required for declined/failed.
        """
        previous = self.view_outcome
        self.view_outcome = {
            "outcome": outcome,
            "reason": reason,
            "view_mode": view_mode,
            "clip_guid": clip_guid,
            "timeline_guid": timeline_guid,
            "at": time.time(),
        }
        if outcome in self._VIEW_OK:
            self._mirror_failure_reason = None
            # `already-displayed` is the outcome a stale comparison hides
            # behind: it is what "I ignored the host because I think I already
            # match" reports as.  Leaving it silent put it back out of reach of
            # the logs — the 2026-08-08 21:08 soak recorded ~124 of them and
            # showed none.  Log it, but only when it *changes*, so a steady
            # stream of position messages carrying an unchanged view does not
            # drown the log the way logging every one would.
            if outcome == self.VIEW_ALREADY_DISPLAYED and not _same_view(previous, self.view_outcome):
                _log(
                    f"VIEW ALREADY-DISPLAYED: mode={view_mode}"
                    f" clip={str(clip_guid or '-')[:8]} tl={str(timeline_guid or '-')[:8]}"
                    f" — {reason}"
                )
            return
        if outcome == self.VIEW_FAILED:
            self._mirror_failure_reason = reason
        label = "MIRROR FAILED" if outcome == self.VIEW_FAILED else "VIEW DECLINED"
        _log(f"{label}: {reason}")
        try:
            import sys
            verb = ("Cannot show" if outcome == self.VIEW_FAILED
                    else "Did not adopt")
            print(f"[OTIOSync] {verb} the host's view: {reason}", file=sys.stderr)
        except Exception:
            pass

    def _displayed_view(self):
        """Return ``(view_mode, view_node, timeline_guid)`` for what RV shows now.

        Read from ``rv.commands.viewNode()`` — the application, not a variable
        this class maintains.  Anything this class maintains parts company with
        the display the moment the user changes the view locally, which is
        permitted and which no message records.

        Deliberately reports the source-mode view by its **node**, not by clip
        GUID.  Resolving a node back to a clip means scanning every source group
        and then the whole object map, per message, and this runs on every
        position update in a scrub.  The node is the identity the switch helpers
        already work in, and comparing there is both cheaper and more direct.

        :returns: ``("source", node, tl_guid)``, ``("sequence", node, tl_guid)``,
            or ``(None, None, None)`` when the view cannot be read.
        """
        try:
            view = rv.commands.viewNode()
            node_type = rv.commands.nodeType(view) if view else None
        except Exception:
            return (None, None, None)
        if not view:
            return (None, None, None)
        seq = self.plugin.sequence
        if node_type == "RVSourceGroup":
            clip_tls = getattr(self.plugin.sync_manager, "_clip_timelines", {}) or {}
            return ("source", view, clip_tls.get(view))
        tl_guid = seq._rv_node_to_timeline_guid.get(view)
        if tl_guid is None:
            # OTIO-origin timelines live in _otio_guid_to_root (Stack → Sequence),
            # so the displayed node may be the stack's inner sequence group.
            for guid, root in list(seq._otio_guid_to_root.items()):
                if root == view:
                    tl_guid = guid
                    break
                try:
                    if view in seq._get_sequence_inputs(root):
                        tl_guid = guid
                        break
                except Exception:
                    pass
        return ("sequence", view, tl_guid)

    def _displayed_timeline_guid(self):
        """Return the timeline guid naming the view RV is currently displaying.

        A broadcast frame is expressed relative to the displayed view — a
        timecode source starts at 96899, an OTIO sequence at 0, a normal view
        at 1 — so this guid is the only thing that tells a peer which
        coordinate space the position belongs to.  It is the isolated clip's
        own timeline in source mode, the sequence's timeline in sequence mode,
        and ``None`` when what we are showing has no timeline shared with the
        session.

        ``None`` is a real answer, not a failure to look one up.  Substituting
        ``active_timeline_guid`` was the defect this replaces: it labelled a
        clip-local position with the *sequence's* guid, which passed the
        receiver's timeline check and moved its sequence playhead to a frame
        from a different space.

        Source mode resolves through :attr:`_cur_clip_guid` — the clip this
        peer is claiming to show in the same message, so ``clip_guid`` and
        ``timeline_guid`` always describe the same thing, and an isolation we
        could not resolve (``_forget_current_clip``) yields no guid rather than
        a wrong one.  It deliberately does not use :meth:`_displayed_view`'s
        third element: that branch looks ``_clip_timelines`` up by RV node name
        while the map is keyed by clip guid, so it never resolves.  Harmless
        there (the apply path only reads it for sequence views) but not here.
        """
        mode, _node, tl_guid = self._displayed_view()
        if mode == "source":
            clip_guid = self._cur_clip_guid
            if not clip_guid:
                return None
            clip_tls = getattr(self.plugin.sync_manager, "_clip_timelines", {}) or {}
            return clip_tls.get(clip_guid)
        return tl_guid

    def _report_mirror_failure(self, detail):
        """Record that the host's reported view could not be shown.

        A follower that cannot adopt the host's view reports the failure rather
        than substituting its closest local approximation (D4).

        :param detail: Human-readable reason, e.g. ``"no source group for …"``.
        """
        self._record_view_outcome(self.VIEW_FAILED, detail)

    def _broadcast_playback(self):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        # Input-driven only by construction: every call site funnels through
        # here and the guard above already excludes remote-apply scopes, so
        # claiming can never be triggered by an echo (design.md D4).
        self.plugin.sync_manager.claim_category(CHANNEL_POSITION)
        fps = rv.commands.fps()
        current_frame = rv.commands.frame()
        playing = rv.commands.isPlaying()
        try:
            playback_mode = _PLAY_MODE_TO_WIRE.get(rv.commands.playMode(), "loop")
        except AttributeError:
            playback_mode = "loop"

        displayed_mode, view, _ = self._displayed_view()
        timeline_guid = self._displayed_timeline_guid()
        base = self._frame_base()
        # `displayed` is what RV is actually showing; `mode` is what we are
        # broadcasting.  Logging both is what makes a mislabelled position
        # visible in a log rather than only in a peer's playhead.
        broadcast_clip_guid = self._cur_clip_guid if displayed_mode == "source" else None
        _log(
            f"SEND playback playing={playing} frame={current_frame} base={base}"
            f" fps={fps} view={view} tl={timeline_guid or '-'}"
            f" displayed={displayed_mode}"
            f" mode={displayed_mode} clip={(broadcast_clip_guid or '')[:8]}"
        )
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(current_frame - base),
                "rate": float(fps),
            },
            "playback_mode": playback_mode,
            "timeline_guid": timeline_guid,
            # Carry the current view alongside every position update (not just
            # explicit view-state changes) so a peer receiving a pure scrub/play
            # update also keeps the right mode/clip — single broadcast path (D4).
            "view_mode": displayed_mode,
            "clip_guid": broadcast_clip_guid,
        }
        status = self.plugin.sync_manager.broadcast_playback_state(state)
        self._last_broadcast_frame = current_frame
        return status

    def broadcast_view_state(self, clip_guid, view_mode, asserts_view=True):
        """Broadcast an explicit view-state change (clip and/or mode switch).

        This is the single source of a view-affecting broadcast: every local
        event that changes what we're viewing (RV's view-changed/selection-
        changed events) funnels through here, mirroring the xStudio plugin's
        ``broadcast_view_state``.  Position-only updates ride ``_cur_view_mode``/
        ``_cur_clip_guid`` via ``_broadcast_playback`` instead of duplicating
        this logic.

        Visibility is leased, so this does **not** test whether RV may send it:
        the manager strips ``view_mode``/``clip_guid`` from a non-holder's
        message at the single core enforcement point and reports ``SUPPRESSED``,
        which is observed here only to log it.  Keeping the check out of the
        plugin is what stops the two applications drifting on it, and keeps the
        local view/position bookkeeping identical whichever role RV holds.

        What the plugin *does* decide is whether the local event was a user
        changing the shot, which is what :meth:`claim_category` needs and the
        core cannot see.  *asserts_view* draws that line: RV's native selection
        is a loop/highlight concept rather than a view switch (see
        ``on_selection_changed``), so it passes ``False`` and rides whatever
        lease this peer already holds instead of taking the category for a
        highlight.  Claiming there would let merely highlighting a clip in the
        session manager pull the whole session onto that shot.

        :param clip_guid: GUID of the clip now being viewed, or ``None``.
        :param view_mode: ``"sequence"`` or ``"source"``.
        :param asserts_view: Whether this event is a user changing what is
            shown, and so may claim the visibility lease.
        """
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        self._cur_view_mode = view_mode
        self._cur_clip_guid = clip_guid or None
        if asserts_view:
            self.plugin.sync_manager.claim_category(CHANNEL_VISIBILITY)
        if self._broadcast_playback() == SUPPRESSED:
            _log(
                f"SEND view-state suppressed (not host): mode={view_mode}"
                f" clip={(clip_guid or '-')[:8]} — position still sent"
            )

    def _forget_current_clip(self, reason, view_mode=None):
        """Stop naming a clip we can no longer identify.

        ``_cur_clip_guid`` is written only where a view change *resolves*
        (:meth:`broadcast_view_state` locally, the apply path remotely).  A
        local isolation that cannot be resolved to a clip therefore used to
        leave the previous clip in place, and every position message after it
        went on naming that clip while RV displayed something else.

        Observed 2026-08-09 12:27: isolating ``seq_C`` — bin media with no clip
        in the shared sequence — produced

            SEND playback ... view=sourceGroup000005 ... mode=source clip=7b7fd1f4

        where ``sourceGroup000005`` is seq_C and ``7b7fd1f4`` is laser, the clip
        isolated before it.  It stayed wrong for 11 s across three isolations.
        Only the follower visibility strip kept it off the wire; as host, or
        holding the lease, RV would have told the peer to show laser.

        Reporting ``source`` with no clip is a state the protocol already
        handles: the receiving side declines it as "source mode with no
        clip_guid" and records a mirror failure, which is the honest outcome —
        the peers *are* looking at different things, and neither should pretend
        otherwise.

        :param reason: Why the clip could not be named; logged.
        :param view_mode: Mode to report going forward, or ``None`` to keep the
            current one (for a view we cannot classify at all).
        """
        if self._cur_clip_guid is None and (
            view_mode is None or view_mode == self._cur_view_mode
        ):
            return
        _log(
            f"view-change: forgetting current clip"
            f" {(self._cur_clip_guid or '-')[:8]} — {reason}"
        )
        self._cur_clip_guid = None
        if view_mode is not None:
            self._cur_view_mode = view_mode

    def _apply_playback(self, data):
        """Apply an incoming unified view-state message (SELECTION_1.0 retired).

        Applies, atomically:
        * the **view** — when ``view_mode``/``clip_guid``/``timeline_guid``
          actually transition, switch the RV view node.  A clip-only change
          while the timeline is unchanged in sequence mode surfaces the peer's
          clip selection by switching to that clip's *source* view: RV has no
          Python-bound "highlight in place" command (only view switching), so
          selecting the clip means showing it.  RV loses the parent-sequence
          context, which is acceptable (see the ``xstudio-clip-selection-sync``
          spec — the clip identity is what matters, not its sequence).
        * the **position** — ``current_time`` always wins once any view switch
          above has landed, so the frame is never raced against a separate
          selection-apply (one apply path — D4).

        The view is *mirrored*, not derived: ``view_mode`` decides between the
        sequence and an isolated clip, and ``clip_guid`` decides which clip.  RV
        never computes a view it considers equivalent to the host's, because
        that is what let the two peers reach different answers from the same
        inputs and present them as agreement.  A message carrying no
        ``view_mode`` — which is what a follower's stripped broadcast looks like
        — leaves the local view alone and applies position only.
        """
        view_mode = data.get("view_mode")
        clip_guid = data.get("clip_guid")
        timeline_guid = data.get("timeline_guid")

        if view_mode is not None:
            # Compared against the view RV is *displaying*, never against a
            # record of what it last adopted.  A cache of "what we show" goes
            # stale two ways, and both were observed: the user changes the view
            # locally (no message records it), and a switch that FAILED was
            # still written to the cache — after which the identical instruction
            # reported "already displayed", cleared the failure, and became a
            # permanent no-op the host could not override (2026-08-08 21:43:47).
            cur_mode, cur_node, cur_tl = self._displayed_view()
            _displayed = (
                f"displayed mode={cur_mode} node={cur_node}"
                f" tl={str(cur_tl or '-')[:8]}"
            )
            # Fast path.  Re-resolving the target node means scanning every
            # source group, and this runs on every position update.  The
            # shortcut is safe only because it is re-checked against the live
            # view node: a local view change invalidates it, which is exactly
            # what the cache it replaces failed to do.
            verified = self._verified_view
            already_verified = verified is not None and verified == (
                view_mode, clip_guid, timeline_guid, cur_node
            )
            if already_verified:
                self._record_view_outcome(
                    self.VIEW_ALREADY_DISPLAYED,
                    reason=f"verified against {_displayed}",
                    view_mode=view_mode, clip_guid=clip_guid,
                    timeline_guid=timeline_guid,
                )
            try:
                if already_verified:
                    pass  # outcome already recorded above
                elif view_mode == "sequence":
                    if cur_mode != "sequence" or (
                        timeline_guid is not None and cur_tl != timeline_guid
                    ):
                        # Not showing this sequence — including the case that
                        # matters most, where RV is on a locally isolated clip
                        # and the host is telling it to come back.
                        self._switch_to_sequence_view(timeline_guid)
                    elif clip_guid is not None:
                        # A sequence-mode clip_guid change is deliberately NOT
                        # actioned.  In sequence view xStudio's clip_guid tracks
                        # the clip under the playhead (emitted on show_atom
                        # media-change), so it changes while merely SCRUBBING —
                        # and there is no distinct "user selected a clip" signal
                        # (the Timeline selection actor stays empty; only the
                        # playhead moves).  Isolating on it would wrongly yank RV
                        # to a single clip on every scrub, so RV stays on the
                        # sequence.  Explicit isolation comes via SOURCE mode
                        # (double-click in xStudio), handled by the branch below.
                        #
                        # Recorded rather than silent: this is a *correct*
                        # decline, and it has to be distinguishable from the
                        # incorrect kind — an instruction dropped because the
                        # comparison above was stale.
                        self._record_view_outcome(
                            self.VIEW_DECLINED,
                            reason="sequence-mode clip change tracks the playhead,"
                                   " not a selection — staying on the sequence",
                            view_mode=view_mode, clip_guid=clip_guid,
                            timeline_guid=timeline_guid,
                        )
                    else:
                        self._record_view_outcome(
                            self.VIEW_ALREADY_DISPLAYED,
                            reason=f"verified against {_displayed}",
                            view_mode=view_mode, clip_guid=clip_guid,
                            timeline_guid=timeline_guid,
                        )
                elif view_mode == "source":
                    # Always delegated: deciding "am I already showing this
                    # clip?" needs the clip resolved to its source group, which
                    # is work the switch helper already does.  It short-circuits
                    # to already-displayed when the node it resolves is the one
                    # on screen, so the comparison still happens against the
                    # display rather than against a record of it.
                    self._switch_to_source_view(clip_guid)
                else:
                    self._record_view_outcome(
                        self.VIEW_DECLINED,
                        reason=f"unknown view_mode {view_mode!r}",
                        view_mode=view_mode, clip_guid=clip_guid,
                        timeline_guid=timeline_guid,
                    )
            except Exception as exc:
                _log_exc("apply view-state: view switch failed")
                self._record_view_outcome(
                    self.VIEW_FAILED,
                    reason=f"view switch raised: {exc}",
                    view_mode=view_mode, clip_guid=clip_guid,
                    timeline_guid=timeline_guid,
                )
            # Record the shortcut only when the instruction actually landed, and
            # only against the node that is on screen *now* — after the switch,
            # not the one we intended to reach.  A declined or failed
            # instruction records nothing, so the next identical message
            # re-resolves instead of inheriting an agreement that never
            # happened.  That inheritance is the bug this replaces: a failed
            # switch used to be written here unconditionally, and the repeat
            # then reported success.
            if (self.view_outcome or {}).get("outcome") in self._VIEW_OK:
                try:
                    landed_node = rv.commands.viewNode()
                except Exception:
                    landed_node = None
                self._verified_view = (
                    view_mode, clip_guid, timeline_guid, landed_node
                )
            else:
                self._verified_view = None
            # Sync _cur_* immediately so on_rv_frame_changed / on_rv_play_start
            # callbacks that fire during the frame/play calls below broadcast the
            # right mode instead of echoing stale "sequence" state back to peers.
            self._cur_view_mode = view_mode
            # Only SOURCE mode names a clip.  A sequence-mode clip_guid is the
            # sender's playhead position — the receiving branch above already
            # refuses to act on it for that reason — so recording it here as
            # "the clip we are showing" is the same mistake one step later.
            #
            # It cost a real isolation: the host reported clip 26ca26dd in
            # sequence mode at 09:54:13, RV stored it, and when the user
            # isolated that very clip at 09:54:19 the change was skipped as
            # "already the current clip" and never broadcast (2026-08-09).
            self._cur_clip_guid = (clip_guid or None) if view_mode == "source" else None

        # A message carrying no position fields is one whose sender did not hold
        # the position lease: the manager stripped them on the way out.  Absent
        # means "not mine to say", not "zero" — and the defaults below read it
        # as zero, so every such message stopped playback and seeked to the
        # start of the view.  That is the jump-to-first-frame seen whenever the
        # host reported its view while the follower held position
        # (2026-08-09 09:54:21, `value=None` with the view switch alongside).
        #
        # The view block above already gets this right by only acting when
        # view_mode is present; this is the same rule for the other field group.
        if not any(field in data for field in POSITION_FIELDS):
            _log(
                "RECV playback: no position fields (stripped by sender) —"
                f" view applied, position left alone (mode={view_mode})"
            )
            return

        playing = data.get("playing", False)
        playback_mode = data.get("playback_mode", "loop")
        self._cur_playback_mode = playback_mode
        current_time = data.get("current_time", {})

        # Resolve the frame base AFTER any view switch so frameStart() reflects
        # the view the frame actually targets (a timecode source starts at 100,
        # an OTIO sequence at 0, a normal view at 1).  Protocol value is a 0-based
        # offset into that view.
        base = self._frame_base()
        # A present-but-unusable current_time asserts no more than an absent one
        # (see authority.position_frame).  Reading it as 0 is what turned "said
        # nothing" into "seek to the start"; the play state below still applies,
        # because only the seek depended on the value.
        _value = position_frame(data)
        target_frame = None if _value is None else int(_value) + base
        _log(
            f"RECV playback playing={playing} playback_mode={playback_mode}"
            f" frame={'-' if target_frame is None else target_frame} base={base}"
            f" value={current_time.get('value')} tl={timeline_guid} mode={view_mode}"
        )

        # Suppress on_rv_frame_changed / on_rv_play_start during mechanical apply
        # so they don't echo back to peers — _broadcast_playback checks _rv_updating.
        self.plugin._rv_updating = True
        try:
            # Sync RV's play mode so it doesn't fire spurious playing=False at the
            # clip boundary and cause the peer to stop.
            target_play_mode = _WIRE_TO_PLAY_MODE.get(playback_mode, 0)
            try:
                if rv.commands.playMode() != target_play_mode:
                    rv.commands.setPlayMode(target_play_mode)
                    _log(f"RECV playback: set playMode={target_play_mode} (playback_mode={playback_mode})")
            except Exception:
                _log_exc("RECV playback: setPlayMode failed")
            if target_frame is not None and rv.commands.frame() != target_frame:
                rv.commands.setFrame(target_frame)
            is_playing = rv.commands.isPlaying()
            if playing and not is_playing:
                rv.commands.play()
            elif not playing and is_playing:
                rv.commands.stop()
        finally:
            self.plugin._rv_updating = False

    def _switch_to_sequence_view(self, timeline_guid):
        """Switch the RV view to the RVSequenceGroup for *timeline_guid*.

        No seek happens here — in sequence mode position is authoritative and
        is applied right after this returns, by the caller (D2).

        Adopts the host's timeline; when that timeline has no local node the
        failure is reported rather than approximated.  The one substitution kept
        is the single-sequence case, where "the only sequence in the session" is
        the host's timeline by construction rather than a guess at it.
        """
        seq_node = None
        if timeline_guid:
            # The OTIO root wins when both exist.  A timeline rebuilt by
            # apply_otio_snapshot is registered there, and the display is left
            # on it; a native entry surviving alongside is stale by definition.
            # The old order took the native entry first, so a GUID with both
            # would switch to the orphan while the display sat on the root —
            # two nodes answering to one GUID, resolved differently by the
            # switch and by the display read (2026-08-08 22:35).
            root = self.plugin.sequence._otio_guid_to_root.get(timeline_guid)
            if root and rv.commands.nodeType(root) == "RVStackGroup":
                # Use the inner RVSequenceGroup so setViewNode/setFrame work.
                inputs = self.plugin.sequence._get_sequence_inputs(root)
                seq_node = next(
                    (n for n in inputs
                     if rv.commands.nodeType(n) == "RVSequenceGroup"),
                    root,
                )
            native = [
                rv_node
                for rv_node, mapped in self.plugin.sequence._rv_node_to_timeline_guid.items()
                if mapped == timeline_guid
                and rv.commands.nodeType(rv_node) != "RVSourceGroup"
            ]
            if seq_node is not None and native:
                # Not fatal — the root is the right answer — but one GUID owning
                # two nodes means something registered a node it should have
                # replaced, and that has to be visible where it happens rather
                # than inferred from odd behaviour a soak later.
                _log(
                    f"apply view-state: WARNING {timeline_guid[:8]} resolves to"
                    f" both OTIO root {seq_node} and native {native}"
                    " — using the root; the native entries are stale"
                )
            elif seq_node is None and native:
                seq_node = native[0]
        if seq_node is None:
            # Only substitute when there is no choice to get wrong: a session
            # holding exactly one sequence has only one node the host can mean.
            # Picking "the first non-source-group node" out of several was a
            # nearest-local-match guess, and a wrong guess here is precisely the
            # divergence this change removes — RV showing one clip while the
            # host holds another, both reporting the same timeline name.
            candidates = [
                n for n in self.plugin.sequence._rv_node_to_timeline_guid
                if rv.commands.nodeType(n) != "RVSourceGroup"
            ]
            if len(candidates) == 1:
                seq_node = candidates[0]
            elif candidates:
                self._report_mirror_failure(
                    f"host's timeline {(timeline_guid or '?')[:8]} has no local node"
                    f" and {len(candidates)} sequences are candidates — not guessing"
                )
                return
        if seq_node is None:
            self._report_mirror_failure(
                f"no sequence node for the host's timeline {(timeline_guid or '?')[:8]}"
            )
            return
        seq_tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(seq_node)
        if seq_tl_guid:
            self.plugin.sync_manager.active_timeline_guid = seq_tl_guid
        self.plugin._rv_updating = True
        try:
            rv.commands.setViewNode(seq_node)
            self._record_view_outcome(
                self.VIEW_ADOPTED, view_mode="sequence", timeline_guid=timeline_guid
            )
            _log(f"apply view-state: sequence → {seq_node} ({(timeline_guid or '')[:8]})")
        finally:
            self.plugin._rv_updating = False

    def _switch_to_source_view(self, clip_guid):
        """Switch the RV view to the source group for *clip_guid* (source mode).

        Shows the clip the host reports, resolved by GUID.  Every path that
        cannot resolve it reports the failure instead of leaving whatever
        happens to be on screen and letting the peers look synced.
        """
        if not clip_guid:
            self._report_mirror_failure("host reported source mode with no clip_guid")
            return
        clip = self.plugin.sync_manager._object_map.get(clip_guid) if self.plugin.sync_manager else None
        if clip is None or not isinstance(clip, otio.schema.Clip):
            # Bin clip guid: xStudio sometimes sends the flat-playlist clip guid
            # (which is purged from object_map when the playlist is replaced by a
            # real sequence).  Fall back to the cached bin guid → path mapping.
            media_path = self.plugin.sequence._bin_guid_to_path.get(clip_guid)
            if not media_path:
                self._report_mirror_failure(
                    f"host's clip {clip_guid[:8]} is in neither the object map"
                    " nor the bin cache"
                )
                return
            _log(f"apply view-state: resolving bin clip_guid={clip_guid[:8]} via bin cache → {media_path}")
        else:
            ref = clip.media_reference
            if not isinstance(ref, otio.schema.ExternalReference):
                self._report_mirror_failure(
                    f"host's clip {clip_guid[:8]} has no external media reference"
                )
                return
            media_path = _media_path(ref.target_url)
        source_group = self.plugin.sequence._path_to_source_group_map().get(media_path)
        if not source_group:
            self._report_mirror_failure(
                f"host's clip {clip_guid[:8]} ({media_path}) has no local source group"
            )
            return
        clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
        # In flat-playlist mode the current sequence view shows exactly this source
        # group as its only input.  Switching to the source group would show
        # identical content but move annotations to a different paint node
        # (sourceGroup_paint vs seq_p_sourceGroup), hiding locally-drawn strokes.
        current_view = rv.commands.viewNode()
        if current_view == source_group:
            # Already showing exactly the node the host named — compared against
            # the display, so this is the spec's "an instruction matching the
            # displayed view is still a no-op".  Returning here matters beyond
            # saving work: falling through would re-run setFrame(1) on every
            # position update in a scrub and fight the position apply below.
            self._record_view_outcome(
                self.VIEW_ALREADY_DISPLAYED,
                reason=f"already showing {source_group}",
                view_mode="source", clip_guid=clip_guid,
            )
            return
        if current_view != source_group:
            try:
                seq_inputs = self.plugin.sequence._get_sequence_inputs(current_view)
                if seq_inputs == [source_group]:
                    # Not an approximation: the current view's only input IS the
                    # clip the host named, so we are already showing exactly it.
                    # Switching anyway would move annotations to a different
                    # paint node and hide locally-drawn strokes.
                    self._record_view_outcome(
                        self.VIEW_ALREADY_DISPLAYED,
                        reason=f"{current_view} already shows {source_group}",
                        view_mode="source", clip_guid=clip_guid,
                    )
                    _log(
                        f"apply view-state: flat-playlist source mode — {current_view} "
                        f"already shows {source_group}, skipping view switch"
                    )
                    return
            except Exception:
                pass
        self.plugin._rv_updating = True
        try:
            rv.commands.setViewNode(source_group)
            rv.commands.setFrame(1)  # jump to first frame of this source
            self._record_view_outcome(
                self.VIEW_ADOPTED, view_mode="source", clip_guid=clip_guid
            )
            _log(f"apply view-state: source → {source_group} (clip {clip_guid[:8]})")
        finally:
            self.plugin._rv_updating = False

    def _clip_guid_for_media_path(self, media_path):
        """Return the OTIO GUID of the Clip whose ExternalReference matches media_path."""
        import os
        norm_media_path = os.path.abspath(_media_path(media_path))
        _log(f"LOOKUP CLIP: media_path='{media_path}' -> norm='{norm_media_path}'")
        for guid, obj in self.plugin.sync_manager._object_map.items():
            if isinstance(obj, otio.schema.Clip):
                ref = obj.media_reference
                if isinstance(ref, otio.schema.ExternalReference):
                    ref_norm = os.path.abspath(_media_path(ref.target_url))
                    _log(f"  Checking clip {guid}: target_url='{ref.target_url}' -> norm='{ref_norm}'")
                    if ref.target_url == media_path or ref_norm == norm_media_path:
                        _log(f"  -> MATCHED clip {guid}")
                        return guid
        _log("LOOKUP CLIP: NO MATCH FOUND")
        return None

    def _clip_guid_for_media_and_frame(self, media_path, media_frame):
        """Return the OTIO clip guid for the occurrence of media_path covering media_frame.

        For OTIO cut sequences the same file can appear at multiple positions with
        different source_range values.  We pick the clip whose effective range
        (source_range, or media_reference.available_range when source_range is
        the legitimate-but-unhelpful None — see _clip_effective_range) contains
        media_frame (the absolute media/timecode frame stored in RV paint node
        names). Falls back to the first path-match for native single-occurrence
        timelines or when no range covers the frame.
        """
        import os
        norm = os.path.abspath(_media_path(media_path))
        fallback = None
        for guid, obj in self.plugin.sync_manager._object_map.items():
            if not isinstance(obj, otio.schema.Clip):
                continue
            ref = obj.media_reference
            if not isinstance(ref, otio.schema.ExternalReference):
                continue
            ref_norm = os.path.abspath(_media_path(ref.target_url))
            if ref.target_url != media_path and ref_norm != norm:
                continue
            if fallback is None:
                fallback = guid
            effective = _clip_effective_range(obj)
            if effective is not None:
                start, end = effective
                if start <= int(media_frame) <= end:
                    _log(f"LOOKUP CLIP (frame={media_frame}): matched occurrence {guid} [{start}..{end}]")
                    return guid
        if fallback:
            _log(f"LOOKUP CLIP (frame={media_frame}): no source_range match, using fallback {fallback}")
        else:
            _log(f"LOOKUP CLIP (frame={media_frame}): NO MATCH FOUND")
        return fallback

    def on_selection_changed(self, event):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        selection = rv.commands.selection()
        if selection == self._last_selection:
            event.reject()
            return
        self._last_selection = selection
        # Map each selected source group to an OTIO clip GUID and broadcast the
        # first one as a highlight (the view mode doesn't change — RV's native
        # "selection" is a loop/highlight concept, not a view switch; see
        # design.md's resolved highlight-only note).
        for node in selection:
            # Per node, for the same reason as on_view_changed: inverting the
            # path-keyed map loses every duplicate source group but one.
            media_path = self.plugin.sequence.media_path_for_source_group(node)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND view-state [selection-change]: clip '{_clip_label}' guid={clip_guid[:8]} node={node}")
                    # Highlight only, per the note above — not a view switch, so
                    # it does not claim visibility.
                    self.broadcast_view_state(
                        clip_guid, self._cur_view_mode, asserts_view=False
                    )
                    break
        event.reject()

    def on_view_changed(self, event):
        """RV's view node changed — decide whether it is ours to broadcast.

        Reports every path, including the ones that do nothing.  This function
        used to be silent unless it reached a broadcast, so a local isolation
        that produced no message was indistinguishable from one that never
        reached the function at all — four different causes, one empty log, and
        a soak spent failing to tell them apart (2026-08-09 08:32).
        """
        view = None
        try:
            view = rv.commands.viewNode()
        except Exception:
            pass
        if self.plugin._rv_updating:
            _log(f"view-change {view}: ignored — applying a remote message")
            event.reject()
            return
        if not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            _log(f"view-change {view}: ignored — not synced")
            event.reject()
            return
        node_type = rv.commands.nodeType(view) if view else None
        _log(f"view-change {view} ({node_type})")
        tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view)
        if tl_guid and rv.commands.nodeType(view) != "RVSourceGroup":
            # Sequence/timeline view — covers both switching to a different
            # sequence and returning from a source (clip) view.
            if tl_guid != self.plugin.sync_manager.active_timeline_guid or self._cur_view_mode != "sequence":
                self.plugin.sync_manager.active_timeline_guid = tl_guid
                _log(f"SEND view-state [sequence]: view={view} tl={tl_guid}")
                self.broadcast_view_state(None, "sequence")
            else:
                _log(
                    f"view-change {view}: sequence unchanged"
                    f" (tl={str(tl_guid)[:8]}) — nothing to send"
                )
        elif rv.commands.nodeType(view) == "RVSourceGroup":
            # Clip selection: user double-clicked into a source group.
            # Map source group → media path → OTIO clip GUID and broadcast.
            # Asked of the node directly, not by inverting the path-keyed map:
            # duplicate source groups for one media file collapse in that map,
            # and the loser then has no media path at all.
            media_path = self.plugin.sequence.media_path_for_source_group(view)
            if not media_path:
                _log(
                    f"view-change {view}: isolated a source group with no media"
                    " path (placeholder, or no file source) — cannot broadcast"
                )
                self.plugin.sequence.log_source_group_inventory(
                    f"{view} has no media path"
                )
                self._forget_current_clip(
                    f"{view} has no media path", view_mode="source"
                )
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid and clip_guid == self._cur_clip_guid:
                    _log(
                        f"view-change {view}: clip {clip_guid[:8]} is already the"
                        " current clip — not re-broadcasting"
                    )
                elif not clip_guid:
                    _log(
                        f"view-change {view}: no clip in the object map matches"
                        f" {media_path} — cannot broadcast this isolation"
                    )
                    self._forget_current_clip(
                        f"{view} shows media no shared clip names",
                        view_mode="source",
                    )
                if clip_guid and clip_guid != self._cur_clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND view-state [source]: clip '{_clip_label}' guid={clip_guid[:8]} view={view}")
                    clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
                    if clip_tl_guid:
                        # Unconditional: the manager announces each clip
                        # timeline once.  Gating here on whether *this* peer
                        # created it is what left one built during a remote
                        # apply unannounced.
                        status = self.plugin.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                        _log(
                            f"view-change {view}: announce clip timeline"
                            f" {clip_tl_guid[:8]} → {status}"
                        )
                        self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
                    else:
                        _log(
                            f"view-change {view}: no clip timeline for"
                            f" {clip_guid[:8]} — nothing to announce"
                        )
                    self.broadcast_view_state(clip_guid, "source")
        else:
            _log(
                f"view-change {view} ({node_type}): not a known sequence and not"
                " a source group — no view-state sent"
            )
            # No mode override: we cannot classify this view, so claiming
            # "source" would be a different guess rather than fewer.  Dropping
            # the clip is what matters — whatever this node is, it is not the
            # clip we were naming.
            self._forget_current_clip(f"{view} is a {node_type}, not a clip")
        event.reject()
