import ctypes
import ctypes.util
import gc
import threading
import time
from contextlib import contextmanager

from .asr import load_model


def release_freed_memory() -> None:
    """Ask glibc to return freed arenas to the OS after a model unload.

    Without this the process RSS stays at the model's peak (~500 MB) even
    though the memory is free inside the allocator.
    """
    try:
        libc_name = ctypes.util.find_library("c")
        if libc_name:
            ctypes.CDLL(libc_name).malloc_trim(0)
    except (OSError, AttributeError):
        pass


class ModelManager:
    def __init__(self) -> None:
        self._model = None
        self._device = "unloaded"
        self._active_users = 0
        self._last_used_at = time.monotonic()
        self._lock = threading.RLock()

    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None

    def device(self) -> str:
        with self._lock:
            return self._device

    def touch(self) -> None:
        with self._lock:
            self._last_used_at = time.monotonic()

    def ensure_loaded(self):
        with self._lock:
            if self._model is not None:
                self._last_used_at = time.monotonic()
                return self._model, False

            self._model, self._device = load_model()
            self._last_used_at = time.monotonic()
            return self._model, True

    @contextmanager
    def borrow(self):
        with self._lock:
            model, loaded_now = self.ensure_loaded()
            self._active_users += 1
            self._last_used_at = time.monotonic()

        try:
            yield model, loaded_now
        finally:
            with self._lock:
                self._active_users = max(0, self._active_users - 1)
                self._last_used_at = time.monotonic()

    def unload_if_idle(self, timeout_seconds: float) -> bool:
        with self._lock:
            if self._model is None or self._active_users > 0:
                return False
            if time.monotonic() - self._last_used_at < timeout_seconds:
                return False

            model = self._model
            self._model = None
            self._device = "unloaded"

        del model
        gc.collect()
        release_freed_memory()
        return True

    def unload_now(self) -> bool:
        with self._lock:
            self._last_used_at = 0
        return self.unload_if_idle(0)
