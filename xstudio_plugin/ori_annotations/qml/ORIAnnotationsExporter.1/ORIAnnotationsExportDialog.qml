// SPDX-License-Identifier: Apache-2.0
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtQuick.Dialogs

import xStudio 1.0

XsWindow {

    id: dialog
    title: "Export Annotations (OTIO)"
    width: 520
    minimumWidth: 460
    minimumHeight: 240

    modality: Qt.WindowModal
    flags: Qt.Dialog | Qt.WindowStaysOnTopHint

    property var rowHeight: 24

    FolderDialog {
        id: folderDialog
        title: "Select Export Folder"
        onAccepted: {
            // selectedFolder is a file:// URL — strip scheme for display
            var path = selectedFolder.toString()
            if (path.startsWith("file://")) path = path.slice(7)
            outputFolder.text = path
        }
    }

    GridLayout {

        anchors.fill: parent
        anchors.margins: 20
        columns: 2
        columnSpacing: 12
        rowSpacing: 10

        // ── Output folder ──────────────────────────────────────────────
        XsText {
            text: "Output Folder"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            XsTextField {
                id: outputFolder
                Layout.fillWidth: true
                Layout.preferredHeight: rowHeight
                placeholderText: "Browse for output folder..."
                text: ""
            }

            XsPrimaryButton {
                Layout.preferredHeight: rowHeight
                text: "Browse..."
                onClicked: folderDialog.open()
            }
        }

        // ── OTIO filename ───────────────────────────────────────────────
        XsText {
            text: "OTIO Filename"
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        }

        XsTextField {
            id: otioName
            Layout.fillWidth: true
            Layout.preferredHeight: rowHeight
            text: "annotations.otio"
        }

        // ── Checkboxes ──────────────────────────────────────────────────
        Item { Layout.preferredWidth: 1 }

        XsCheckBox {
            id: includeMedia
            text: "Copy media files into export directory"
            checked: false
        }

        Item { Layout.preferredWidth: 1 }

        XsCheckBox {
            id: includeImages
            text: "Render annotation images (PNG)"
            checked: false
        }

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
                text: "Export"
                width: XsStyleSheet.primaryButtonStdWidth * 2
                enabled: outputFolder.text !== ""
                onClicked: {
                    var result = python_callback(
                        "do_export",
                        outputFolder.text,
                        otioName.text,
                        includeMedia.checked,
                        includeImages.checked
                    )
                    if (Array.isArray(result)) {
                        if (result[0] === true) {
                            dialogHelpers.messageDialogFunc("Export Complete", result[1], "Ok")
                            dialog.hide()
                        } else {
                            dialogHelpers.errorDialogFunc("Export Failed", result[1])
                        }
                    } else {
                        dialogHelpers.errorDialogFunc("Export Error", String(result))
                    }
                }
            }
        }
    }
}
