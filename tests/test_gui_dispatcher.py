from __future__ import annotations

import threading
import time
import tkinter as tk

from job_comparison_app.gui.dispatcher import MainThreadDispatcher


def _pump_until(root: tk.Misc, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Tk callback was not delivered before the timeout")


def test_worker_post_runs_on_main_thread_in_fifo_order(tk_window) -> None:
    root = tk_window
    root.withdraw()
    dispatcher = MainThreadDispatcher(root)
    main_thread = threading.get_ident()
    delivered: list[tuple[int, int]] = []

    worker = threading.Thread(
        target=lambda: (
            dispatcher.post(lambda: delivered.append((1, threading.get_ident()))),
            dispatcher.post(lambda: delivered.append((2, threading.get_ident()))),
        )
    )
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert delivered == []
    _pump_until(root, lambda: len(delivered) == 2)
    assert delivered == [(1, main_thread), (2, main_thread)]
    dispatcher.close()


def test_callback_error_is_reported_and_does_not_stop_queue(tk_window) -> None:
    root = tk_window
    root.withdraw()
    dispatcher = MainThreadDispatcher(root)
    errors: list[tuple[type[BaseException], str]] = []
    delivered: list[str] = []
    root.report_callback_exception = lambda error_type, error, traceback: errors.append(
        (error_type, str(error))
    )

    def fail() -> None:
        raise RuntimeError("dispatcher-test")

    dispatcher.post(fail)
    dispatcher.post(lambda: delivered.append("after-error"))

    _pump_until(root, lambda: bool(delivered))
    assert errors == [(RuntimeError, "dispatcher-test")]
    assert delivered == ["after-error"]
    dispatcher.close()


def test_close_and_root_destroy_discard_late_callbacks(
    tk_interpreter: tk.Tk,
) -> None:
    first_root = tk.Toplevel(tk_interpreter)
    first_root.withdraw()
    first = MainThreadDispatcher(first_root)
    delivered: list[str] = []
    first.close()
    first.post(lambda: delivered.append("closed"))
    first_root.update()
    assert delivered == []
    first_root.destroy()

    second_root = tk.Toplevel(tk_interpreter)
    second_root.withdraw()
    second = MainThreadDispatcher(second_root)
    second_root.destroy()
    second.post(lambda: delivered.append("destroyed"))
    tk_interpreter.update()
    assert delivered == []
