from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from unittest.mock import patch

from job_comparison_app.gui.dialogs import DialogKind, DialogPresenter
from job_comparison_app.gui.pdf_outputs import OutputConflictChoice
import job_comparison_app.gui.visuals as visuals


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _schedule_dialog(root: tk.Tk, callback) -> None:
    def locate() -> None:
        dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
        if not dialogs:
            root.after(20, locate)
            return
        callback(dialogs[-1])

    root.after(20, locate)


def _button(dialog: tk.Toplevel, text: str) -> ttk.Button:
    return next(
        widget
        for widget in _walk(dialog)
        if isinstance(widget, ttk.Button) and str(widget.cget("text")) == text
    )


def test_confirmation_defaults_to_cancel_and_action_returns_true(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        with patch.object(visuals, "detect_high_contrast", return_value=False):
            presenter = DialogPresenter(root)
        observed = {}

        def inspect(dialog) -> None:
            observed["default"] = str(_button(dialog, "キャンセル").cget("default"))
            _button(dialog, "実行").invoke()

        _schedule_dialog(root, inspect)
        assert presenter.ask("確認", "続行しますか？", "実行") is True
        assert observed["default"] == "active"
        assert root.grab_current() is None
    finally:
        root.destroy()


def test_escape_cancels_confirmation(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        presenter = DialogPresenter(root)
        observed = {}

        def send_escape(dialog) -> None:
            observed["escape_binding"] = bool(dialog.bind("<Escape>"))
            _button(dialog, "キャンセル").invoke()

        _schedule_dialog(root, send_escape)
        assert presenter.ask("確認", "続行しますか？", "実行") is False
        assert observed["escape_binding"]
    finally:
        root.destroy()


def test_help_contains_steps_and_long_notice_scrolls(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        presenter = DialogPresenter(root)
        texts: list[str] = []

        def inspect_help(dialog) -> None:
            texts.extend(
                str(widget.cget("text"))
                for widget in _walk(dialog)
                if isinstance(widget, ttk.Label)
            )
            _button(dialog, "閉じる").invoke()

        _schedule_dialog(root, inspect_help)
        presenter.show_help("使い方")
        combined = "\n".join(texts)
        assert "1. 転職者条件" in combined
        assert "2. 求人取得" in combined
        assert "3. 求人PDF整理" in combined
        assert "4. 求人PDF全文統合テキスト化" in combined
        assert "5. 比較資料PDF生成" in combined
        assert "6. 補足とショートカット" in combined
        assert "Ctrl+1～5" in combined

        observed = {}

        def inspect_long(dialog) -> None:
            observed["scroll"] = any(
                isinstance(widget, ttk.Scrollbar) and widget.winfo_manager() == "grid"
                for widget in _walk(dialog)
            )
            _button(dialog, "閉じる").invoke()

        presenter._work_area = lambda: (0, 0, 520, 320)
        _schedule_dialog(root, inspect_long)
        presenter.show("長い案内", "\n".join(f"案内行 {index}" for index in range(60)))
        assert observed["scroll"]
    finally:
        root.destroy()


def test_native_messagebox_fallbacks_preserve_semantics(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        presenter = DialogPresenter(root)
        with (
            patch.object(presenter, "_show_dialog", side_effect=tk.TclError),
            patch.object(messagebox, "askokcancel", return_value=False) as ask,
        ):
            assert presenter.ask("確認", "本文", "実行") is False
            ask.assert_called_once()
        with (
            patch.object(presenter, "_show_dialog", side_effect=tk.TclError),
            patch.object(messagebox, "showerror") as showerror,
        ):
            presenter.show("失敗", "本文", DialogKind.ERROR)
            showerror.assert_called_once()
    finally:
        root.destroy()


def test_csv_conflict_dialog_returns_typed_choice(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        presenter = DialogPresenter(root)

        def choose_new(dialog) -> None:
            _button(dialog, "別名で保存").invoke()

        _schedule_dialog(root, choose_new)
        assert (
            presenter.ask_pdf_csv_conflict("既存のCSVがあります", "選択してください。")
            is OutputConflictChoice.SAVE_AS_NEW
        )
    finally:
        root.destroy()


def test_csv_conflict_native_fallback_maps_yes_no_cancel(tk_window) -> None:
    root = tk_window
    root.withdraw()
    try:
        presenter = DialogPresenter(root)
        for native, expected in (
            (True, OutputConflictChoice.REPLACE),
            (False, OutputConflictChoice.SAVE_AS_NEW),
            (None, OutputConflictChoice.CANCEL),
        ):
            with (
                patch.object(presenter, "_show_dialog", side_effect=tk.TclError),
                patch.object(messagebox, "askyesnocancel", return_value=native),
            ):
                assert presenter.ask_pdf_csv_conflict("既存", "本文") is expected
    finally:
        root.destroy()
