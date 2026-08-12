## Context

`session-state-ui` (archived 2026-08-10) shipped a shared, Qt-free projection of session state and a panel over it in both hosts. It renders every peer as `app` plus a GUID prefix, because that is all the protocol carries. `session-roles` will put a role beside each of those rows and, in its administration phase, a control that changes one — at which point "which of these three `openrv` peers is the supervisor" stops being a debugging inconvenience and becomes the thing the feature is for.

Relevant current state:

- **`PEER_ANNOUNCE` carries `peer_guid`, `app`, `capabilities`** and nothing else, into `SyncManager._peers`.
- **Two paths populate the peer table**, not one: periodic announcements, and the roster in `STATE_SNAPSHOT` (`_peer_roster()` → `adopt_peers()`). The answer-to-announce cascade was deliberately removed — a joiner learns quiet peers from the roster. Any new peer field must travel both ways or it is missing on exactly the peers a joiner has not heard from yet.
- **`session_state_snapshot` is spec'd read-only** and is where host-visible derivations belong: "a panel that needs state the manager does not hold SHALL obtain it from the host … rather than by extending the sync core".
- **The panel already has a Debug Mode** separating end-user information from mechanism.
- **The two host plugins have measurably drifted** on hand-replicated behaviour, which is why display rules are derived once rather than formatted per host.

## Goals / Non-Goals

**Goals:**

- A peer is identifiable by a human name in both hosts' panels and in logs.
- The source of identity is replaceable — local machine now, authenticated login later — without touching the protocol, the peer table, or either panel.
- Identity reaches a joiner as reliably as it reaches a peer that was already connected.
- A peer that sends no identity still renders as well as it does today.

**Non-Goals:**

- Authentication. Identity is self-declared, exactly like `app` is today.
- Authorization. Nothing in this change reads identity to decide what a peer may do; that is `session-roles`.
- Cross-session persistent identity, accounts, or a user database.
- Presence beyond what `peer-departure` already provides.

## Decisions

### D1: Identity is a peer-table field set, resolved once at session start

Identity is resolved when the session starts and then held, not recomputed per message: it cannot change mid-session (the override is a join-time decision, D4), and a per-message lookup would put `pwd`/`socket` calls on the announce path for no gain.

It is stored in the same peer entry as `app` and `capabilities`, and propagates over **both** existing paths — `PEER_ANNOUNCE` and `_peer_roster()`/`adopt_peers()`. This is stated as a decision rather than left to implementation because the roster path is the easy one to miss, and missing it produces a defect that only appears for peers that were quiet when someone joined.

- *Alternative — a new `PEER_IDENTITY` message:* rejected. It would duplicate a message that already exists, with its own cadence and storm risk, and would still need a roster path for late joiners. The same argument `session-roles` D5 makes for role.

### D2: A provider seam, not a hardcoded lookup

`identity.py` exposes one function, `local_identity()`, returning the field set. Everything else — the message, the peer table, the projection, both panels — is provider-agnostic.

Field names map onto standard identity-provider claims (`preferred_username`, `given_name`, `family_name`) so that an authenticated source is a swap of that function, not a schema migration. The `source` field records provenance (`"local"`, `"override"`, later `"oidc"`), which is what lets a later change tell a verified identity from a typed one without re-deriving it from the presence of other fields.

Local resolution is best-effort by design: `getpass.getuser()` and `socket.gethostname()` are reliable, `pw_gecos` is not (it holds a full name on macOS, and anything from a full name to an empty string to a comma-separated stanza on Linux). Names are therefore optional and the display rule degrades (D3) rather than the resolver guessing.

### D3: The display string is derived in the projection, never sent

`session_state_snapshot` computes `display_name` as `"First Last"` → `user` → `app`, and both panels render that field.

Sending a formatted string instead would let a peer send an arbitrary label, and would let the two hosts drift on how they render a half-populated identity — the exact class of divergence `session-state-ui` created the shared projection to prevent. The wire carries facts; the projection carries presentation.

This also keeps the projection's read-only contract intact: deriving a display name reads the peer table and adds nothing to `SyncManager`.

### D4: Identity is self-declared and overridable, and that is not a defect

The join dialog offers an identity override, and an env override serves the test harness. Neither is verified.

This is the same trust model everything else in the protocol already has: `app` is self-declared and ranks a peer for host election; the broker is unauthenticated. An override exists for real cases — shared workstations, render-farm seats, machines named after their previous owner — and its cost is that a user can type someone else's name.

That cost is worth stating precisely because `session-roles` intends to key role memory on `user`: **once it does, typing another person's username inherits their remembered role.** Acceptable under that change's declared non-goal of adversarial security, and no weaker than the plaintext token it replaces, but it is a property that must be decided rather than inherited. If a session ever needs identity it can trust, the answer is D2's seam — an authenticated provider — not verification bolted onto a typed field.

### D5: `user` and `host` are Debug Mode fields

The row shows the display name and application. Account name and machine name go behind the toggle `session-state-ui` already ships.

Two reasons, one practical and one not: a screening with external vendors should not put everyone's machine hostname on everyone's screen by default, and a 30-row list is more readable without two extra columns that only matter when something is wrong.

### D6: Absent identity degrades to today's rendering

A peer that sends no identity — older code, or a client that has none — renders as `app` plus GUID, which is exactly what every peer renders as today. Never a blank row, never "Unknown".

This follows the compatibility convention already established for `host_guid` and the ownership section: omit when unset, ignore an absence on receipt rather than treating it as a value. A peer predating this change must not become invisible or anonymous in a panel that previously showed it.

## Risks / Trade-offs

- **[Identity is trusted more than it deserves]** A name in a panel reads as verified even though nothing verifies it. → `source` records provenance; the trust model is stated in the spec; D2 leaves an authenticated provider as the answer if that changes. Worth re-reading before `session-roles` keys anything durable on `user` (D4).
- **[The roster path is forgotten]** Identity added to `PEER_ANNOUNCE` alone would be missing precisely for peers a joiner has not yet heard from — the peers most in need of a name. → D1 makes both paths a decision; the scenario belongs in the spec, not only in a task.
- **[`pw_gecos` returns something unhelpful on Linux]** It may hold a comma-separated stanza, an empty string, or an unrelated comment. → Names are optional, the display rule degrades through `user` to `app`, and the resolver does not attempt to parse beyond taking the first comma-delimited segment.
- **[Hostnames leak into a shared screening]** → D5 puts `host` behind Debug Mode. Not a security control; a default-view decision.
- **[One user on two machines]** Both peers carry the same `user`, different `host`. Harmless here; it matters to `session-roles` if role memory keys on `user` alone, which is that change's decision to make and is flagged in its D3.
- **[A landed mechanism is silently absent in RV]** `identity.py` must be in `makepackage.csh`'s vendoring list; `__init__.py` swallows `ImportError`, so an omission leaves the plugin connected and inert. This exact fault shipped with `authority.py`. → Add it in the same commit; confirm from the startup banner which copy RV loaded.

## Migration Plan

1. **Core**: `identity.py`, identity on `PeerAnnounce`, on `_peer_roster()`/`adopt_peers()`, and in `session_state_snapshot` with the derived `display_name`. Vendoring list updated in the same commit. Expose the display name in the test inspector's peer state.
2. **Hosts**: both panels render display name, with `user`/`host` under Debug Mode; both session dialogs gain the override field.

Backwards compatible throughout: step 1 alone changes nothing visible, and a session mixing old and new peers renders the old ones as it does today (D6).

Rollback: the fields are additive and ignored when absent, so reverting is removing a column.

## Open Questions

- Should `email` be carried now, unused, or added with the authenticated provider that would populate it? Leaning toward adding it with the provider — an always-empty field invites someone to derive a display name from it.
- Should the identity-scoped key be `user` or `user@host`? Deferred to `session-roles` D3, which is the first thing that needs a key; this change carries both fields either way.
- Should `sync_viewer` declare an identity, or is an anonymous observer row correct for it? It already declares `capabilities=[]` to stay out of election; the parallel choice here is probably a fixed `"Sync Viewer"` display name.
