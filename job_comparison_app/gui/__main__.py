from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from ..application import JobComparisonApplication
from ..errors import AlreadyRunningError, JobComparisonAppError
from ..single_instance import SingleInstanceLock
from .controller import Controller
from .view import TkView, WindowsOutputOpener


APP_ICON_PATH = Path(__file__).resolve().parents[1] / "assets" / "JobComparisonTool.ico"


def _set_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("user32", use_last_error=True).SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _work_area(root: tk.Tk) -> tuple[int, int, int, int]:
    if os.name == "nt":
        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = Rect()
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _fit_to_work_area(root: tk.Tk) -> None:
    root.update_idletasks()
    left, top, right, bottom = _work_area(root)
    work_width = max(1, right - left)
    work_height = max(1, bottom - top)
    minimum_width = min(work_width, max(760, root.winfo_reqwidth()))
    minimum_height = min(work_height, max(600, root.winfo_reqheight()))
    width = min(work_width, max(960, minimum_width))
    height = min(work_height, max(700, minimum_height))
    x = left + max(0, (work_width - width) // 2)
    y = top + max(0, (work_height - height) // 2)
    root.minsize(minimum_width, minimum_height)
    root.geometry(f"{width}x{height}+{x}+{y}")


def _set_window_icon(root: tk.Tk) -> None:
    try:
        if APP_ICON_PATH.is_file():
            root.iconbitmap(default=str(APP_ICON_PATH))
    except (OSError, tk.TclError):
        # The icon is cosmetic; a missing or unreadable asset must not prevent startup.
        pass


def _show_startup_error(title: str, message: str, root: tk.Tk | None = None) -> None:
    temporary: tk.Tk | None = None
    try:
        if root is None:
            temporary = tk.Tk()
            temporary.withdraw()
            root = temporary
        messagebox.showerror(title, message, parent=root)
    except tk.TclError:
        pass
    finally:
        if temporary is not None:
            temporary.destroy()


def main() -> int:
    _set_dpi_awareness()
    lock = SingleInstanceLock()
    root: tk.Tk | None = None
    try:
        lock.acquire()
        root = tk.Tk()
        _set_window_icon(root)
        root.withdraw()
        root.geometry("960x700")
        application = JobComparisonApplication()
        view = TkView(root)
        controller = Controller(
            view=view,
            application=application,
            output_opener=WindowsOutputOpener(),
        )
        view.bind_controller(controller)
        _fit_to_work_area(root)
        root.deiconify()
        controller.start()
        root.mainloop()
        return 0
    except AlreadyRunningError as exc:
        _show_startup_error("求人比較ツール", exc.user_message, root)
        return 1
    except JobComparisonAppError as exc:
        _show_startup_error("求人比較ツールを起動できません", str(exc), root)
        return 1
    except Exception:
        _show_startup_error(
            "求人比較ツールを起動できません",
            "予期しないエラーが発生しました（エラーコード: APP001）。",
            root,
        )
        return 1
    finally:
        try:
            lock.release()
        except JobComparisonAppError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
