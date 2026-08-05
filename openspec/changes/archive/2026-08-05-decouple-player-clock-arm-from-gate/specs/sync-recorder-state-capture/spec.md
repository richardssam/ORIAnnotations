## ADDED Requirements

### Requirement: Explicit Clock Arming Decoupled From Peer-Join Gate
The player's peer-join gate (`wait_for_peer`) SHALL track peer-snapshot delivery only — it SHALL NOT itself start the recording's logical playback clock (`_play_start_time`). Starting the clock SHALL be a separate, explicit action the caller performs once its own readiness check passes, so the recording's t=0 anchor can be defined by a caller-chosen readiness signal instead of the gate's own.

The two signals SHALL be combined, not substituted for one another: the clock arms once the gate's own conditions **and** an explicit arming request have both occurred, whichever comes last. Arming SHALL NOT bypass the gate, since dispatching before peers have received their initial snapshot is a worse race than the one this requirement closes.

#### Scenario: Peer-join gate clearing does not arm the clock
- **WHEN** the peer-join gate's own conditions are satisfied (a snapshot has been delivered to every required peer and any configured post-snapshot delay has elapsed)
- **THEN** the player SHALL NOT start the logical clock on its own
- **AND** the clock SHALL remain unarmed until the caller explicitly arms it

#### Scenario: Arming before the gate clears does not start the clock early
- **WHEN** the caller arms the clock before the peer-join gate's own conditions are satisfied
- **THEN** the clock SHALL remain unarmed until those gate conditions are also satisfied
- **AND** the clock SHALL arm at that later point, not at the moment the arming request was made

#### Scenario: Explicit arming starts the clock
- **WHEN** the caller explicitly arms the clock and the peer-join gate's own conditions are already satisfied
- **THEN** the player SHALL set its logical clock anchor to the current time
- **AND** subsequent recorded-event dispatch SHALL be timed relative to that anchor, exactly as today's dispatch is timed relative to the point the gate used to arm it implicitly

#### Scenario: The moment the clock arms is reported
- **WHEN** the player arms its logical clock, or is ticked while the gate has cleared but no arming request has been made
- **THEN** it SHALL log that state, so the interval between gate-clearing and arming is observable rather than silent
- **AND** a caller that never arms SHALL be diagnosable from that log rather than presenting only as an unexplained stall

#### Scenario: The network is still serviced before the clock is armed
- **WHEN** the peer-join gate has cleared but the clock has not yet been explicitly armed
- **THEN** the player SHALL continue servicing incoming network requests (peer joins, state requests)
- **AND** SHALL NOT dispatch any recorded event

#### Scenario: A caller that never arms the clock sees no dispatch
- **WHEN** `wait_for_peer` is requested and the caller never explicitly arms the clock
- **THEN** the player SHALL continue servicing the network indefinitely without dispatching any recorded event, the same way an unarmed clock behaves before the caller's readiness check has passed
