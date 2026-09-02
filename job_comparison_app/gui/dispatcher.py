"""Thread-safe delivery of callbacks to the Tk main thread."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, SimpleQueue
from threading import Lock
import tkinter as tk


class MainThreadDispatcher:
    """Poll a thread-safe queue from Tk without calling Tk from workers."""

    _POLL_INTERVAL_MS = 10
    _MAX_CALLBACKS_PER_POLL = 100

    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._queue: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._state_lock = Lock()
        self._closed = False
        self._after_id: str | None = None
        self._root.bind("<Destroy>", self._root_destroyed, add="+")
        self._schedule()

    def post(self, callback: Callable[[], None]) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._queue.put(callback)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            after_id = self._after_id
            self._after_id = None
        if after_id is not None:
            try:
                self._root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._discard_pending()

    def _schedule(self) -> None:
        with self._state_lock:
            if self._closed or self._after_id is not None:
                return
            try:
                self._after_id = self._root.after(
                    self._POLL_INTERVAL_MS, self._drain
                )
            except tk.TclError:
                self._closed = True

    def _drain(self) -> None:
        with self._state_lock:
            self._after_id = None
            if self._closed:
                return

        for _ in range(self._MAX_CALLBACKS_PER_POLL):
            try:
                callback = self._queue.get_nowait()
            except Empty:
                break
            try:
                callback()
            except BaseException as error:
                try:
                    self._root.report_callback_exception(
                        type(error), error, error.__traceback__
                    )
                except tk.TclError:
                    self.close()
                    return

        self._schedule()

    def _discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return

    def _root_destroyed(self, event: tk.Event) -> None:
        if event.widget is self._root:
            self.close()


__all__ = ["MainThreadDispatcher"]
