from __future__ import annotations

import importlib
from pathlib import Path
import tkinter as tk


gui_main = importlib.import_module("job_comparison_app.gui.__main__")


class IconRoot:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def iconbitmap(self, *, default: str) -> None:
        self.calls.append(("iconbitmap", default))
        if self.error is not None:
            raise self.error


def test_set_window_icon_uses_packaged_icon(monkeypatch, tmp_path: Path) -> None:
    icon = tmp_path / "JobComparisonTool.ico"
    icon.write_bytes(b"icon")
    root = IconRoot()
    monkeypatch.setattr(gui_main, "APP_ICON_PATH", icon)

    gui_main._set_window_icon(root)

    assert root.calls == [("iconbitmap", str(icon))]


def test_set_window_icon_ignores_missing_asset(monkeypatch, tmp_path: Path) -> None:
    root = IconRoot()
    monkeypatch.setattr(gui_main, "APP_ICON_PATH", tmp_path / "missing.ico")

    gui_main._set_window_icon(root)

    assert root.calls == []


def test_set_window_icon_ignores_tk_load_error(monkeypatch, tmp_path: Path) -> None:
    icon = tmp_path / "JobComparisonTool.ico"
    icon.write_bytes(b"broken")
    root = IconRoot(tk.TclError("invalid icon"))
    monkeypatch.setattr(gui_main, "APP_ICON_PATH", icon)

    gui_main._set_window_icon(root)

    assert root.calls == [("iconbitmap", str(icon))]
