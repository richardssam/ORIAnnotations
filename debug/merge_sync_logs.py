#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Merge two or more ori_sync peer logs into one time-ordered view.

Every sync question is a question about two peers at the same instant — "the
client is looking at something different to the host", "the client didn't stop
when I paused" — and answering it from two separate files means holding two
timelines in your head and grepping each for the terms you already suspect. That
biases the answer towards whatever you thought to grep for.

Four views, all over the same parsed stream:

``merge`` (default)
    Every line from every log, interleaved by timestamp, tagged with its peer.
``state``
    What each peer *believed* — view mode, clip, timeline, frame, playback mode
    — as a transition table. One row per change, peers side by side.
``diverge``
    Only the intervals where peers disagreed about what is on screen, with how
    long each lasted. This is the "they're looking at different things" report.
``wire``
    Wire traffic only, correlating each send with the peers that received it and
    the latency between, so a message nobody applied is visible as such.

Usage::

    python debug/merge_sync_logs.py rvplugin/ori_sync/xstudio_{host,client}.log
    python debug/merge_sync_logs.py --view diverge  <logs...>
    python debug/merge_sync_logs.py --view state --since 15:03:00 <logs...>
    python debug/merge_sync_logs.py --json <logs...> > events.jsonl

Peer names default to the filename stem with any ``xstudio_``/``rv_`` prefix
removed (``xstudio_host.log`` → ``host``); override with ``name=path``.

Pure stdlib, so it runs under any interpreter in this repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

# A log line: "15:03:17.512  apply view-state: ...". Two spaces after the stamp.
_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s\s?(.*)$")
_MQ_HDR_RE = re.compile(r"^=== MQ (SEND|RECV) \[([^\]]*)\] ===$")


@dataclass
class Event:
    """One log entry, from one peer."""

    t: float                      # seconds since midnight
    peer: str
    text: str
    extra: list[str] = field(default_factory=list)   # continuation lines
    wire: dict | None = None      # parsed payload, for MQ blocks
    direction: str | None = None  # SEND / RECV
    lineno: int = 0

    @property
    def stamp(self) -> str:
        h, rem = divmod(self.t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

    def schema(self) -> tuple[str, str] | None:
        if not self.wire:
            return None
        payload = self.wire.get("payload", {})
        cmd = payload.get("command", {})
        return (payload.get("command_schema", "?"), cmd.get("event", "?"))

    def source(self) -> str | None:
        return (self.wire or {}).get("source_guid")

    def to_dict(self) -> dict:
        d = {"t": round(self.t, 3), "stamp": self.stamp, "peer": self.peer,
             "text": self.text, "lineno": self.lineno}
        if self.extra:
            d["extra"] = self.extra
        if self.wire is not None:
            d["direction"] = self.direction
            schema = self.schema()
            d["schema"], d["event"] = schema[0], schema[1]
            d["source_guid"] = self.source()
            d["payload"] = self.wire.get("payload", {}).get("command", {}).get("payload")
        return d


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_log(path: str, peer: str) -> list[Event]:
    """Parse one peer log into events, folding MQ blocks and continuations in."""
    with open(path, errors="replace") as fh:
        lines = fh.read().splitlines()

    events: list[Event] = []
    i = 0
    day = 0.0        # added to every stamp; bumped when the clock wraps
    last_t = -1.0

    while i < len(lines):
        line = lines[i]
        m = _TS_RE.match(line)
        if not m:
            # A stray line before the first stamp, or a continuation whose
            # parent we already consumed.
            if events:
                events[-1].extra.append(line)
            i += 1
            continue

        raw_t = _seconds(*m.groups()[:4])
        if raw_t + day < last_t - 3600:      # midnight wrap, not mere jitter
            day += 86400.0
        t = raw_t + day
        last_t = t
        text = m.group(5).strip()
        lineno = i + 1
        i += 1

        # An MQ block: a stamp with no message, then the header, then JSON.
        if not text and i < len(lines) and _MQ_HDR_RE.match(lines[i].strip()):
            hdr = _MQ_HDR_RE.match(lines[i].strip())
            direction = hdr.group(1)
            i += 1
            depth, buf = 0, []
            started = False
            while i < len(lines):
                buf.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                if "{" in lines[i]:
                    started = True
                i += 1
                if started and depth <= 0:
                    break
            try:
                wire = json.loads("\n".join(buf))
            except (ValueError, TypeError):
                wire = None
            if wire is not None:
                ev = Event(t=t, peer=peer, text="", wire=wire,
                           direction=direction, lineno=lineno)
                schema, event = ev.schema()
                ev.text = f"[{direction}] {schema}/{event}"
                events.append(ev)
            continue

        events.append(Event(t=t, peer=peer, text=text, lineno=lineno))

        # Fold indented continuations (e.g. the [SCAN] census) into the parent.
        while i < len(lines) and not _TS_RE.match(lines[i]) and lines[i].startswith(" "):
            events[-1].extra.append(lines[i])
            i += 1

    return events


# ── state extraction ──────────────────────────────────────────────────────────
#
# Derived from wire payloads where possible: JSON a peer sent is a far more
# reliable record of what it believed than prose it logged about it. The log
# lines below cover what never goes on the wire — what a *receiving* peer did
# with a message, which is exactly where divergence becomes visible.

# Both hosts log an applied view, in three spellings between them — the name is
# quoted in xStudio and bare in OpenRV, and source mode labels its guid `clip`:
#
#   apply view-state: sequence → 'Added Media' (2fe12c79)      xStudio
#   apply view-state: sequence → defaultSequence (f5e35c8d)    OpenRV, sequence
#   apply view-state: source → sourceGroup000001 (clip 2e5f1ec8)  OpenRV, source
_APPLY_VIEW_RE = re.compile(
    r"apply view-state: (\w+) → '?([^'(]+?)'? \((?:clip )?([0-9a-f]+)\)"
)
# xStudio's source-mode apply is logged by a different code path again.
_APPLY_SOURCE_RE = re.compile(
    r"RECV selection: clip '([^']*)' guid=([0-9a-f]+) mode=(\w+)"
)
# The position actually applied. OpenRV has always logged this; xStudio's
# equivalent was added 2026-08-13 (until then a follower that applied every
# frame and one that dropped them all produced identical logs).
_RV_RECV_PLAYBACK_RE = re.compile(
    r"RECV playback playing=(\w+) playback_mode=(\S+) frame=(-?\d+).*?"
    r"tl=([0-9a-f-]+) mode=(\w+)"
)
_XS_SEEK_RE = re.compile(r"RECV playback: seek → frame=(-?[\d.]+) playing=(\w+)")
_DECLINED_RE = re.compile(r"VIEW DECLINED: (.{0,40})")
_MISMATCH_RE = re.compile(
    r"mismatched timeline_guid \(local=([0-9a-f]+), target=([0-9a-f]+), incoming=([0-9a-f]+)\)"
)
_UNPUBLISHED_RE = re.compile(r"\[VIEW\] viewing Timeline ([0-9a-f]+) which is NOT")
_ELECT_RE = re.compile(r"elect_host: (\S+) → (\S+) \(self=(\w+)")
_ROLE_STRIP_RE = re.compile(r"stripped fields not permitted to role '(\w+)'")
_REFUSED_RE = re.compile(r"claim_category: refused (\w+) — role '(\w+)'")

# Fields tracked per peer, in the order they are displayed.
_FIELDS = ("view_mode", "clip_guid", "timeline_guid", "frame", "applied_frame",
           "playback_mode", "playing", "role", "applied_view", "note")


def _short(v):
    """First 8 hex chars of any guid, dashed or not.

    Clip guids arrive as bare 40-char hex while timeline guids are dashed
    uuids, and the logs print both truncated to 8. Normalising to the same
    8 chars is what lets a wire payload be compared with a log line.
    """
    if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{8}[0-9a-f-]*", v):
        return v[:8]
    return v


def _normalise_view(mode, clip_guid, timeline_guid) -> str | None:
    """One comparable string for "what is on screen".

    Source mode is identified by its clip, every other mode by its timeline. A
    peer isolated on a clip and a peer showing the sequence that contains it are
    looking at different things while carrying the same ``timeline_guid``.
    """
    if not mode:
        return None
    ident = clip_guid if mode == "source" else timeline_guid
    return f"{mode}/{_short(ident) or '-'}"


def build_state(events: list[Event]) -> list[tuple[float, str, dict]]:
    """Return (t, peer, changed-fields) for every observable state change."""
    current: dict[str, dict] = {}
    out: list[tuple[float, str, dict]] = []

    def update(t, peer, **kw):
        cur = current.setdefault(peer, {})
        changed = {k: v for k, v in kw.items() if v is not None and cur.get(k) != v}
        if not changed:
            return
        cur.update(changed)
        out.append((t, peer, changed))

    for ev in events:
        if ev.wire is not None and ev.direction == "SEND":
            schema, event = ev.schema()
            payload = ev.wire.get("payload", {}).get("command", {}).get("payload") or {}
            if schema == "PLAYBACK_SETTINGS_1.0":
                ct = payload.get("current_time") or {}
                # `view_mode` absent means the visibility group was stripped —
                # this peer is not the host and asserted nothing about the view.
                # Recording that as a value would make every follower look like
                # it disagreed with the host for the whole session.
                asserts_view = "view_mode" in payload
                update(
                    ev.t, ev.peer,
                    view_mode=payload.get("view_mode") if asserts_view else None,
                    clip_guid=_short(payload.get("clip_guid")) if asserts_view else None,
                    asserted_view=(
                        f"{payload.get('view_mode')}/{_short(payload.get('timeline_guid'))}"
                        if asserts_view else None
                    ),
                    # Normalised "what is on screen", comparable with the
                    # applied forms below so a host's claim and a follower's
                    # result can be held against each other.  Source mode is
                    # identified by the clip; sequence mode by the timeline —
                    # comparing both against timeline_guid would call an
                    # isolated clip equal to the sequence containing it.
                    view=(_normalise_view(
                        payload.get("view_mode"),
                        payload.get("clip_guid"),
                        payload.get("timeline_guid"),
                    ) if asserts_view else None),
                    timeline_guid=_short(payload.get("timeline_guid")),
                    frame=ct.get("value"),
                    playback_mode=payload.get("playback_mode"),
                    playing=payload.get("playing"),
                )
            elif event == "PEER_ANNOUNCE":
                update(ev.t, ev.peer, role=payload.get("role"))
            continue

        if (m := _APPLY_VIEW_RE.search(ev.text)):
            update(ev.t, ev.peer,
                   applied_view=f"{m.group(1)} '{m.group(2)}' ({m.group(3)[:8]})",
                   view=f"{m.group(1)}/{m.group(3)[:8]}")
        elif (m := _APPLY_SOURCE_RE.search(ev.text)):
            update(ev.t, ev.peer,
                   applied_view=f"{m.group(3)} '{m.group(1)}' ({m.group(2)[:8]})",
                   view=f"{m.group(3)}/{m.group(2)[:8]}")
        elif (m := _RV_RECV_PLAYBACK_RE.search(ev.text)):
            mode = m.group(5)
            update(
                ev.t, ev.peer,
                applied_frame=float(m.group(3)),
                playing=(m.group(1) == "True"),
                playback_mode=m.group(2),
                # In source mode this line still carries the *sequence* guid, so
                # it would overwrite the clip identity the apply-view line just
                # established. Only sequence mode names the thing on screen.
                view=(f"{mode}/{_short(m.group(4))}" if mode != "source" else None),
            )
        elif (m := _XS_SEEK_RE.search(ev.text)):
            update(ev.t, ev.peer,
                   applied_frame=float(m.group(1)),
                   playing=(m.group(2) == "True"))
        elif (m := _DECLINED_RE.search(ev.text)):
            update(ev.t, ev.peer, note=f"VIEW DECLINED: {m.group(1).strip()}")
        elif (m := _MISMATCH_RE.search(ev.text)):
            update(ev.t, ev.peer,
                   note=f"DROPPED incoming={m.group(3)[:8]} (local={m.group(1)[:8]})")
        elif (m := _UNPUBLISHED_RE.search(ev.text)):
            update(ev.t, ev.peer, note=f"UNPUBLISHED view {m.group(1)[:8]}")
        elif (m := _ELECT_RE.search(ev.text)):
            update(ev.t, ev.peer, note=f"host→{m.group(2)[:8]} (self={m.group(3)})")
        elif (m := _ROLE_STRIP_RE.search(ev.text)):
            update(ev.t, ev.peer, note=f"role {m.group(1)}: fields stripped")
        elif (m := _REFUSED_RE.search(ev.text)):
            update(ev.t, ev.peer, note=f"role {m.group(2)}: refused {m.group(1)}")

    return out


def view_state(events: list[Event], peers: list[str]) -> None:
    changes = build_state(events)
    width = 34
    print("time         " + "".join(p[:width].ljust(width + 2) for p in peers))
    print("-" * (13 + (width + 2) * len(peers)))
    for t, peer, changed in changes:
        cells = []
        for p in peers:
            if p != peer:
                cells.append("".ljust(width + 2))
                continue
            desc = " ".join(f"{k}={v}" for k, v in changed.items())
            cells.append(desc[:width].ljust(width + 2))
        h, rem = divmod(t, 3600)
        m_, s = divmod(rem, 60)
        print(f"{int(h):02d}:{int(m_):02d}:{s:06.3f} " + "".join(cells))


def view_diverge(events: list[Event], peers: list[str], min_seconds: float) -> None:
    """Report intervals where peers disagreed about what is on screen.

    A peer's view comes from whichever is more recent: the view it *asserted*
    (only the host asserts — everyone else has the visibility group stripped) or
    the view it *applied* from a peer. Comparing raw sends instead would flag
    every follower for the whole session, because a follower legitimately claims
    nothing about the view; silence is not disagreement.
    """
    changes = build_state(events)
    latest: dict[str, dict] = {}
    disagreed_since: float | None = None
    disagreement: tuple | None = None
    spans: list[tuple[float, float, tuple]] = []

    def view_of(p):
        st = latest.get(p, {})
        # applied_view wins when it is the more recent of the two, which is
        # tracked by ordering: build_state emits changes in time order and we
        # overwrite as we go, so the last one written is the latest.
        return st.get("view") or None

    for t, peer, changed in changes:
        latest.setdefault(peer, {}).update(changed)
        views = {p: view_of(p) for p in peers}
        known = [v for v in views.values() if v is not None]
        differs = len(known) == len(peers) and len(set(known)) > 1
        if differs and disagreed_since is None:
            disagreed_since, disagreement = t, tuple(views[p] for p in peers)
        elif not differs and disagreed_since is not None:
            spans.append((disagreed_since, t, disagreement))
            disagreed_since = None

    if disagreed_since is not None:
        spans.append((disagreed_since, changes[-1][0] if changes else disagreed_since,
                      disagreement))

    spans = [s for s in spans if s[1] - s[0] >= min_seconds]
    if not spans:
        print(f"No divergence lasting >= {min_seconds}s. Peers agreed on the view.")
        return

    print(f"{len(spans)} divergence(s) lasting >= {min_seconds}s:\n")
    for start, end, views in spans:
        h, rem = divmod(start, 3600)
        m_, s = divmod(rem, 60)
        print(f"  {int(h):02d}:{int(m_):02d}:{s:06.3f}  for {end - start:6.2f}s")
        for p, v in zip(peers, views):
            print(f"      {p:<10} {v if v else '(no view established yet)'}")
        print()


def view_wire(events: list[Event], peers: list[str]) -> None:
    """Correlate each send with the peers that logged receiving it."""
    sends = [e for e in events if e.direction == "SEND"]
    # Receipts indexed per (source_guid, schema, event, receiving peer), in time
    # order.  A scrub emits dozens of byte-identical messages, so a match must be
    # *consumed*: without that, every send pairs with the same earliest receipt
    # and the reported latencies drift negative.  Sends are walked in time order
    # and receipts are already in time order, so FIFO pairing is the right one.
    pending: dict[tuple, list[Event]] = {}
    for e in events:
        if e.direction == "RECV":
            pending.setdefault((e.source(),) + e.schema() + (e.peer,), []).append(e)
    cursor: dict[tuple, int] = {}

    for s in sends:
        got = []
        for p in peers:
            if p == s.peer:
                continue
            key = (s.source(),) + s.schema() + (p,)
            queue = pending.get(key, [])
            i = cursor.get(key, 0)
            # Skip receipts that predate this send — they belong to an earlier
            # one whose own log line we never saw (a log started mid-session).
            while i < len(queue) and queue[i].t < s.t - 0.005:
                i += 1
            if i < len(queue):
                got.append(f"{p}+{(queue[i].t - s.t) * 1000:.0f}ms")
                cursor[key] = i + 1
            else:
                got.append(f"{p}:NONE")
                cursor[key] = i
        schema, event = s.schema()
        print(f"{s.stamp}  {s.peer:<10} → {schema}/{event:<24} {'  '.join(got)}")


def view_merge(events: list[Event], peers: list[str], show_payload: bool) -> None:
    width = max(len(p) for p in peers)
    for ev in events:
        print(f"{ev.stamp}  {ev.peer.ljust(width)}  {ev.text}")
        for line in ev.extra:
            print(f"{' ' * (12 + width + 4)}{line.strip()}")
        if show_payload and ev.wire is not None:
            payload = ev.wire.get("payload", {}).get("command", {}).get("payload")
            if payload is not None:
                dumped = json.dumps(payload, sort_keys=True)
                print(f"{' ' * (12 + width + 4)}{dumped}")


def _peer_name(spec: str) -> tuple[str, str]:
    if "=" in spec and not os.path.exists(spec):
        name, _, path = spec.partition("=")
        return name, path
    stem = os.path.splitext(os.path.basename(spec))[0]
    for prefix in ("xstudio_", "rv_", "openrv_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem, spec


def _parse_clock(value: str) -> float:
    parts = value.split(":")
    while len(parts) < 3:
        parts.append("0")
    h, m, rest = parts[0], parts[1], parts[2]
    sec, _, ms = rest.partition(".")
    return _seconds(h, m, sec or "0", (ms or "0").ljust(3, "0")[:3])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Merge ori_sync peer logs into one time-ordered view.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage::")[1] if "Usage::" in __doc__ else None,
    )
    ap.add_argument("logs", nargs="+", metavar="[name=]path")
    ap.add_argument("--view", choices=("merge", "state", "diverge", "wire"),
                    default="merge")
    ap.add_argument("--json", action="store_true",
                    help="emit JSONL of every event instead of a rendered view")
    ap.add_argument("--since", metavar="HH:MM:SS")
    ap.add_argument("--until", metavar="HH:MM:SS")
    ap.add_argument("--grep", metavar="REGEX",
                    help="keep only events whose text matches")
    ap.add_argument("--peer", action="append", metavar="NAME",
                    help="restrict to these peers (repeatable)")
    ap.add_argument("--no-wire", action="store_true",
                    help="drop MQ traffic; leaves only the plugins' own lines")
    ap.add_argument("--payload", action="store_true",
                    help="merge view: print each wire message's payload")
    ap.add_argument("--min-seconds", type=float, default=1.0,
                    help="diverge view: ignore disagreements shorter than this")
    args = ap.parse_args(argv)

    events: list[Event] = []
    peers: list[str] = []
    for spec in args.logs:
        name, path = _peer_name(spec)
        if not os.path.exists(path):
            print(f"no such log: {path}", file=sys.stderr)
            return 2
        peers.append(name)
        events.extend(parse_log(path, name))

    if args.peer:
        keep = set(args.peer)
        events = [e for e in events if e.peer in keep]
        peers = [p for p in peers if p in keep]
    if args.no_wire:
        events = [e for e in events if e.wire is None]
    if args.since:
        lo = _parse_clock(args.since)
        events = [e for e in events if e.t >= lo]
    if args.until:
        hi = _parse_clock(args.until)
        events = [e for e in events if e.t <= hi]
    if args.grep:
        rx = re.compile(args.grep)
        events = [e for e in events
                  if rx.search(e.text) or any(rx.search(x) for x in e.extra)]

    # Stable sort: equal stamps keep per-file order, so a send and the receipt
    # it caused do not swap places at millisecond resolution.
    events.sort(key=lambda e: e.t)

    if not events:
        print("no events matched", file=sys.stderr)
        return 1

    if args.json:
        for ev in events:
            print(json.dumps(ev.to_dict()))
        return 0

    {"merge": lambda: view_merge(events, peers, args.payload),
     "state": lambda: view_state(events, peers),
     "diverge": lambda: view_diverge(events, peers, args.min_seconds),
     "wire": lambda: view_wire(events, peers)}[args.view]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
