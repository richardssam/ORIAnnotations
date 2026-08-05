## MODIFIED Requirements

### Requirement: Thin RV Applier With Append and Reconcile Modes

The codec SHALL provide the only RV-touching function, `apply_specs(specs, commands, *, rv_node, frame, mode, start_id=None)`, which writes `PaintNodeSpec` entries (each carrying its own `user` field) to RV paint nodes and maintains the per-frame `order` list. It SHALL support `mode="append"` (create fresh nodes and append to order) and `mode="reconcile"` (match existing nodes by `uuid`, update in place when found, add when not, and prune managed nodes whose uuid is absent from the incoming set).

Reconcile mode's kind-inferring prune (deriving which kinds it may prune from the kinds actually present in `specs`) SHALL remain unchanged for a non-empty `specs` list — callers routinely reconcile one kind (or even a single item) at a time, and a spec list that says nothing about a kind MUST NOT be read as "no items of that kind exist anymore." Annotation deletion that empties a frame entirely SHALL NOT be expressed by calling `apply_specs` with an empty `specs` list (which reconcile mode cannot distinguish from "no opinion, prune nothing"); callers needing a full clear SHALL clear the frame's `order` property directly instead, outside `apply_specs`.

#### Scenario: Append mode adds nodes

- **WHEN** `apply_specs` is called with `mode="append"` for a frame that has no existing managed nodes
- **THEN** it SHALL create the paint child nodes and set the frame `order` to reference them

#### Scenario: Reconcile updates in place by uuid

- **WHEN** `apply_specs` is called with `mode="reconcile"` and a spec whose `uuid` matches an existing node in the frame order
- **THEN** it SHALL update that node's properties in place rather than appending a duplicate

#### Scenario: Reconcile prunes deleted annotations

- **WHEN** `apply_specs` is called with `mode="reconcile"` and an existing managed node's `uuid` is not present among the incoming specs
- **THEN** that node SHALL be removed from the frame `order`

#### Scenario: Reconcile with an empty specs list prunes nothing

- **WHEN** `apply_specs` is called with `mode="reconcile"` and `specs` is an empty list
- **THEN** no existing managed node SHALL be removed from the frame `order`
- **AND** a caller needing to fully clear the frame MUST NOT rely on this call to do so
