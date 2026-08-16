# SPDX-License-Identifier: Apache-2.0
"""Qt models bridging the OTIO Sync Manager state to QML.

OpenRV-only: these are PySide6 objects handed to a ``QQuickView`` running in
the RV process.  xStudio's plugin has no PySide6 to hand them to — it talks to
xStudio's own QML engine over ``python_callback`` — so the two hosts share
:func:`otio_sync_core.session_state.session_state_snapshot` instead of sharing
this module.  Everything below is a thin Qt wrapper over that dict; session
semantics belong there, not here.
"""

import logging

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QTimer,
    Qt,
    Property,
    Signal,
    Slot,
)

from .session_state import session_state_snapshot

#: Poll period for both models.  The manager emits no per-peer or per-lease
#: change events, so the panel samples it instead (design.md: a debug view may
#: lag by one period, but must never wire callbacks into the sync hot path).
POLL_INTERVAL_MS = 500

_logger = logging.getLogger("otio_sync.ui")


class SessionStateModel(QObject):
    """Exposes top-level session state to QML."""

    statusChanged = Signal()
    masterGuidChanged = Signal()
    isHostChanged = Signal()
    isDebugChanged = Signal()
    splitViewChanged = Signal()
    roleChanged = Signal()
    mayAdministerRolesChanged = Signal()
    driverlessChanged = Signal()
    selfHoldsVisibilityChanged = Signal()
    mayHoldVisibilityChanged = Signal()
    joinConfirmationChanged = Signal()
    structureDivergenceChanged = Signal()

    def __init__(self, manager, local_view_provider=None, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._is_debug = False
        # Callable returning the host's own (timeline_guid, clip_guid) — what
        # this application is actually showing, which the manager does not
        # track separately from the shared session state.  Without it the
        # panel simply never reports a split view.
        self._local_view_provider = local_view_provider
        self._snapshot = session_state_snapshot(manager)
        self._is_split = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_state)
        self._timer.start(POLL_INTERVAL_MS)

    def _poll_state(self):
        previous = self._snapshot
        self._snapshot = session_state_snapshot(self._manager)

        if self._snapshot["status"] != previous["status"]:
            self.statusChanged.emit()
        if self._snapshot["master_guid"] != previous["master_guid"]:
            self.masterGuidChanged.emit()
        if self._snapshot["is_host"] != previous["is_host"]:
            self.isHostChanged.emit()
        if self._snapshot["self_role"] != previous["self_role"]:
            self.roleChanged.emit()
        if self._snapshot["may_administer_roles"] != previous["may_administer_roles"]:
            self.mayAdministerRolesChanged.emit()
        if self._snapshot["driverless"] != previous["driverless"]:
            self.driverlessChanged.emit()
        if self._snapshot["self_holds_visibility"] != previous["self_holds_visibility"]:
            self.selfHoldsVisibilityChanged.emit()
        if self._snapshot["may_hold_visibility"] != previous["may_hold_visibility"]:
            self.mayHoldVisibilityChanged.emit()
        if self._snapshot["join_confirmation"] != previous["join_confirmation"]:
            self.joinConfirmationChanged.emit()
        if self._snapshot["structure_divergence"] != previous["structure_divergence"]:
            self.structureDivergenceChanged.emit()

        is_split = self._compute_split_view()
        if is_split != self._is_split:
            self._is_split = is_split
            self.splitViewChanged.emit()

    def _compute_split_view(self):
        """Whether this host is looking at something other than the session's view."""
        if self._snapshot["is_host"] or self._snapshot["status"] != "SYNCED":
            return False
        if self._local_view_provider is None:
            return False

        try:
            local_tl, local_clip = self._local_view_provider()
        except Exception as exc:  # a debug panel must never break the host
            _logger.debug("local_view_provider failed: %s", exc)
            return False

        shared_tl = self._snapshot["active_timeline_guid"]
        if local_tl and shared_tl and local_tl != shared_tl:
            return True

        shared_clip = self._snapshot["selected_clip_guid"]
        if local_clip and shared_clip and local_clip != shared_clip:
            return True

        return False

    @Property(str, notify=statusChanged)
    def status(self):
        return self._snapshot["status"]

    @Property(str, notify=masterGuidChanged)
    def masterGuid(self):
        return self._snapshot["master_guid"] or ""

    @Property(str, notify=masterGuidChanged)
    def masterAppName(self):
        return self._snapshot["master_app"]

    @Property(bool, notify=isHostChanged)
    def isHost(self):
        return self._snapshot["is_host"]

    @Property(bool, notify=splitViewChanged)
    def isSplitView(self):
        return self._is_split

    @Property(str, notify=roleChanged)
    def selfRole(self):
        """This peer's session role — what it may emit, not what it is driving."""
        return self._snapshot["self_role"]

    @Property(str, notify=roleChanged)
    def defaultRole(self):
        """The role this session gives a participant it does not recognise."""
        return self._snapshot["default_role"]

    @Property(bool, notify=mayAdministerRolesChanged)
    def mayAdministerRoles(self):
        """Whether this peer may grant another participant a role.

        Read by the panel to decide whether to *offer* the per-row role
        control; the core makes the actual decision independently when
        :meth:`setPeerRole` is invoked (session-role-administration).
        """
        return self._snapshot["may_administer_roles"]

    @Slot(str, str)
    def setPeerRole(self, user, role):
        """Grant *user* *role* (session-role-administration).

        A thin dispatch to :meth:`SyncManager.set_peer_role`; the permission
        check, the local application, and the broadcast all happen there —
        this model does not decide whether the grant is permitted, only
        whether to offer the control (see :attr:`mayAdministerRoles`).
        """
        if self._manager is None:
            return
        try:
            self._manager.set_peer_role(user, role)
        except Exception as exc:
            _logger.debug("setPeerRole(%r, %r) failed: %s", user, role, exc)

    @Property(bool, notify=driverlessChanged)
    def isDriverless(self):
        """Whether no peer is eligible to be host, so nobody may change the view.

        Reported, not merely inferable from a greyed-out menu item: a session
        whose view is frozen has to say why, and the panel is where it says it.
        """
        return self._snapshot["driverless"]

    @Property(bool, notify=selfHoldsVisibilityChanged)
    def selfHoldsVisibility(self):
        """Whether this peer currently holds visibility authority (effective holder)."""
        return self._snapshot["self_holds_visibility"]

    @Property(bool, notify=mayHoldVisibilityChanged)
    def mayHoldVisibility(self):
        """Whether this peer's role permits it to claim visibility authority."""
        return self._snapshot["may_hold_visibility"]

    @Property(str, notify=joinConfirmationChanged)
    def joinConfirmationOutcome(self):
        """``"confirmed"`` / ``"mismatched"`` / ``"not_confirmed"``, or ``""``.

        Empty string — never one of the three outcomes — before this peer has
        ever joined a session, so the panel can tell "not checked" apart from
        any recorded outcome (session-state-ui).
        """
        jc = self._snapshot["join_confirmation"]
        return jc["outcome"] if jc else ""

    @Property(list, notify=joinConfirmationChanged)
    def joinConfirmationDifferences(self):
        """Itemised differences for a mismatch; empty for any other outcome."""
        jc = self._snapshot["join_confirmation"]
        return jc["differences"] if jc else []

    @Property(str, notify=structureDivergenceChanged)
    def structureDivergence(self):
        """``"recovering"`` / ``"unrecoverable"``, or ``""`` when synchronised.

        Empty string for a peer that has never diverged, distinguishable from
        both conditions the same way ``joinConfirmationOutcome`` uses an empty
        string for "never joined" (structure-divergence-recovery, design D7).
        """
        return self._snapshot["structure_divergence"] or ""

    @Property(bool, notify=isDebugChanged)
    def isDebug(self):
        return self._is_debug

    @isDebug.setter
    def isDebug(self, val):
        if self._is_debug != val:
            self._is_debug = val
            self.isDebugChanged.emit()


class PeerListModel(QAbstractListModel):
    """Exposes the list of connected peers to QML."""

    GuidRole = Qt.UserRole + 1
    AppNameRole = Qt.UserRole + 2
    RoleRole = Qt.UserRole + 3
    IsSelfRole = Qt.UserRole + 4
    IsHostRole = Qt.UserRole + 5
    HoldsPositionLeaseRole = Qt.UserRole + 6
    HoldsDisplayLeaseRole = Qt.UserRole + 7
    HoldsStructureLeaseRole = Qt.UserRole + 8
    IsMasterRole = Qt.UserRole + 9
    DisplayNameRole = Qt.UserRole + 10
    UserRole = Qt.UserRole + 11
    HostRole = Qt.UserRole + 12
    SourceRole = Qt.UserRole + 13
    HoldsVisibilityRole = Qt.UserRole + 14

    #: QML role name → snapshot peer key.
    _FIELDS = {
        GuidRole: "guid",
        AppNameRole: "app",
        RoleRole: "role",
        IsSelfRole: "is_self",
        IsHostRole: "is_host",
        IsMasterRole: "is_master",
        HoldsPositionLeaseRole: "holds_position_lease",
        HoldsDisplayLeaseRole: "holds_display_lease",
        HoldsStructureLeaseRole: "holds_structure_lease",
        HoldsVisibilityRole: "holds_visibility",
        DisplayNameRole: "display_name",
        UserRole: "user",
        HostRole: "host",
        SourceRole: "source",
    }

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._peers = session_state_snapshot(manager)["peers"]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_peers)
        self._timer.start(POLL_INTERVAL_MS)

    def roleNames(self):
        return {
            self.GuidRole: b"guid",
            self.AppNameRole: b"appName",
            self.RoleRole: b"role",
            self.IsSelfRole: b"isSelf",
            self.IsHostRole: b"isHost",
            self.HoldsPositionLeaseRole: b"holdsPositionLease",
            self.HoldsDisplayLeaseRole: b"holdsDisplayLease",
            self.HoldsStructureLeaseRole: b"holdsStructureLease",
            self.HoldsVisibilityRole: b"holdsVisibility",
            self.IsMasterRole: b"isMaster",
            self.DisplayNameRole: b"displayName",
            self.UserRole: b"user",
            self.HostRole: b"host",
            self.SourceRole: b"source",
        }

    def _poll_peers(self):
        peers = session_state_snapshot(self._manager)["peers"]
        if peers == self._peers:
            return

        # The snapshot orders peers deterministically, so a membership change is
        # exactly a change in the guid sequence; anything else is a value-only
        # update that rows can refresh in place.
        if [p["guid"] for p in peers] != [p["guid"] for p in self._peers]:
            self.beginResetModel()
            self._peers = peers
            self.endResetModel()
            return

        self._peers = peers
        self.dataChanged.emit(self.index(0, 0), self.index(len(peers) - 1, 0))

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._peers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._peers)):
            return None
        field = self._FIELDS.get(role)
        if field is None:
            return None
        return self._peers[index.row()][field]
