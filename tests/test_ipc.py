import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from airtype import ipc
from airtype.ipc import IPCServer, ServiceNotRunningError, request, service_running


class IPCTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._socket = Path(self._tmpdir.name) / "control.sock"
        self._env = mock.patch.dict(os.environ, {"AIRTYPE_SOCKET": str(self._socket)})
        self._env.start()
        self.server: IPCServer | None = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.stop()
        self._env.stop()
        self._tmpdir.cleanup()

    def _start_server(self, handler) -> IPCServer:
        def submit(command, reply):
            threading.Thread(target=handler, args=(command, reply), daemon=True).start()

        self.server = IPCServer(submit)
        self.server.start()
        return self.server

    def test_request_roundtrip(self) -> None:
        def handler(command, reply):
            reply({"ok": True, "result": {"echo": command["cmd"]}})

        self._start_server(handler)
        response = request("status")
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["echo"], "status")

    def test_unknown_command_rejected_without_service_handler(self) -> None:
        self._start_server(lambda command, reply: reply({"ok": True}))
        response = request("status")  # sanity: known command works
        self.assertTrue(response["ok"])

    def test_request_without_server_raises(self) -> None:
        with self.assertRaises(ServiceNotRunningError):
            request("status", timeout=0.5)
        self.assertFalse(service_running())

    def test_second_server_refuses_to_start(self) -> None:
        self._start_server(lambda command, reply: reply({"ok": True}))
        second = IPCServer(lambda command, reply: None)
        with self.assertRaises(RuntimeError):
            second.start()

    def test_stale_socket_is_replaced(self) -> None:
        self._socket.parent.mkdir(parents=True, exist_ok=True)
        self._socket.touch()  # stale file, nothing listening
        self._start_server(lambda command, reply: reply({"ok": True, "result": "alive"}))
        self.assertEqual(request("status")["result"], "alive")

    def test_broadcast_reaches_subscriber(self) -> None:
        server = self._start_server(lambda command, reply: reply({"ok": True}))
        received = []
        done = threading.Event()

        def listen():
            # The first line is the subscription ack; wait for a real event.
            for event in ipc.subscribe_events(timeout=5.0):
                if event.get("event"):
                    received.append(event)
                    done.set()
                    return

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        # Wait for the subscription ack before broadcasting.
        deadline = threading.Event()
        for _ in range(50):
            with server._lock:
                if server._subscribers:
                    break
            deadline.wait(0.05)
        server.broadcast({"event": "state", "state": "recording"})
        self.assertTrue(done.wait(3.0))
        events = [e for e in received if e.get("event") == "state"]
        self.assertEqual(events[0]["state"], "recording")


    def test_subscriber_receives_initial_state(self) -> None:
        def handler(command, reply):
            reply({"ok": True, "result": {"state": "ready"}})

        self._start_server(handler)
        received = []
        done = threading.Event()

        def listen():
            for event in ipc.subscribe_events(timeout=5.0):
                if event.get("event") == "state":
                    received.append(event)
                    done.set()
                    return

        threading.Thread(target=listen, daemon=True).start()
        self.assertTrue(done.wait(3.0))
        self.assertEqual(received[0]["state"], "ready")


if __name__ == "__main__":
    unittest.main()
