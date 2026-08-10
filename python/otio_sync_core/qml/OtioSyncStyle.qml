// SPDX-License-Identifier: Apache-2.0
//
// Palette for the OpenRV Session State panel.
//
// OpenRV-only by design.  An xStudio-native panel styles itself from
// XsStyleSheet directly rather than reading this file: the two hosts share the
// Python snapshot (otio_sync_core/session_state.py), not their QML.  The status
// colours below match sync_viewer so the same state reads the same everywhere.
import QtQuick 2.15

QtObject {
    // Backgrounds
    property color panelBgColor: "#242428"
    property color headerBgColor: "#3a3a42"
    property color alternateBgColor: "#1a1a1e"

    // Text
    property color textColor: "#d4d4d8"
    property color secondaryTextColor: "#71717a"
    property color accentColor: "#6366f1"

    // Status
    property color syncedColor: "#22c55e"
    property color joiningColor: "#f59e0b"
    property color discoveringColor: "#3b82f6"
    property color warningColor: "#ef4444"

    // Borders / dividers
    property color borderColor: "#3a3a42"

    // Fonts
    property string fontFamily: "sans-serif"
    property real fontSize: 12

    // Dimensions
    property real rowHeight: 28
    property real padding: 8
}
