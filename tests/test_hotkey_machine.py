import unittest

from airtype.hotkey import HotkeyPolicy, parse_start_combo


def make_policy() -> HotkeyPolicy:
    return HotkeyPolicy(
        start_modifiers={"super"},
        tap_key="alt",
        stop_key="alt",
        double_tap_threshold=0.3,
        stop_cooldown=0.25,
        modifier_release_tolerance=0.5,
    )


class ParseStartComboTests(unittest.TestCase):
    def test_super_alt_combo(self) -> None:
        modifiers, tap = parse_start_combo("super+alt")
        self.assertEqual(modifiers, {"super"})
        self.assertEqual(tap, "alt")

    def test_single_key_has_no_modifiers(self) -> None:
        modifiers, tap = parse_start_combo("alt")
        self.assertEqual(modifiers, set())
        self.assertEqual(tap, "alt")

    def test_default_when_empty(self) -> None:
        modifiers, tap = parse_start_combo(None)
        self.assertEqual(modifiers, {"super"})
        self.assertEqual(tap, "alt")


class HotkeyPolicyStartTests(unittest.TestCase):
    def test_double_tap_with_super_held_starts(self) -> None:
        policy = make_policy()
        self.assertIsNone(policy.feed("super", True, 10.0))
        self.assertIsNone(policy.feed("alt", True, 10.05))
        self.assertIsNone(policy.feed("alt", False, 10.1))
        self.assertEqual(policy.feed("alt", True, 10.2), "start")

    def test_double_tap_without_super_does_not_start(self) -> None:
        policy = make_policy()
        self.assertIsNone(policy.feed("alt", True, 10.0))
        self.assertIsNone(policy.feed("alt", False, 10.05))
        self.assertIsNone(policy.feed("alt", True, 10.1))

    def test_slow_taps_do_not_start(self) -> None:
        policy = make_policy()
        policy.feed("super", True, 10.0)
        self.assertIsNone(policy.feed("alt", True, 10.05))
        policy.feed("alt", False, 10.1)
        self.assertIsNone(policy.feed("alt", True, 10.5))

    def test_super_release_tolerance_allows_early_release(self) -> None:
        policy = make_policy()
        policy.feed("super", True, 10.0)
        policy.feed("alt", True, 10.05)
        policy.feed("alt", False, 10.1)
        policy.feed("super", False, 10.12)
        # Super released a hair before the second tap still counts.
        self.assertEqual(policy.feed("alt", True, 10.2), "start")

    def test_super_released_long_ago_does_not_start(self) -> None:
        policy = make_policy()
        policy.feed("super", True, 9.0)
        policy.feed("super", False, 9.1)
        self.assertIsNone(policy.feed("alt", True, 10.0))
        policy.feed("alt", False, 10.05)
        self.assertIsNone(policy.feed("alt", True, 10.1))

    def test_plain_tap_resets_double_tap_window(self) -> None:
        policy = make_policy()
        # A tap without super must not pair with a later super+tap.
        policy.feed("alt", True, 10.0)
        policy.feed("alt", False, 10.05)
        policy.feed("super", True, 10.1)
        self.assertIsNone(policy.feed("alt", True, 10.15))


class HotkeyPolicyStopTests(unittest.TestCase):
    def _recording_policy(self, start: float = 10.0) -> HotkeyPolicy:
        policy = make_policy()
        policy.set_recording(True, start)
        return policy

    def test_clean_tap_stops(self) -> None:
        policy = self._recording_policy(10.0)
        self.assertIsNone(policy.feed("alt", True, 11.0))
        self.assertEqual(policy.feed("alt", False, 11.1), "stop")

    def test_tap_within_cooldown_does_not_stop(self) -> None:
        policy = self._recording_policy(10.0)
        policy.feed("alt", True, 10.05)
        self.assertIsNone(policy.feed("alt", False, 10.1))

    def test_alt_tab_chord_does_not_stop(self) -> None:
        policy = self._recording_policy(10.0)
        policy.feed("alt", True, 11.0)
        policy.feed("tab", True, 11.05)
        policy.feed("tab", False, 11.1)
        self.assertIsNone(policy.feed("alt", False, 11.15))

    def test_release_without_armed_press_does_not_stop(self) -> None:
        # The keyup of the starting double-tap arrives after recording began.
        policy = make_policy()
        policy.feed("super", True, 10.0)
        policy.feed("alt", True, 10.05)
        policy.feed("alt", False, 10.1)
        self.assertEqual(policy.feed("alt", True, 10.2), "start")
        policy.set_recording(True, 10.2)
        self.assertIsNone(policy.feed("alt", False, 10.6))

    def test_stop_works_with_super_still_held(self) -> None:
        policy = self._recording_policy(10.0)
        policy.feed("super", True, 10.9)
        policy.feed("alt", True, 11.0)
        self.assertEqual(policy.feed("alt", False, 11.1), "stop")


class HotkeyPolicyMiscTests(unittest.TestCase):
    def test_modifiers_clear(self) -> None:
        policy = make_policy()
        self.assertTrue(policy.modifiers_clear())
        policy.feed("super", True, 10.0)
        self.assertFalse(policy.modifiers_clear())
        policy.feed("super", False, 10.1)
        self.assertTrue(policy.modifiers_clear())

    def test_reset_clears_pressed_state(self) -> None:
        policy = make_policy()
        policy.feed("super", True, 10.0)
        policy.reset()
        self.assertTrue(policy.modifiers_clear())


if __name__ == "__main__":
    unittest.main()
