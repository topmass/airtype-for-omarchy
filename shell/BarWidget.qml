import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// AirType dictation indicator for the Omarchy bar.
//
// Subscribes to the airtype service's unix control socket for live state
// (the service pushes the current state on subscribe, then every transition).
// Click toggles recording. The socket vanishing means the service stopped;
// the widget dims and keeps retrying until it comes back.
BarWidget {
  id: root
  moduleName: "topmass.airtype"

  // offline | ready | unloaded | recording | transcribing
  property string serviceState: "offline"

  readonly property bool online: serviceState !== "offline"
  readonly property bool recording: serviceState === "recording"
  readonly property bool transcribing: serviceState === "transcribing"
  readonly property bool busy: recording || transcribing
  readonly property bool showWhenIdle: setting("showWhenIdle", true) === true

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Soft breathing pulse while recording. Lives on the widget root, not the
  // button: an animation writing button.opacity would break the button's own
  // dim/conceal opacity binding.
  SequentialAnimation on opacity {
    running: root.recording
    loops: Animation.Infinite
    alwaysRunToEnd: true
    NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
    NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
  }

  Socket {
    id: ipc
    path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/airtype/control.sock"
    connected: true

    onConnectedChanged: {
      if (connected) write('{"cmd":"subscribe"}\n')
      else root.serviceState = "offline"
    }

    parser: SplitParser {
      onRead: function(line) {
        var data
        try { data = JSON.parse(line) } catch (e) { return }
        // State rides on "state" events and on "transcript" events (which
        // carry the state the service settled into after transcribing).
        if (data.event && data.state) root.serviceState = String(data.state)
      }
    }
  }

  Timer {
    interval: 3000
    repeat: true
    running: !ipc.connected
    onTriggered: ipc.connected = true
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: !root.online ? "󰍭" : root.transcribing ? "󰔟" : "󰍬"
    hasVisualContent: root.busy || root.showWhenIdle
    dimmed: !root.busy
    active: root.recording
    interactive: root.online
    tooltipText: root.recording ? "Recording — click to stop"
      : root.transcribing ? "Transcribing…"
      : root.online ? "AirType — click to dictate"
      : "AirType service not running"

    onPressed: function(b) {
      if (root.online && root.bar) root.bar.run("airtype toggle")
    }
  }
}
