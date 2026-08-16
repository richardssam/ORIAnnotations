## Context

See `proposal.md` — *Why* for the motivation and for the two decisions already
taken there (any driver may grant; the grant is broadcast rather than unicast).
This document covers only what those decisions leave open in the code.

The relevant existing shape:

- `authority.ROLE_PERMISSIONS` maps role → the set of **field groups** that role
  may emit. `role_permits()` is the single predicate behind both the broadcast
  guard and the lease-claim gate.
- `SyncManager._peer_roles` is `{user: role}`, keyed on `identity["user"]`. It
  is written today by exactly one method — `adopt_role_policy()` — which merges
  additively, ignores an empty policy, re-runs `resolve_own_role()`,
  re-announces only if this peer's own role moved, and enqueues a host election.
- `resolve_own_role()` reads `_peer_roles` first and `_default_role` second.
- `RabbitMQNetwork` **discards messages whose `source_guid` is this peer**
  (`rabbitmq_network.py:185`). A sender never receives its own broadcast.
- xStudio's panel binds to a JSON attribute pushed from the poll thread; OpenRV's
  binds to `PySide6` models polling the same projection. Both read
  `session_state.session_state_snapshot`.

## Goals / Non-Goals

**Goals:**

- One writer into `_peer_roles`, shared by policy adoption and grants.
- One role table behind "may drive" and "may administer".
- The create-time default reaches the manager without either host application
  re-deriving the creator-seeding rule.
- The panel gains an action without the projection gaining a write path.

**Non-Goals:**

- Any change to `resolve_own_role`'s assignment order, to `role_permits`'
  semantics, or to the D7 self-elevation gate.
- Any change to how role propagates. `PEER_ANNOUNCE` stays the sole write path
  into the peer table; this change adds no second one.
- A new core module. See D8.

## Decisions

### D1 — `SET_PEER_ROLE` on `LiveSession.1`, targeting a participant

One new `ProtocolMessage` subclass, `SetPeerRole`, schema `LiveSession.1`, event
`SET_PEER_ROLE`, carrying:

| field | meaning |
|---|---|
| `user` | target participant identity — the `_peer_roles` key |
| `role` | one of `driver` / `reviewer` / `viewer` |
| `issuer_guid` | GUID of the peer that issued it, for logging and provenance |

`user` rather than a peer GUID because the memory every peer merges into is
identity-keyed; a GUID-addressed grant could not survive the reconnection the
memory exists to serve. `issuer_guid` is recorded, not enforced on: enforcement
is send-side (see D3).

It joins `NON_DISPLAY_EVENTS` in `manager.py`. That set is a denylist of session
mechanics excluded from display-state attribution, and a grant carries no
display state; leaving it out would let a grant be attributed as a display
change.

**Alternative rejected:** a field on `PEER_ANNOUNCE`. An announcement describes
its *sender*; a grant describes someone else. Overloading it would make the
announcement handler the place where one peer writes another peer's role, which
is precisely the second writer the spec forbids.

### D2 — The receive path is `adopt_role_policy`, not a new merge

`_h_set_peer_role` calls
`self.adopt_role_policy({"peer_roles": {msg.user: msg.role}})`.

Everything a grant needs, that method already does and already has tests: an
additive merge, `normalise_role`, re-resolution of this peer's own role,
re-announcement *only* when this peer's role actually moved, and a host-election
request. It is exactly the "applied by the target, which then re-announces"
behaviour, obtained by reuse rather than by a parallel implementation.

Consequences that fall out for free:

- A non-target peer merges and stops — no announcement, no election churn.
- A grant naming a role the target already holds changes nothing, so no
  announcement is emitted. The spec's idempotence requirement needs no code.
- Two peers holding the same identity both apply it, because both resolve their
  own role from the same key.

The only addition is a distinct log line, so a grant and a snapshot adoption are
distinguishable in a trace.

**Alternative rejected:** a dedicated `apply_grant()`. It would duplicate the
merge and the re-announce condition, and the two would drift the first time one
was touched — the failure mode this codebase has already had between hosts.

### D3 — Administration is a row in the existing role table

Add `ADMINISTRATION = "administration"` as a permission group in
`authority.ROLE_PERMISSIONS`, granted to `driver` only, and gate the issue path
on `role_permits(self._self_role, authority.ADMINISTRATION)`.

This satisfies "the same role table the broadcast guard uses" literally rather
than by resemblance, and it makes the deferred fourth role (proposal —
*Non-goals*) one more cell in the same table.

Two properties of the group must hold and are asserted by test rather than left
implicit: no `broadcast_*` method maps to it via `role_group_for()`, and
`strip_role_fields()` does not consult it. It is a permission that gates a
whole message, not a field group that is stripped from one.

### D4 — The issuer applies its own grant locally before sending

The network layer drops messages whose `source_guid` is this peer, so the issuer
never receives its own broadcast. `set_peer_role()` therefore performs the same
local `adopt_role_policy` merge *and then* sends.

Stated because it is easy to miss and its failure is quiet and asymmetric: a
driver granting itself a role, or granting one to a participant while it is the
master, would see nothing happen locally while every other peer applied it — and
if the issuer is the master, the grant would be missing from the very snapshot
that carries policy to later joiners.

Order is local-then-send so that a send failure still leaves this peer
consistent with the grant it believes it issued; the grant is re-carried to
everyone else by the master's snapshot in any case.

### D5 — Creator seeding is gated on an explicit `seed_creator` flag

`SyncManager.__init__` gains `seed_creator: bool = False`. When `True` and
`default_role` names a role other than `driver`, the constructor seeds
`_peer_roles[own identity user] = driver` before the existing
`resolve_own_role()` call.

**Revised from the original plan of inferring this from `default_role` merely
being passed.** That was tried first and reverted on contact with the test
suite: `default_role=` is *already* the established way to pin a constructed
peer's own effective role for reasons that have nothing to do with starting a
session — `test_role_enforcement.py::_manager(role=...)` and several cases in
`test_role_policy.py` ("an unrecognised participant receives the default")
construct a manager with a non-driver `default_role` specifically to assert it
resolves to that default, unseeded. Inferring seeding from the argument's mere
presence silently reinterpreted every such construction as "this peer created
the session," seeding an identity that every one of those tests expects to
remain unseeded — 23 failures across four files not otherwise touched by this
change, none of them about creation semantics. The parameter was already
overloaded before this change reused it a third way; inference could not
distinguish the three.

An explicit flag is the fix, not the rejected alternative it once was: the two
host applications pass `seed_creator=True` together with `default_role=` on
their create path only, and pass neither on join. Every other construction —
tests included — is unaffected because the flag defaults to `False`.

`ORI_SESSION_DEFAULT_ROLE` still never seeds, flag or no flag: the environment
variable is read by *every* peer that has it set, joiners included, so seeding
on it would make every env-configured peer a driver and destroy the mechanism
regardless of how the constructor argument is gated.

### D6 — Create-time default reaches the manager through the existing connect paths

- **xStudio**: `SessionDialog.qml` already carries `dialog.mode` (`create` /
  `join`) and already dispatches through `python_callback("do_session_connect")`.
  A role combo is shown only when `mode === "create"` and adds a `default_role`
  key to that payload; `do_session_connect` forwards it to
  `connect_to_session`, which passes it to the `SyncManager` constructor.
- **OpenRV**: `utils.session_dialog(title)` gains an optional role combo,
  returning a 4-tuple. `do_create_session` shows it; `do_join_session` does not.

Both hosts show the control on the create path only, because the value has no
meaning on join (the session already has one and sends it in `STATE_SNAPSHOT`).

### D7 — Panel dispatch: a named command per host, no shared view code

- **xStudio**: the role control calls
  `python_callback("set_peer_role", {user, role})` on click. It stays off the
  polling path — display remains attribute-bound — because `python_callback`
  blocks xStudio's Qt main thread. The plugin method calls
  `manager.set_peer_role(...)` directly, matching `_menu_become_controller`
  rather than `_menu_leave_session`: like `elect_role_to_driver`, this is a
  role write plus an announce plus an election *request*, which is what the
  existing self-elevation path already does from the Qt thread. Routing it
  through `_cmd_queue` instead would be defensible; it is rejected only so the
  two role actions do not use two different threading conventions.
- **OpenRV**: a `@Slot(str, str)` on `ui_model.SessionStateModel` calling the
  same manager method. `SessionStateModel` gains its first slot; the models
  remain thin wrappers over the projection, and the slot adds no session
  semantics of its own.

The projection gains `may_administer_roles` (top level, bool). Peer rows already
carry `user`, which is what the panel needs to decide the control is offerable
for a given row, so no per-peer field is added.

### D8 — No new module

Everything lands in `authority.py`, `manager.py`, `protocol_messages.py`,
`session_state.py`, and `ui_model.py`, all of which are already in
`rvplugin/ori_sync/makepackage.csh`'s hand-maintained vendoring list. A new
module omitted from that list leaves the RV plugin connected and silently inert
— a failure this project has shipped before — so the packaging change is avoided
rather than remembered.

## Risks / Trade-offs

**A grant for an absent participant is remembered but never applied** → Intended,
not a gap: the memory is where the grant lives, and the participant resolves
against it on their next join. The panel only offers the control for peers that
are present, so this arises only from a race with departure.

**Concurrent grants for one participant have an arbitrary winner** → Accepted and
specified. The target is the single serialisation point, so every peer converges
on the same value; only *which* value is arbitrary. A last-writer-wins ordering
would need a session clock this protocol does not have.

**A grant can leave the session with no driver** → Deliberately unguarded (see the
spec's rationale: the guard would read a peer table that can be stale). Mitigated
by the panel's confirmation and by the existing driverless report plus
self-elevation exit — both already shipped, both already tested.

**`ADMINISTRATION` is a role-table group that is never stripped** → Mitigated by
tests asserting `role_group_for()` maps no broadcast to it and
`strip_role_fields()` ignores it, so a future "strip every group this role lacks"
loop cannot silently start stripping on it.

**A demoted peer running older or modified code keeps its role** → Inherent to
send-side enforcement; already stated in `session-role-model` and restated in
this change's spec. The panel shows the declared role and asserts nothing more.

**xStudio's `python_callback` blocks the Qt main thread** → Bounded here: it is a
click, not a poll, and the callback returns after a local merge and one publish.
The polling path is untouched.

## Migration Plan

Additive and backwards compatible in both directions:

- A peer predating this change has no `("LiveSession.1", "SET_PEER_ROLE")`
  handler, so the message falls through its dispatch table and is ignored. It
  keeps the role it resolved.
- A peer running this change in a session with no declared policy never issues a
  grant, because `default_role: driver` leaves nothing to change anyone to.

Rollback is the existing one: `default_role: driver` (the default), or
`ORI_SESSION_ROLE_ENFORCEMENT=0` for the broadcast guard. Neither requires a
rebuild.
