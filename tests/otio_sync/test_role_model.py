"""Tests for the session role permission matrix.

The table in ``authority`` is the single statement of what each role may emit,
consulted by both enforcement points — the broadcast guard and the lease claim
gate — so it is asserted here once, row by row, against the matrix in the
``session-roles`` proposal.  The two enforcement points are tested against
behaviour in ``test_role_enforcement.py``; this file is about the table itself.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402


# (role, group, destructive, permitted) — one case per row of the proposal's
# per-field-group permission matrix, for all three roles.
MATRIX = [
    # Playback settings — view mode / clip identity (visibility)
    (authority.DRIVER, authority.VISIBILITY, False, True),
    (authority.REVIEWER, authority.VISIBILITY, False, False),
    (authority.VIEWER, authority.VISIBILITY, False, False),
    # Playback settings — current time / playing / playback mode (position)
    (authority.DRIVER, authority.POSITION, False, True),
    (authority.REVIEWER, authority.POSITION, False, True),
    (authority.VIEWER, authority.POSITION, False, False),
    # Display state — per-peer presentation, open to every role
    (authority.DRIVER, authority.DISPLAY, False, True),
    (authority.REVIEWER, authority.DISPLAY, False, True),
    (authority.VIEWER, authority.DISPLAY, False, True),
    # Timeline add/remove/replace/rename, property set, child edits
    (authority.DRIVER, authority.STRUCTURE, False, True),
    (authority.REVIEWER, authority.STRUCTURE, False, False),
    (authority.VIEWER, authority.STRUCTURE, False, False),
    # Annotation strokes and annotation-track inserts
    (authority.DRIVER, authority.ANNOTATION, False, True),
    (authority.REVIEWER, authority.ANNOTATION, False, True),
    (authority.VIEWER, authority.ANNOTATION, False, False),
    # Destructive annotation operations (clear all paint) — driver only
    (authority.DRIVER, authority.ANNOTATION, True, True),
    (authority.REVIEWER, authority.ANNOTATION, True, False),
    (authority.VIEWER, authority.ANNOTATION, True, False),
]


@pytest.mark.parametrize("role,group,destructive,permitted", MATRIX)
def test_permission_matrix(role, group, destructive, permitted):
    assert authority.role_permits(role, group, destructive=destructive) is permitted


def test_three_roles_and_a_permissive_default():
    assert authority.ROLES == (authority.DRIVER, authority.REVIEWER, authority.VIEWER)
    assert set(authority.ROLE_PERMISSIONS) == set(authority.ROLES)
    # The default is the *permissive* one: the whole mechanism is inert until a
    # session opts in, and that default is also the rollback.
    assert authority.DEFAULT_ROLE == authority.DRIVER


def test_unknown_role_resolves_permissively():
    """Unknown is never the restrictive value.

    A peer running code that predates roles, an entry adopted from an older
    peer's roster, or a typo in a policy must not silently become the most
    restricted participant in the session — the ``xs_flat_playlist``
    ``media_exists`` default got exactly this wrong once already.
    """
    for unknown in (None, "", "  ", "presenter", "admin"):
        assert authority.normalise_role(unknown) == authority.DEFAULT_ROLE
        assert authority.role_permits(unknown, authority.VISIBILITY) is True
        assert authority.role_permits(unknown, authority.STRUCTURE) is True


def test_role_names_are_case_and_whitespace_insensitive():
    assert authority.normalise_role(" Driver ") == authority.DRIVER
    assert authority.normalise_role("VIEWER") == authority.VIEWER


def test_an_ungated_broadcast_is_permitted_to_every_role():
    """Session plumbing carries no user intent and is never role-gated."""
    for role in authority.ROLES:
        assert authority.role_permits(role, None) is True
        assert authority.role_group_for("broadcast_master_discovery") is None


def test_display_is_its_own_group_not_the_position_category():
    """Display is categorised ``position`` for leases and is open to every role.

    The two tables answer different questions, and conflating them would strip a
    viewer's own exposure change — which is not a session event at all.
    """
    assert authority.category_for("broadcast_display_state") == authority.POSITION
    assert authority.role_group_for("broadcast_display_state") == authority.DISPLAY
    assert authority.role_permits(authority.VIEWER, authority.DISPLAY) is True
    assert authority.role_permits(authority.VIEWER, authority.POSITION) is False


def test_every_gated_broadcast_method_has_a_role_group():
    """A new broadcast method cannot be gated by category and not by role."""
    for method, category in authority.BROADCAST_CATEGORIES.items():
        group = authority.role_group_for(method)
        assert group is not None, method
        assert group in {authority.VISIBILITY, authority.POSITION, authority.DISPLAY,
                         authority.ANNOTATION, authority.STRUCTURE}
        # Display is the one deliberate divergence; everything else agrees.
        if method != "broadcast_display_state":
            assert group == category


def test_only_driver_may_administer():
    assert authority.role_may_administer(authority.DRIVER) is True
    assert authority.role_may_administer(authority.REVIEWER) is False
    assert authority.role_may_administer(authority.VIEWER) is False


def test_unknown_role_administers_permissively():
    # Absent/unknown resolves via normalise_role() to DEFAULT_ROLE (driver),
    # never to the most restrictive outcome.
    assert authority.DEFAULT_ROLE == authority.DRIVER
    assert authority.role_may_administer(None) is True
    assert authority.role_may_administer("nonsense") is True


def test_administration_is_inert_on_the_broadcast_path():
    """ADMINISTRATION gates set_peer_role itself, not a stripped field group —
    it must never surface through role_group_for or strip_role_fields."""
    for method in authority.ROLE_GROUPS:
        assert authority.role_group_for(method) != authority.ADMINISTRATION

    before = _state()
    for role in authority.ROLES:
        out = authority.strip_role_fields(_state(), role)
        # Only VISIBILITY/POSITION fields are ever candidates for stripping;
        # administration permission does not change this output.
        assert out == authority.strip_role_fields(before, role)


def _state():
    return {
        "view_mode": "sequence",
        "clip_guid": "clip-1",
        "current_time": 42,
        "playing": True,
        "playback_mode": "loop",
        "sync_timestamp": 1.0,
    }


def test_strip_role_fields_leaves_a_driver_untouched():
    assert authority.strip_role_fields(_state(), authority.DRIVER) == _state()


def test_reviewer_keeps_position_and_loses_visibility():
    out = authority.strip_role_fields(_state(), authority.REVIEWER)

    assert authority.asserts_position(out) is True
    assert authority.asserts_visibility(out) is False


def test_viewer_loses_both_playback_groups():
    out = authority.strip_role_fields(_state(), authority.VIEWER)

    assert authority.asserts_position(out) is False
    assert authority.asserts_visibility(out) is False
    # Non-authority fields are not collateral damage.
    assert out["sync_timestamp"] == 1.0


def test_a_stripped_group_never_leaves_partially():
    """A message keeping ``clip_guid`` while dropping ``view_mode`` still asserts
    what the session should look at — the failure whole-group stripping exists to
    make unavailable."""
    for role in authority.ROLES:
        out = authority.strip_role_fields(_state(), role)
        for group in (authority.VISIBILITY_FIELDS, authority.POSITION_FIELDS):
            present = [f for f in group if f in out]
            assert present == [] or present == list(group)


def test_strip_role_fields_does_not_mutate_its_input():
    state = _state()
    authority.strip_role_fields(state, authority.VIEWER)
    assert state == _state()


def test_kill_switch_is_read_per_call(monkeypatch):
    monkeypatch.delenv(authority.ROLE_ENFORCEMENT_ENV, raising=False)
    assert authority.role_enforcement_enabled() is True
    monkeypatch.setenv(authority.ROLE_ENFORCEMENT_ENV, "0")
    assert authority.role_enforcement_enabled() is False
    monkeypatch.setenv(authority.ROLE_ENFORCEMENT_ENV, "1")
    assert authority.role_enforcement_enabled() is True
