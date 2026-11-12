import unittest
from unittest.mock import Mock, patch

from evdev import ecodes
from airtype import clipboard


class WowPasteTests(unittest.TestCase):
    def test_wow_paste_uses_keyboard_and_releases_chord(self):
        keyboard = Mock()
        with patch.object(clipboard, '_paste_keyboard', None), \
             patch('evdev.UInput', return_value=keyboard) as create, \
             patch.object(clipboard, 'active_window_class', return_value='steam_app_battlenet'), \
             patch.object(clipboard.platform, 'system', return_value='Linux'), \
             patch.dict(clipboard.os.environ, {'XDG_SESSION_TYPE': 'wayland'}), \
             patch.object(clipboard.time, 'sleep'), \
             patch.object(clipboard, '_paste_with_wtype') as wtype:
            for _ in range(2):
                self.assertEqual(clipboard.auto_paste('ctrl_v'), (True, 'uinput'))
            create.assert_called_once()
            wtype.assert_not_called()
        self.assertEqual([c.args for c in keyboard.write.call_args_list], [
            (ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
            (ecodes.EV_KEY, ecodes.KEY_V, 1),
            (ecodes.EV_KEY, ecodes.KEY_V, 0),
            (ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0),
        ] * 2)

    def test_unavailable_keyboard_does_not_retry_wayland_paste(self):
        with patch.object(clipboard, '_paste_keyboard', None), \
             patch('evdev.UInput', side_effect=PermissionError), \
             patch.object(clipboard, 'active_window_class', return_value='wow.exe'), \
             patch.object(clipboard.platform, 'system', return_value='Linux'), \
             patch.dict(clipboard.os.environ, {'XDG_SESSION_TYPE': 'wayland'}), \
             patch.object(clipboard, '_paste_with_wtype') as wtype:
            self.assertEqual(clipboard.auto_paste('ctrl_v'), (False, 'copy_only'))
            wtype.assert_not_called()

    def test_focus_change_after_discovery_sends_no_keys(self):
        keyboard = Mock()
        with patch.object(clipboard, '_paste_keyboard', None), \
             patch('evdev.UInput', return_value=keyboard), \
             patch.object(clipboard, 'active_window_class', return_value='chromium'), \
             patch.object(clipboard.time, 'sleep'):
            self.assertFalse(clipboard._paste_with_uinput('ctrl_v'))
            keyboard.write.assert_not_called()
