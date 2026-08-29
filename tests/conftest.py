from __future__ import annotations

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_interpreter() -> tk.Tk:
    """Keep one Tcl/Tk interpreter alive for the Windows GUI test session."""

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_window(tk_interpreter: tk.Tk) -> tk.Toplevel:
    window = tk.Toplevel(tk_interpreter)
    yield window
    if window.winfo_exists():
        window.destroy()
