// SPDX-License-Identifier: Apache-2.0
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtQuick.Dialogs

import xStudio 1.0

XsWindow {

    id: dialog
    title: "Import Annotations (OTIO)"
    width: 520
    minimumWidth: 460
    minimumHeight: 180

    modality: Qt.WindowModal
    flags: Qt.Dialog | Qt.WindowStaysOnTopHint

    property var rowHeight: 24

    FileDialog {
        id: fileDialog
        title: "Select OTIO File to Import"
        nameFilters: ["OTIO files (*.otio)", "All files (*)"]
        onAccepted: {
            var path = selectedFile.toString()
            if (path.startsWith("file://")) path = path.slice(7)
            otioFile.text = path
        }
    }

    GridLayout {

        anchors.fill: parent
        anchors.margins: 20
        columns: 2
        columnSpacing: 12
        rowSpacing: 10

        // ── OTIO file ──────────────────────────────────────────────
        XsText {
            text: "OTIO File"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            XsTextField {
                id: otioFile
                Layout.fillWidth: true
                Layout.preferredHeight: rowHeight
                placeholderText: "Browse for .otio file..."
                text: ""
            }

            XsPrimaryButton {
                Layout.preferredHeight: rowHeight
                text: "Browse..."
                onClicked: fileDialog.open()
            }
        }

        // ── Spacer ──────────────────────────────────────────────────────
        Item { Layout.fillHeight: true; Layout.columnSpan: 2 }

        // ── Buttons ─────────────────────────────────────────────────────
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
                text: "Import"
                width: XsStyleSheet.primaryButtonStdWidth * 2
                enabled: otioFile.text !== ""
                onClicked: {
                    var result = python_callback(
                        "do_import",
                        otioFile.text
                    )
                    if (Array.isArray(result)) {
                        if (result[0] === true) {
                            dialogHelpers.messageDialogFunc("Import Complete", result[1], "Ok")
                            dialog.hide()
                        } else {
                            dialogHelpers.errorDialogFunc("Import Failed", result[1])
                        }
                    } else {
                        dialogHelpers.errorDialogFunc("Import Error", String(result))
                    }
                }
            }
        }
    }
}
