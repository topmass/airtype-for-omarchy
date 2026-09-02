import unittest
from types import SimpleNamespace
from unittest import mock

from airtype import service
from airtype.service import AirtypeService


def make_stub(held: set[str], dir_mtime: float) -> SimpleNamespace:
    """Bare object with only the attributes _restart_listener_on_hotplug touches."""
    stub = SimpleNamespace(
        _listener_backend="evdev",
        _state="ready",
        _input_dir_mtime=0.0,
        _listener=SimpleNamespace(device_paths=set(held)),
        _hotkey_keys=lambda: ["super", "alt"],
        _current_input_dir_mtime=lambda: dir_mtime,
        restarts=[],
    )
    stub._restart_listener = stub.restarts.append
    return stub


def run_hotplug_check(stub, now: float, scan_result) -> None:
    with mock.patch.object(service.time, "time", return_value=now), mock.patch.object(
        service, "scan_hotkey_devices", return_value=scan_result
    ):
        AirtypeService._restart_listener_on_hotplug(stub)


class HotplugRaceTests(unittest.TestCase):
    HELD = {"/dev/input/event3"}
    NEW = {"/dev/input/event3", "/dev/input/event24"}

    def test_unchanged_mtime_does_nothing(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        stub._input_dir_mtime = 100.0
        with mock.patch.object(service, "scan_hotkey_devices") as scan:
            AirtypeService._restart_listener_on_hotplug(stub)
        scan.assert_not_called()
        self.assertEqual(stub.restarts, [])

    def test_waits_for_directory_to_settle(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        with mock.patch.object(service, "scan_hotkey_devices") as scan:
            with mock.patch.object(service.time, "time", return_value=100.5):
                AirtypeService._restart_listener_on_hotplug(stub)
        scan.assert_not_called()
        self.assertEqual(stub._input_dir_mtime, 0.0)

    def test_restarts_once_new_keyboard_is_readable(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        run_hotplug_check(stub, now=101.2, scan_result=(self.NEW, set()))
        self.assertEqual(len(stub.restarts), 1)
        self.assertEqual(stub._input_dir_mtime, 100.0)

    def test_retries_while_node_is_unreadable_then_picks_it_up(self):
        # Tick 1: udev has not chown'd event24 yet -> skip, keep old mtime.
        stub = make_stub(self.HELD, dir_mtime=100.0)
        run_hotplug_check(stub, now=101.2, scan_result=(self.HELD, {"/dev/input/event24"}))
        self.assertEqual(stub.restarts, [])
        self.assertEqual(stub._input_dir_mtime, 0.0)
        # Tick 2: node is readable now -> restart with the full set.
        run_hotplug_check(stub, now=101.4, scan_result=(self.NEW, set()))
        self.assertEqual(len(stub.restarts), 1)
        self.assertEqual(stub._input_dir_mtime, 100.0)

    def test_gives_up_on_permanently_unreadable_node(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        run_hotplug_check(stub, now=106.0, scan_result=(self.HELD, {"/dev/input/event99"}))
        self.assertEqual(stub.restarts, [])
        # mtime recorded: no rescans every tick forever.
        self.assertEqual(stub._input_dir_mtime, 100.0)

    def test_clock_stepped_back_is_treated_as_settled(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        run_hotplug_check(stub, now=50.0, scan_result=(self.NEW, set()))
        self.assertEqual(len(stub.restarts), 1)

    def test_skips_while_recording(self):
        stub = make_stub(self.HELD, dir_mtime=100.0)
        stub._state = "recording"
        with mock.patch.object(service, "scan_hotkey_devices") as scan:
            with mock.patch.object(service.time, "time", return_value=200.0):
                AirtypeService._restart_listener_on_hotplug(stub)
        scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
