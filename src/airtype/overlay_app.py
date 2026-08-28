"""Recording waveform overlay: a small layer-shell pill above the bottom edge.

Runs under the SYSTEM python3 (PyGObject + gtk4-layer-shell are system
packages that cannot live in the airtype venv), so this file must not import
anything from airtype. The service spawns it while recording and streams one
mic RMS value per line on stdin; stdin EOF fades the pill out and exits.

Colors come from the active Omarchy theme (accent + background in
~/.local/state/omarchy/current/theme/colors.toml) with neutral fallbacks, so
the overlay always matches the current theme without any configuration.
"""

import math
import os
import sys
from ctypes import CDLL
from pathlib import Path

# gtk4-layer-shell must be loaded before libwayland-client (which GTK links),
# or layer-shell setup silently fails. Documented workaround for Python.
for _soname in ("libgtk4-layer-shell.so", "libgtk4-layer-shell.so.0"):
    try:
        CDLL(_soname)
        break
    except OSError:
        continue

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
import cairo
from gi.repository import Gdk, Gio, GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

OMARCHY_COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
FALLBACK_ACCENT = "#7dcfff"
FALLBACK_BACKGROUND = "#16161e"

BAR_COUNT = 32
BAR_WIDTH = 4
BAR_GAP = 3
PAD_X = 16
HEIGHT = 42
BAR_MAX = 26
BAR_MIN = 2.0
MARGIN_BOTTOM = 30
WIDTH = PAD_X * 2 + BAR_COUNT * (BAR_WIDTH + BAR_GAP) - BAR_GAP

TICK_MS = 33
FADE_MS = 160
# Auto-ranging: absolute mic RMS varies wildly per mic and gain, so bars are
# scaled between a tracked noise floor and a slowly decaying peak.
MIN_SPAN = 0.003
PEAK_DECAY = 0.994


def _theme_colors() -> tuple[str, str]:
    try:
        import tomllib

        data = tomllib.loads(OMARCHY_COLORS.read_text(encoding="utf-8"))
        accent = str(data.get("accent") or FALLBACK_ACCENT)
        background = str(data.get("dark_background") or data.get("background") or FALLBACK_BACKGROUND)
        return accent, background
    except Exception:
        return FALLBACK_ACCENT, FALLBACK_BACKGROUND


def _rgb(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        value = FALLBACK_ACCENT.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _focused_monitor(display: "Gdk.Display") -> "Gdk.Monitor | None":
    """The Hyprland-focused monitor, so the pill shows where the user is typing."""
    try:
        import json
        import subprocess

        out = subprocess.run(
            ["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=1
        )
        focused = next(m["name"] for m in json.loads(out.stdout) if m.get("focused"))
    except Exception:
        return None
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        if monitor.get_connector() == focused:
            return monitor
    return None


def _rounded_rect(cr: "cairo.Context", x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class WaveformApp(Gtk.Application):
    def __init__(self) -> None:
        # NON_UNIQUE: each recording spawns its own process; D-Bus single-instance
        # would route a new launch into a previous, already-fading overlay.
        super().__init__(flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.accent = _rgb(_theme_colors()[0])
        self.background = _rgb(_theme_colors()[1])
        self.bars = [0.0] * BAR_COUNT
        self.pending = 0.0
        self.smoothed = 0.0
        self.floor = 1.0
        self.peak = 0.0
        self.opacity = 0.0
        self.fading_out = False
        self._stdin_buffer = b""

    def do_activate(self) -> None:
        win = Gtk.ApplicationWindow(application=self)
        win.set_default_size(WIDTH, HEIGHT)

        LayerShell.init_for_window(win)
        LayerShell.set_namespace(win, "airtype-overlay")
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, MARGIN_BOTTOM)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        monitor = _focused_monitor(win.get_display())
        if monitor is not None:
            LayerShell.set_monitor(win, monitor)

        css = Gtk.CssProvider()
        css.load_from_string("window { background-color: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        area = Gtk.DrawingArea()
        area.set_content_width(WIDTH)
        area.set_content_height(HEIGHT)
        area.set_draw_func(self._draw)
        win.set_child(area)
        self.area = area

        # Click-through: never steal pointer input from the app being dictated into.
        win.connect("map", self._make_click_through)
        win.present()

        GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP, self._on_stdin
        )
        GLib.timeout_add(TICK_MS, self._tick)
        self.hold()

    def _make_click_through(self, win: Gtk.ApplicationWindow) -> None:
        surface = win.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def _on_stdin(self, fd: int, condition: GLib.IOCondition) -> bool:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            self.fading_out = True
            return False
        self._stdin_buffer += chunk
        *lines, self._stdin_buffer = self._stdin_buffer.split(b"\n")
        for line in lines:
            try:
                self.pending = max(self.pending, float(line))
            except ValueError:
                continue
        return True

    def _tick(self) -> bool:
        if self.fading_out:
            self.opacity -= TICK_MS / FADE_MS
            if self.opacity <= 0:
                self.quit()
                return False
        else:
            self.opacity = min(1.0, self.opacity + TICK_MS / FADE_MS)
            target = self._normalize(self.pending)
            self.pending = 0.0
            # Fast attack, gentle release keeps the motion lively but unfussy.
            self.smoothed = max(target, self.smoothed * 0.82)
            self.bars = self.bars[1:] + [self.smoothed]
        self.area.queue_draw()
        return True

    def _normalize(self, rms: float) -> float:
        self.floor = min(self.floor, rms)
        self.peak = max(rms, self.floor + (self.peak - self.floor) * PEAK_DECAY)
        n = (rms - self.floor) / max(self.peak - self.floor, MIN_SPAN)
        n = min(1.0, max(0.0, n))
        return n**0.55

    def _draw(self, area: Gtk.DrawingArea, cr: "cairo.Context", w: int, h: int) -> None:
        cr.push_group()

        _rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, h / 2)
        cr.set_source_rgba(*self.background, 0.88)
        cr.fill_preserve()
        cr.set_source_rgba(*self.accent, 0.28)
        cr.set_line_width(1.0)
        cr.stroke()

        mid = h / 2
        for i, level in enumerate(self.bars):
            x = PAD_X + i * (BAR_WIDTH + BAR_GAP)
            bar_h = BAR_MIN + level * (BAR_MAX - BAR_MIN)
            # Older bars fade toward the left of the pill.
            alpha = 0.30 + 0.70 * (i / (BAR_COUNT - 1))
            cr.set_source_rgba(*self.accent, alpha)
            _rounded_rect(cr, x, mid - bar_h / 2, BAR_WIDTH, bar_h, BAR_WIDTH / 2)
            cr.fill()

        cr.pop_group_to_source()
        cr.paint_with_alpha(self.opacity)


def main() -> int:
    return WaveformApp().run(None)


if __name__ == "__main__":
    raise SystemExit(main())
