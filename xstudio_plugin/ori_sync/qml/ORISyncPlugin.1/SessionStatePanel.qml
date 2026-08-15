// SPDX-License-Identifier: Apache-2.0
//
// Session State panel — xStudio-native view of the ORI Sync session.
//
// The layout is xStudio's; the *state* comes from the shared projection in
// otio_sync_core/session_state.py, published by the plugin as the "Session
// State" attribute (JSON).  OpenRV renders the same projection through its own
// PySide6 panel.  The two views are written separately on purpose — see the
// session-state-ui design doc — but neither derives session semantics locally,
// so they cannot disagree about who is in the session or who holds a lease.
//
// Binding to an attribute rather than polling python_callback is deliberate:
// python_callback blocks xStudio's Qt main thread, and a panel that polled it
// twice a second would block the UI twice a second for as long as it was open.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

import xStudio 1.0
import xstudio.qml.models 1.0
import xstudio.qml.helpers 1.0

XsWindow {

    id: panel
    title: "Session State"
    width: 420
    height: 480
    minimumWidth: 360
    minimumHeight: 280

    property bool debugMode: false

    // ── state feed ─────────────────────────────────────────────────────
    XsModuleData {
        id: sessionStateAttrs
        modelDataName: "ori_sync_state"
    }

    XsAttributeValue {
        id: __sessionState
        attributeTitle: "Session State"
        model: sessionStateAttrs
    }

    // Declarative so QML tracks the dependency itself — the panel re-renders
    // whenever the plugin pushes a new projection, with no timer anywhere.
    property var state: {
        var raw = __sessionState.value
        if (!raw) return ({})
        try {
            return JSON.parse(raw)
        } catch (err) {
            // A malformed payload must not take the panel down with it.
            console.log("SessionStatePanel: bad payload:", err)
            return ({})
        }
    }
    property var peers: state.peers ? state.peers : []

    function statusColour(s) {
        if (s === "SYNCED") return "#22c55e"
        if (s === "JOINING") return "#f59e0b"
        if (s === "DISCOVERING") return "#3b82f6"
        return XsStyleSheet.hintColor
    }

    function shortGuid(g) {
        return g ? String(g).substring(0, 8) : "-"
    }

    // A mismatch is a fact about THIS peer's own state, not a session or peer
    // error — the wording below is deliberate about that (session-state-ui).
    function joinConfirmationText() {
        var jc = panel.state.join_confirmation
        if (!jc) return ""
        if (jc.outcome === "confirmed") return "Join confirmed — this view matches what was sent."
        if (jc.outcome === "mismatched") {
            var diffs = jc.differences ? jc.differences : []
            return "This view does not match what was sent: " + diffs.join("; ")
        }
        return "Join not confirmed — could not verify this view against what was sent."
    }

    function joinConfirmationColour() {
        var jc = panel.state.join_confirmation
        if (!jc) return XsStyleSheet.secondaryTextColor
        if (jc.outcome === "confirmed") return "#22c55e"
        if (jc.outcome === "mismatched") return "#ef4444"
        return "#f59e0b"
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: XsStyleSheet.panelPadding * 2
        spacing: XsStyleSheet.panelPadding * 2

        // ── header ─────────────────────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: headerCol.implicitHeight + XsStyleSheet.panelPadding * 4
            color: XsStyleSheet.panelTitleBarColor
            radius: 2

            ColumnLayout {
                id: headerCol
                anchors.fill: parent
                anchors.margins: XsStyleSheet.panelPadding * 2
                spacing: XsStyleSheet.panelPadding

                RowLayout {
                    spacing: 6
                    XsText {
                        text: "Status:"
                        horizontalAlignment: Text.AlignLeft
                        color: XsStyleSheet.secondaryTextColor
                    }
                    XsText {
                        text: panel.state.status ? panel.state.status : "DISCONNECTED"
                        horizontalAlignment: Text.AlignLeft
                        color: panel.statusColour(panel.state.status)
                    }
                    Item { Layout.fillWidth: true }
                }

                XsText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignLeft
                    color: XsStyleSheet.secondaryTextColor
                    text: {
                        if (!panel.state.master_guid) return "Master: none elected"
                        var app = panel.state.master_app ? panel.state.master_app : "unknown"
                        return "Master: " + app + " (" + panel.shortGuid(panel.state.master_guid) + ")"
                    }
                }

                // Session role of this peer: a third axis, not a restatement of
                // master or host. It says what this participant may ever emit.
                XsText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignLeft
                    color: XsStyleSheet.secondaryTextColor
                    visible: panel.state.self_role !== undefined
                    text: "Your role: " + panel.state.self_role
                          + " (session default: " + panel.state.default_role + ")"
                }

                XsText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignLeft
                    color: panel.state.self_holds_visibility ? XsStyleSheet.accentColor : XsStyleSheet.secondaryTextColor
                    visible: panel.state.self_holds_visibility !== undefined
                    text: {
                        if (panel.state.self_holds_visibility) return "I am driving the view"
                        if (panel.state.may_hold_visibility) return "I could drive it"
                        return "changing the view is not available to me"
                    }
                }

                // The driverless condition is reported here rather than left to
                // be inferred from a menu item that is simply absent: a session
                // whose view nobody may change has to explain itself.
                XsText {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignLeft
                    color: "#f59e0b"  // same warning amber as JOINING in statusColour
                    font.bold: true
                    visible: panel.state.driverless === true
                    text: "No eligible driver — nobody can change what the session is "
                          + "looking at. Session ▸ Become Controller to take it on."
                }

                XsText {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignLeft
                    color: XsStyleSheet.secondaryTextColor
                    visible: panel.debugMode
                    text: "This peer: " + panel.shortGuid(panel.state.self_guid)
                          + (panel.state.is_host ? " — holds visibility authority" : "")
                }

                // Post-join state confirmation: whether this peer's joined
                // state was confirmed against the snapshot it was sent.
                // Absent entirely before this peer has ever joined a session
                // (see panel.joinConfirmationText) — "not checked" and
                // "checked and matching" must read as different facts, not
                // collapse into one.
                XsText {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignLeft
                    visible: panel.state.join_confirmation !== undefined
                             && panel.state.join_confirmation !== null
                    color: panel.joinConfirmationColour()
                    text: panel.joinConfirmationText()
                }
            }
        }

        // ── collaborators ──────────────────────────────────────────────
        XsText {
            text: "Collaborators (" + panel.peers.length + ")"
            horizontalAlignment: Text.AlignLeft
            color: XsStyleSheet.secondaryTextColor
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: XsStyleSheet.panelBgColor
            border.width: XsStyleSheet.dividerSize
            border.color: XsStyleSheet.menuBorderColor

            ListView {
                id: peerList
                anchors.fill: parent
                anchors.margins: XsStyleSheet.dividerSize
                clip: true
                model: panel.peers
                ScrollBar.vertical: ScrollBar {}

                delegate: Rectangle {
                    width: peerList.width
                    height: rowCol.implicitHeight + XsStyleSheet.panelPadding * 2
                    color: index % 2 === 0 ? "transparent" : XsStyleSheet.baseColor

                    ColumnLayout {
                        id: rowCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: XsStyleSheet.panelPadding * 2
                        spacing: 2

                        RowLayout {
                            spacing: 6

                            // Host marker: the peer holding visibility authority.
                            Rectangle {
                                width: 6
                                height: 6
                                radius: 3
                                color: modelData.is_host
                                       ? XsStyleSheet.accentColor
                                       : "transparent"
                                border.width: 1
                                border.color: modelData.is_host
                                              ? XsStyleSheet.accentColor
                                              : XsStyleSheet.hintColor
                            }

                            XsText {
                                text: modelData.display_name + (modelData.is_self ? " (You)" : "")
                                horizontalAlignment: Text.AlignLeft
                                color: XsStyleSheet.primaryTextColor
                            }

                            XsText {
                                text: (modelData.is_master ? "Master / " : "") + modelData.role + " (" + modelData.app + ")"
                                horizontalAlignment: Text.AlignLeft
                                color: XsStyleSheet.secondaryTextColor
                            }

                            Item { Layout.fillWidth: true }
                        }

                        XsText {
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            horizontalAlignment: Text.AlignLeft
                            visible: panel.debugMode
                            color: XsStyleSheet.hintColor
                            text: "Identity: " + (modelData.user ? modelData.user : "-") + "@" + (modelData.host ? modelData.host : "-")
                                  + (modelData.source ? " (" + modelData.source + ")" : "")
                        }

                        XsText {
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            horizontalAlignment: Text.AlignLeft
                            visible: panel.debugMode
                            color: XsStyleSheet.hintColor
                            text: {
                                var leases = []
                                if (modelData.holds_position_lease) leases.push("Position")
                                if (modelData.holds_display_lease) leases.push("Display")
                                if (modelData.holds_structure_lease) leases.push("Structure")
                                if (modelData.holds_visibility) leases.push("Visibility")
                                return panel.shortGuid(modelData.guid) + "  —  "
                                       + (leases.length ? "leases: " + leases.join(", ")
                                                        : "no leases held")
                            }
                        }
                    }
                }
            }
        }

        // ── footer ─────────────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            XsCheckBox {
                text: "Debug Mode"
                checked: panel.debugMode
                // onToggled, not onCheckedChanged: the `checked` binding above
                // would otherwise write back to the property driving it.
                onToggled: panel.debugMode = checked
            }

            Item { Layout.fillWidth: true }

            XsSimpleButton {
                text: "Close"
                width: XsStyleSheet.primaryButtonStdWidth * 2
                onClicked: panel.hide()
            }
        }
    }
}
