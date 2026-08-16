import QtQuick 2.15

Rectangle {
    property bool isDebug: false
    color: uiStyle.panelBgColor
    width: 400
    height: 500

    OtioSyncStyle {
        id: uiStyle
    }

    // Top status area
    Rectangle {
        id: header
        width: parent.width
        height: 60
        color: uiStyle.headerBgColor
        
        Column {
            anchors.fill: parent
            anchors.margins: uiStyle.padding
            spacing: 4
            
            Item {
                width: parent.width
                height: statusRow.height
                
                Row {
                    spacing: 20
                    
                    Row {
                        id: statusRow
                        spacing: 4
                        Text {
                            text: "Status: "
                            color: uiStyle.textColor
                            opacity: 0.7
                            font.family: uiStyle.fontFamily
                            font.pixelSize: uiStyle.fontSize
                        }
                        Text {
                            text: sessionState.status + (sessionState.isSplitView ? " (SPLIT VIEW)" : "")
                            color: {
                                if (sessionState.isSplitView) return uiStyle.warningColor;
                                if (sessionState.status === "SYNCED") return uiStyle.syncedColor;
                                if (sessionState.status === "JOINING") return uiStyle.joiningColor;
                                if (sessionState.status === "DISCOVERING") return uiStyle.discoveringColor;
                                return uiStyle.secondaryTextColor;
                            }
                            font.family: uiStyle.fontFamily
                            font.pixelSize: uiStyle.fontSize
                            font.bold: true
                        }
                    }
                    
                    // Custom Checkbox
                    MouseArea {
                        width: checkRow.width
                        height: checkRow.height
                        cursorShape: Qt.PointingHandCursor
                        onClicked: isDebug = !isDebug
                        
                        Row {
                            id: checkRow
                            spacing: 4
                            
                            Rectangle {
                                width: 16
                                height: 16
                                border.color: isDebug ? uiStyle.syncedColor : uiStyle.secondaryTextColor
                                border.width: 2
                                color: isDebug ? uiStyle.syncedColor : "transparent"
                            }
                            Text {
                                text: "Debug Mode"
                                color: isDebug ? uiStyle.syncedColor : uiStyle.textColor
                                opacity: isDebug ? 1.0 : 0.7
                                font.family: uiStyle.fontFamily
                                font.pixelSize: uiStyle.fontSize
                                font.bold: isDebug
                            }
                        }
                    }
                }
            }
            
            Text {
                text: "Master: " + (sessionState.masterGuid ? (sessionState.masterAppName ? sessionState.masterAppName + " (" + sessionState.masterGuid.substring(0, 8) + ")" : sessionState.masterGuid.substring(0, 8)) : "None")
                color: uiStyle.textColor
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
            }

            // Session role of this peer. A third axis, not a restatement of
            // master or host: role is what this participant may ever emit.
            Text {
                text: "Your role: " + sessionState.selfRole
                      + " (session default: " + sessionState.defaultRole + ")"
                color: uiStyle.textColor
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
            }

            Text {
                text: {
                    if (sessionState.selfHoldsVisibility) return "I am driving the view"
                    if (sessionState.mayHoldVisibility) return "I could drive it"
                    return "changing the view is not available to me"
                }
                color: sessionState.selfHoldsVisibility ? uiStyle.syncedColor : uiStyle.textColor
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
            }

            // The driverless condition is *reported*, not merely inferable from
            // a disabled menu item: a session whose view nobody may change has
            // to explain itself, which is the whole reason this state is
            // surfaced at all.
            Text {
                visible: sessionState.isDriverless
                text: "No eligible driver — nobody can change what the session "
                      + "is looking at. OTIO Sync ▸ Become Controller to take it on."
                color: uiStyle.warningColor
                wrapMode: Text.WordWrap
                width: parent.width
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
                font.bold: true
            }

            // Structure divergence recovery: distinct from the driverless
            // banner above (that is about who may drive the view; this is
            // about whether THIS peer's structure matches the session), and
            // "being repaired" must read differently from "could not be
            // repaired" — the user's options differ (session-state-ui).
            Text {
                visible: sessionState.structureDivergence === "recovering"
                         || sessionState.structureDivergence === "unrecoverable"
                text: {
                    if (sessionState.structureDivergence === "recovering")
                        return "Resynchronising — a local change could not be shared, rebuilding from the session."
                    return "This peer's content may not match the session — no peer could be reached to resynchronise."
                }
                color: sessionState.structureDivergence === "unrecoverable" ? uiStyle.warningColor : uiStyle.joiningColor
                wrapMode: Text.WordWrap
                width: parent.width
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
                font.bold: true
            }

            // Post-join state confirmation: whether this peer's joined state
            // was confirmed against the snapshot it was sent.  Absent (empty
            // outcome string) before this peer has ever joined a session — a
            // mismatch is reported as a fact about THIS peer's own state, not
            // as a session or peer error (session-state-ui).
            Text {
                visible: sessionState.joinConfirmationOutcome !== ""
                text: {
                    if (sessionState.joinConfirmationOutcome === "confirmed")
                        return "Join confirmed — this view matches what was sent."
                    if (sessionState.joinConfirmationOutcome === "mismatched")
                        return "This view does not match what was sent: "
                               + sessionState.joinConfirmationDifferences.join("; ")
                    return "Join not confirmed — could not verify this view against what was sent."
                }
                color: {
                    if (sessionState.joinConfirmationOutcome === "confirmed") return uiStyle.syncedColor
                    if (sessionState.joinConfirmationOutcome === "mismatched") return uiStyle.warningColor
                    return uiStyle.joiningColor
                }
                wrapMode: Text.WordWrap
                width: parent.width
                font.family: uiStyle.fontFamily
                font.pixelSize: uiStyle.fontSize - 2
            }
        }
    }

    // Peer List
    ListView {
        id: peerList
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        model: peerModel
        clip: true

        delegate: Rectangle {
            width: peerList.width
            height: isDebug ? uiStyle.rowHeight * 3 : uiStyle.rowHeight
            color: index % 2 === 0 ? uiStyle.panelBgColor : uiStyle.alternateBgColor
            border.color: uiStyle.borderColor
            border.width: 1

            Column {
                anchors.fill: parent
                anchors.margins: uiStyle.padding
                spacing: 4

                Item {
                    width: parent.width
                    height: Math.max(peerIcon.height, peerName.height)
                    
                    Row {
                        spacing: 4
                        anchors.verticalCenter: parent.verticalCenter
                        Rectangle {
                            id: peerIcon
                            width: 8
                            height: 8
                            radius: 4
                            color: isHost ? uiStyle.syncedColor : uiStyle.secondaryTextColor
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            id: peerName
                            text: displayName + (isSelf ? " (You)" : "")
                            color: uiStyle.textColor
                            font.family: uiStyle.fontFamily
                            font.pixelSize: uiStyle.fontSize
                            font.bold: isSelf
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Text {
                        text: (isMaster ? "Master / " : "") + role + " (" + appName + ")"
                        color: uiStyle.secondaryTextColor
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 2
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                // Role administration (session-role-administration): offered
                // only while this peer may administer, and only for a peer
                // that carries an identity — a grant is addressed by
                // identity, so there is nothing to grant a peer with none.
                Row {
                    id: roleControl
                    visible: sessionState.mayAdministerRoles && user !== ""
                    spacing: 6
                    // Which role is awaiting a second click to confirm; ""
                    // when nothing is pending.
                    property string confirmingRole: ""

                    Repeater {
                        model: ["driver", "reviewer", "viewer"]
                        delegate: Rectangle {
                            property string roleValue: modelData
                            property bool isCurrent: role === roleValue
                            property bool isConfirming: roleControl.confirmingRole === roleValue
                            width: roleLabel.implicitWidth + 10
                            height: roleLabel.implicitHeight + 4
                            radius: 3
                            color: isCurrent ? uiStyle.syncedColor
                                   : (isConfirming ? uiStyle.warningColor : "transparent")
                            border.color: uiStyle.secondaryTextColor
                            border.width: 1

                            Text {
                                id: roleLabel
                                anchors.centerIn: parent
                                text: isConfirming ? "confirm?" : roleValue
                                color: isCurrent ? uiStyle.panelBgColor : uiStyle.textColor
                                font.family: uiStyle.fontFamily
                                font.pixelSize: uiStyle.fontSize - 4
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                enabled: !isCurrent
                                onClicked: {
                                    // A courtesy confirmation in this view
                                    // only, before demoting a peer away from
                                    // "driver" — the core applies or refuses
                                    // the grant on its own evaluation either
                                    // way (session-state-ui: the panel does
                                    // not decide whether an action is
                                    // permitted).  The panel has no cheap way
                                    // to know whether this is the *last*
                                    // driver, so it asks on every such
                                    // demotion rather than only that one.
                                    if (role === "driver" && roleValue !== "driver" && !isConfirming) {
                                        roleControl.confirmingRole = roleValue
                                    } else {
                                        roleControl.confirmingRole = ""
                                        sessionState.setPeerRole(user, roleValue)
                                    }
                                }
                            }
                        }
                    }
                }

                // Debug Information
                Column {
                    visible: isDebug
                    width: parent.width
                    spacing: 2
                    
                    Text {
                        text: "GUID: " + guid
                        color: uiStyle.textColor
                        opacity: 0.7
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 4
                    }
                    Text {
                        text: "Identity: " + (user ? user : "-") + "@" + (host ? host : "-") + (source ? " (" + source + ")" : "")
                        color: uiStyle.textColor
                        opacity: 0.7
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 4
                    }
                    Text {
                        text: "Leases: " + 
                              (holdsPositionLease ? "[Position] " : "") + 
                              (holdsDisplayLease ? "[Display] " : "") + 
                              (holdsStructureLease ? "[Structure] " : "") +
                              (holdsVisibility ? "[Visibility]" : "")
                        color: uiStyle.textColor
                        opacity: 0.7
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 4
                        visible: holdsPositionLease || holdsDisplayLease || holdsStructureLease || holdsVisibility
                    }
                }
            }
        }
    }
}
