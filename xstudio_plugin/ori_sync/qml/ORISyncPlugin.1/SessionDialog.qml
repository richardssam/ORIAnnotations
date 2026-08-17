// SPDX-License-Identifier: Apache-2.0
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

import xStudio 1.0

XsWindow {

    id: dialog
    title: "Connect to Session"
    width: 400
    height: 336
    minimumWidth: 360
    minimumHeight: 316

    modality: Qt.WindowModal
    flags: Qt.Dialog | Qt.WindowStaysOnTopHint

    property string mode: "join"
    property var rowHeight: 24

    // Enter/Return submits the form: each text field's onAccepted below
    // calls this when it has focus.
    function tryConnect() {
        if (nameField.text.trim() !== "") connectButton.clicked()
    }

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
                // site-config-file: a config file dropped into this plugin's
                // folder pre-defines the host beneath the env var and above
                // the hardcoded fallback.
                var site = env ? "" : (python_callback("get_site_default_host", {}) || "")
                text = env || site || "localhost"
            }
            onAccepted: dialog.tryConnect()
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
            onAccepted: dialog.tryConnect()
        }

        // ── Account (read-only) ──────────────────────────────────────────
        // session-connection-ux: the resolved account identity, distinct
        // from the editable display-name override below. Read-only — an
        // account-level override, where one exists, is set the way it is
        // today (ORI_SYNC_USER), not through this dialog.
        XsText {
            text: "Account:"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsTextField {
            id: accountField
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            text: ""
            readOnly: true
            enabled: false
            Component.onCompleted: {
                text = python_callback("get_account_identity", {}) || ""
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
            onAccepted: dialog.tryConnect()
        }

        // ── Default Role (create path only) ──────────────────────────────
        // session-role-config: the session's default role is declared once,
        // at creation, through the host application — never through the
        // join path, where the session already has a policy and sends it in
        // STATE_SNAPSHOT.
        XsText {
            visible: dialog.mode === "create"
            text: "Default Role"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsComboBox {
            id: defaultRoleCombo
            visible: dialog.mode === "create"
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            model: ["Driver (unrestricted)", "Reviewer", "Viewer"]
            property var wireValues: ["driver", "reviewer", "viewer"]
            currentIndex: 0
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
                    var payload = {
                        "host": hostField.text.trim() || "localhost",
                        "name": nameField.text.trim(),
                        // "" rather than null: do_session_connect() already
                        // treats an absent/empty "you" identically
                        // (`(data.get("you") or "").strip()`), and a null
                        // value in the payload dict is a plausible cause of a
                        // silent failure in the QML->Python argument
                        // marshalling — a null costs nothing to avoid here.
                        "you": youField.text.trim() !== youField.defaultYou ? youField.text.trim() : "",
                        "mode": dialog.mode
                    }
                    if (dialog.mode === "create") {
                        payload["default_role"] = defaultRoleCombo.wireValues[defaultRoleCombo.currentIndex]
                    }
                    var result = python_callback("do_session_connect", payload)
                    dialog.hide()
                }
            }
        }
    }
}
