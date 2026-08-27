"""Unix-socket control channel between the airtype service and CLI/menu."""

import json
import os
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

REQUEST_TIMEOUT = 5.0
KNOWN_COMMANDS = ("toggle", "status", "reload-config", "quit", "subscribe")


class ServiceNotRunningError(RuntimeError):
    pass


def socket_path() -> Path:
    env = os.environ.get("AIRTYPE_SOCKET")
    if env:
        return Path(env)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/airtype-{os.getuid()}"
    return Path(runtime_dir) / "airtype" / "control.sock"


class IPCServer:
    """Accepts connections and forwards commands to the service main loop.

    handle(command: dict, reply: Callable[[dict], None]) runs on the service
    thread; subscribe connections are kept open and receive broadcast events.
    """

    def __init__(self, submit: Callable[[dict, Callable[[dict], None]], None]) -> None:
        self._submit = submit
        self._path = socket_path()
        self._server: socket.socket | None = None
        self._subscribers: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        if self._path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1.0)
            try:
                probe.connect(str(self._path))
                probe.close()
                raise RuntimeError(
                    f"another airtype service is already running ({self._path})"
                )
            except (ConnectionRefusedError, socket.timeout, FileNotFoundError, OSError):
                self._path.unlink(missing_ok=True)
            finally:
                probe.close()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self._path))
        os.chmod(self._path, 0o600)
        self._server.listen(8)
        self._server.settimeout(0.5)
        self._thread = threading.Thread(
            target=self._accept_loop, name="airtype-ipc-accept", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for conn in list(self._subscribers):
                try:
                    conn.close()
                except OSError:
                    pass
            self._subscribers.clear()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        self._path.unlink(missing_ok=True)

    def broadcast(self, event: dict[str, Any]) -> None:
        payload = (json.dumps(event) + "\n").encode()
        with self._lock:
            dead = []
            for conn in self._subscribers:
                try:
                    conn.sendall(payload)
                except OSError:
                    dead.append(conn)
            for conn in dead:
                self._subscribers.discard(conn)
                try:
                    conn.close()
                except OSError:
                    pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._server is not None:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle_connection,
                args=(conn,),
                name="airtype-ipc-conn",
                daemon=True,
            )
            thread.start()

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            reader = conn.makefile("r", encoding="utf-8")
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    request = {"cmd": line}
                if not isinstance(request, dict):
                    request = {"cmd": str(request)}
                command = str(request.get("cmd", "")).strip().lower()

                if command == "subscribe":
                    with self._lock:
                        self._subscribers.add(conn)
                    self._send(conn, {"ok": True, "result": "subscribed"})
                    return  # connection stays open for broadcasts only

                if command not in KNOWN_COMMANDS:
                    self._send(conn, {"ok": False, "error": f"unknown command: {command}"})
                    continue

                done = threading.Event()
                response: dict[str, Any] = {}

                def reply(result: dict[str, Any]) -> None:
                    response.update(result)
                    done.set()

                self._submit({**request, "cmd": command}, reply)
                if done.wait(REQUEST_TIMEOUT):
                    self._send(conn, response)
                else:
                    self._send(conn, {"ok": False, "error": "service busy"})
        except OSError:
            pass
        finally:
            with self._lock:
                if conn not in self._subscribers:
                    try:
                        conn.close()
                    except OSError:
                        pass

    @staticmethod
    def _send(conn: socket.socket, payload: dict[str, Any]) -> None:
        try:
            conn.sendall((json.dumps(payload) + "\n").encode())
        except OSError:
            pass


def request(command: str, timeout: float = REQUEST_TIMEOUT, **fields: Any) -> dict[str, Any]:
    path = socket_path()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall((json.dumps({"cmd": command, **fields}) + "\n").encode())
        reader = client.makefile("r", encoding="utf-8")
        line = reader.readline()
        if not line:
            raise ServiceNotRunningError("airtype service closed the connection")
        return json.loads(line)
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout) as exc:
        raise ServiceNotRunningError(
            "airtype service is not running (start it with: systemctl --user start airtype)"
        ) from exc
    finally:
        client.close()


def subscribe_events(timeout: float | None = None) -> Iterator[dict[str, Any]]:
    """Yield service events until the connection closes."""
    path = socket_path()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None:
        client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall((json.dumps({"cmd": "subscribe"}) + "\n").encode())
        reader = client.makefile("r", encoding="utf-8")
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    except (ConnectionRefusedError, FileNotFoundError) as exc:
        raise ServiceNotRunningError("airtype service is not running") from exc
    finally:
        client.close()


def service_running() -> bool:
    try:
        response = request("status", timeout=1.0)
        return bool(response.get("ok"))
    except (ServiceNotRunningError, OSError):
        return False
