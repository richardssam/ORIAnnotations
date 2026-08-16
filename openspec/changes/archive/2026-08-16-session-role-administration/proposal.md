## Why

`session-roles` archived on 2026-08-13 with a working role model and **no way to
use it**. A session's policy — the default role, and the memory of who holds
what — can be declared only through `ORI_SESSION_DEFAULT_ROLE` and
`ORI_SESSION_PEER_ROLES`, read once at construction
(`authority.role_policy_from_env`). Setting up a restricted review therefore
means editing the environment of every participant's application before they
launch it, and changing anyone's mind about it means everyone restarts.

The result is that the entire mechanism is inert in practice. Every real session
runs on `default_role: driver`, which is exactly the pre-roles behaviour, because
declaring anything else is not reachable from either application.

The gap was anticipated. The archived proposal carries a *"Deferred to
`session-role-administration`"* section naming this change and listing what it
must carry, written so the follow-on could be picked up from a list rather than
rediscovered. This change is that follow-on.

The motivating case is a large review with a **second person handling
administration** — putting arriving participants into the right role and
occasionally adding media — so that the person actually driving the review is not
interrupted by session housekeeping. That configuration is expressible in the
existing model *today* as two `driver` peers; what is missing is only the means
to say so.

## What Changes

- **A session declares its default role when it is created**, through the
  existing Create Session dialog in both hosts, instead of only through the
  environment. The value is fixed for the life of the session.
- **`SET_PEER_ROLE`**, a new message naming a target participant and a role.
  Issued by any `driver`; **applied by the target**, which then re-announces, so
  `PEER_ANNOUNCE` remains the single write path into every peer's table.
- **The message is broadcast, not unicast.** Every peer merges the grant into its
  own identity-keyed role memory. This is what makes a grant survive the target's
  reconnection — see "Why broadcast" below.
- **Both session state panels gain a role control per peer row**, enabled only
  while the local peer is a driver.
- **Creating a session with a restrictive default seeds the creator as
  `driver`.** Without this the creator resolves to their own restrictive default
  and is locked out of the session they just started (see "The create-time trap").
- **`session-state-ui`'s "the panel never mutates sync state" requirement is
  replaced.** The *projection* stays strictly read-only; the panel dispatches
  through a named command surface rather than writing into the snapshot dict.

Not breaking. A session that declares no policy still gets `default_role: driver`
and behaves exactly as it does today, and `SET_PEER_ROLE` is never sent in such a
session because there is nothing to change anyone to.

### Any driver may issue a grant

The archived proposal recorded a leaning toward **host only** — "one seat,
already elected, already deterministic", against which "any driver" would give a
session N administrators and "demotion races with no resolution rule". Both
halves of that reasoning have since weakened, and this change takes the other
branch:

1. **Host is the wrong seat for an administrative power.** It is an election
   outcome — `host_rank` prefers xStudio over OpenRV, then GUID ascending, with
   the master breaking ties. Nobody chooses it. Restricting role administration
   to it means the person who may staff the session is whoever the ranking
   happened to land on, which in a mixed session is decided by which application
   each person launched.
2. **`lease-visibility-authority` has since made visibility a lease**, leaving
   election to decide only who holds it when nobody has claimed it. Host is now a
   more transient thing than it was when the leaning was recorded, and a weaker
   candidate for a standing authority.
3. **The race resolves itself.** Because a grant is applied by its *target*, each
   participant is a single serialization point for grants about them. Concurrent
   grants converge: the winner is arbitrary, the outcome is identical on every
   peer, and the target's own `PEER_ANNOUNCE` is what everyone reads.

The remaining hazard is mutual or accidental demotion of the last driver. It is
left to **`session-roles` D7**: the session becomes driverless, the condition is
already reported in both panels, and "Become Controller" is already offered as
the exit. No core-side "you may not demote the last driver" guard is added — it
would need a peer-table count that can be stale, and the recovery it duplicates
already ships. The panel SHOULD confirm before a demotion that would empty the
session of drivers; that is a courtesy in the view, not a rule in the core.

D7's self-elevation gate is **unchanged** and stays gated on the driverless
condition alone. The archived design flagged that putting a role control on the
same panel makes that distinction easy to blur; it is called out here so the
guard is deliberate.

### Why broadcast rather than unicast

The deferral list described a *targeted* message. Making it a broadcast that
names a target costs nothing and solves the reconnection case for free:

```
  SET_PEER_ROLE {target, role, issuer}
    ├── the target      applies it, re-announces
    │                   → PEER_ANNOUNCE remains the sole write path
    │                     into the peer table (rule preserved)
    └── every peer      merges {user: role} into its own _peer_roles
                        → the master's copy is current by construction
                        → adopt_role_policy is already merge-only, so
                          this composes with no change to it
```

The alternative — routing grants through the master so it can update the memory
it ships in `STATE_SNAPSHOT` — adds a hop and fails during a master election. The
broadcast has no such window, because it does not depend on who the master is.

This matters because `role_policy()` is carried **only** in `STATE_SNAPSHOT`,
which is sent only by the master (`if not self.is_master: return`) and only to a
joiner on request. A grant that landed solely in the issuer's memory would never
reach the master, and would evaporate the moment the target's network hiccuped —
failing at precisely the case identity-keyed memory exists to serve.

### The create-time trap

`resolve_own_role` applies the session's memory first and the default second:

```python
remembered = self._peer_roles.get(user) if user else None
role = authority.normalise_role(remembered or self._default_role)
```

A creator who declares `default_role: viewer` is not in `_peer_roles`, so they
resolve to `viewer` — a viewer in the session they just started, in a session
that now reports itself driverless. It is self-recovering, because D7 offers
"Become Controller", but requiring the organiser to recover from their own setup
choice is not acceptable as designed behaviour.

Declaring a restrictive default at creation SHALL therefore seed the creator's
identity into the role memory as `driver`. Named here because the obvious
implementation — passing `default_role=` to the constructor — walks straight into
it.

## Capabilities

### New Capabilities

- `session-role-administration`: Granting a participant a role during a session —
  who may issue a grant, how it is carried, which peer applies it, and how it
  reaches the session's identity-keyed memory on every peer so that it survives
  the target's reconnection.

### Modified Capabilities

- `session-role-config`: The default role is declared when the session is
  created, through the host application, rather than only through the
  environment; it is fixed for the life of the session; and declaring a
  restrictive default seeds the creator as a driver. The identity-keyed memory
  gains a second writer — a grant — alongside the policy a joiner adopts.
- `session-state-ui`: The requirement that the panel never mutates sync state is
  replaced by one that keeps the projection read-only while permitting the panel
  to dispatch commands through a named surface; the panel gains a per-row role
  control gated on the local peer's role.
- `protocol-message-docs`: The new message is documented on the same terms as
  the ownership and identity messages already are.

## Impact

- **Core**: `authority.py` (grant validation against the same role table the
  broadcast guard uses), `manager.py` (`_peer_roles` gains a grant writer;
  `set_peer_role` issue and receive paths; the creator-seeding rule),
  `protocol_messages.py` (one new message class).
- **Session state projection**: `session_state.py` gains whatever the panel needs
  to decide whether to *offer* the control. It stays read-only.
- **Hosts**: `SessionDialog.qml` and OpenRV's `session_dialog` gain a default-role
  control on the create path only. Both `SessionStatePanel` implementations gain
  a per-row role control and a command dispatch — `python_callback` on click in
  xStudio (as `SessionDialog.qml` already does; it must stay off the polling
  path, which is why display remains attribute-bound), a `@Slot` on
  `ui_model.SessionStateModel` in OpenRV.
- **Peer list refresh is already delivered**: a grant changes the target's
  announced role, which changes the peer table, which changes
  `session_state_snapshot`, which both panels already re-render from. No new
  wiring.
- **Packaging**: prefer extending `authority.py` over adding a module. Any new
  core module must be added to `rvplugin/ori_sync/makepackage.csh`'s
  hand-maintained vendoring list, or the RV plugin stays connected and inert.
- **Protocol**: additive and backwards compatible. A peer predating this change
  ignores `SET_PEER_ROLE` and keeps whatever role it resolved.
- **Risk**: low. The mechanism is unreachable in a session that declares no
  policy, and `default_role: driver` remains both the default and the rollback.

## Non-goals

Recorded explicitly so they are not re-litigated:

- **No admission control.** A joiner is never held pending approval. Knowing the
  session name is accepted as the access boundary. This is a threat-model
  decision consistent with what already ships: the broker is unauthenticated,
  identity is self-declared, and under the fanout topology an unadmitted peer
  already receives every message — so a role-layer gate could only ask its own
  client not to render. If a session ever needs outsiders genuinely excluded,
  that is per-session broker credentials, which has no dependency on roles.
- **No join notification.** A driver is not told when someone arrives; they see
  the peer appear in the panel. Deferred, not rejected — it is the natural cue
  for the administrator to act, and it composes cleanly later.
- **No fourth role.** The administrator is simply a second `driver`. A narrower
  "producer" profile — structure without visibility or annotation, so that
  someone adding media cannot change the shot or annotate — was considered and
  deferred. It costs nothing to defer: because roles are evaluated against
  **field groups**, adding it later is one row in `ROLE_PERMISSIONS` plus a name,
  with no new enforcement point, no protocol change, and no election change
  (`host_candidates` already filters on `role_permits(..., VISIBILITY)`, so such a
  role would be host-ineligible for free). The accepted cost of deferring is
  stated: an administrator who is a full driver can take the position lease, so
  if adding media moves their local playhead, the room follows them off the
  driver's frame. That is the signal to add the profile.
- **No requested-vs-declared display.** Enforcement is send-side and cooperative,
  so a demoted peer running modified or older code keeps its role in the panel.
  There is no deployed older code today, so the panel shows the declared role
  alone. `session-role-model`'s existing requirement — that the presentation must
  not assert a peer is *prevented* from acting outside its role — continues to
  govern and is not weakened by adding a control.
- **No tokens.** Identity-keyed memory plus administrator grants covers what
  tokens were left serving. Nothing here forecloses them.
- **No mid-session change to `default_role`.** It is a create-time declaration.
  This is narrower than the deferral list anticipated, and it removes a
  propagation path entirely: the default is set once on the creator, who is the
  initial master, and ships unchanged in every `STATE_SNAPSHOT` thereafter.
