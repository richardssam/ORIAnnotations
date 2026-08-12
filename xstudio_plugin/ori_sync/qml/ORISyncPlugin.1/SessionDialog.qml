// SPDX-License-Identifier: Apache-2.0
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

import xStudio 1.0

XsWindow {

    id: dialog
    title: "Connect to Session"
    width: 400
    minimumWidth: 360
    minimumHeight: 160

    modality: Qt.WindowModal
    flags: Qt.Dialog | Qt.WindowStaysOnTopHint

    property string mode: "join"
    property var rowHeight: 24

    GridLayout {

        anchors.fill: parent
        anchors.margins: 20
        columns: 2
        columnSpacing: 12
        rowSpacing: 10

        // ── MQ Host ────────────────────────────────────────────────────
        XsText {
            text: "MQ Host"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsTextField {
            id: hostField
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            placeholderText: "localhost"
            text: ""
            Component.onCompleted: {
                var env = Qt.environment ? Qt.environment["ORI_RMQ_HOST"] : ""
                text = env || "localhost"
            }
            onAccepted: nameField.forceActiveFocus()
        }

        // ── Session Name ───────────────────────────────────────────────
        XsText {
            text: "Session Name"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsTextField {
            id: nameField
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            placeholderText: "e.g. daily-review"
            text: ""
            onAccepted: {
                if (text.trim() !== "") connectButton.clicked()
            }
        }

        // ── You (Identity) ─────────────────────────────────────────────
        XsText {
            text: "You:"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsTextField {
            id: youField
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            text: ""
            property string defaultYou: ""
            Component.onCompleted: {
                var def = python_callback("get_default_identity", {}) || ""
                text = def
                defaultYou = def
            }
            onAccepted: {
                if (nameField.text.trim() !== "") connectButton.clicked()
            }
        }

        // ── Spacer ─────────────────────────────────────────────────────
        Item { Layout.fillHeight: true; Layout.columnSpan: 2 }

        // ── Buttons ────────────────────────────────────────────────────
        Item { Layout.fillWidth: true }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 6

            XsSimpleButton {
                text: "Cancel"
                width: XsStyleSheet.primaryButtonStdWidth * 2
                onClicked: {
                    // Hide (not destroy): the runtime keeps the single dialog
                    // instance alive and re-shows it by toggling attr_enabled.
                    dialog.hide()
                }
            }

            XsSimpleButton {
                id: connectButton
                text: "Connect"
                width: XsStyleSheet.primaryButtonStdWidth * 2
                enabled: nameField.text.trim() !== ""
                onClicked: {
                    var result = python_callback(
                        "do_session_connect",
                        {
                            "host": hostField.text.trim() || "localhost",
                            "name": nameField.text.trim(),
                            "you": youField.text.trim() !== youField.defaultYou ? youField.text.trim() : null,
                            "mode": dialog.mode
                        }
                    )
                    dialog.hide()
                }
            }
        }
    }
}
