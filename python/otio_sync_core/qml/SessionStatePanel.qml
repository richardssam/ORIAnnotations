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
                            text: appName + (isSelf ? " (You)" : "")
                            color: uiStyle.textColor
                            font.family: uiStyle.fontFamily
                            font.pixelSize: uiStyle.fontSize
                            font.bold: isSelf
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Text {
                        text: (isMaster ? "Master / " : "") + role
                        color: uiStyle.secondaryTextColor
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 2
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
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
                        text: "Leases: " + 
                              (holdsPositionLease ? "[Position] " : "") + 
                              (holdsDisplayLease ? "[Display] " : "") + 
                              (holdsStructureLease ? "[Structure]" : "")
                        color: uiStyle.textColor
                        opacity: 0.7
                        font.family: uiStyle.fontFamily
                        font.pixelSize: uiStyle.fontSize - 4
                        visible: holdsPositionLease || holdsDisplayLease || holdsStructureLease
                    }
                }
            }
        }
    }
}
