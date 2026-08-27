import unittest
from unittest import mock

from airtype import terminal
from airtype.terminal import is_terminal_class, resolve_paste_mode

CLASSES = ["foot", "alacritty", "com.mitchellh.ghostty"]


class TerminalDetectionTests(unittest.TestCase):
    def test_is_terminal_class_matches_case_insensitively(self) -> None:
        self.assertTrue(is_terminal_class("Alacritty", CLASSES))
        self.assertTrue(is_terminal_class("foot", CLASSES))
        self.assertFalse(is_terminal_class("chromium", CLASSES))
        self.assertFalse(is_terminal_class(None, CLASSES))

    def test_auto_resolves_terminal_to_ctrl_shift_v(self) -> None:
        with mock.patch.object(terminal, "active_window_class", return_value="foot"):
            self.assertEqual(resolve_paste_mode("auto", "ctrl_v", CLASSES), "ctrl_shift_v")

    def test_auto_resolves_other_windows_to_ctrl_v(self) -> None:
        with mock.patch.object(terminal, "active_window_class", return_value="chromium"):
            self.assertEqual(resolve_paste_mode("auto", "ctrl_v", CLASSES), "ctrl_v")

    def test_auto_without_hyprctl_uses_fallback(self) -> None:
        with mock.patch.object(terminal, "active_window_class", return_value=None):
            self.assertEqual(resolve_paste_mode("auto", "copy_only", CLASSES), "copy_only")

    def test_non_auto_modes_pass_through(self) -> None:
        with mock.patch.object(terminal, "active_window_class") as active:
            self.assertEqual(resolve_paste_mode("ctrl_v", "copy_only", CLASSES), "ctrl_v")
            active.assert_not_called()

    def test_active_window_class_handles_missing_hyprctl(self) -> None:
        with mock.patch.object(terminal.shutil, "which", return_value=None):
            self.assertIsNone(terminal.active_window_class())


if __name__ == "__main__":
    unittest.main()
