"""Application-owned accessible modal dialogs."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import Enum
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .pdf_outputs import PdfCsvConflictChoice
from .visuals import (
    STYLE_BUTTON,
    STYLE_CARD_CAPTION,
    STYLE_PRIMARY_BUTTON,
    STYLE_SECTION,
    VisualManager,
    VisualMode,
)


_BLOCKED_SHORTCUTS = (
    "<Control-Key-1>",
    "<Control-Key-2>",
    "<F1>",
    "<F5>",
)


class DialogKind(Enum):
    HELP = "help"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CONFIRM = "confirm"

    @property
    def label(self) -> str:
        return {
            DialogKind.HELP: "クイックガイド",
            DialogKind.INFO: "情報",
            DialogKind.SUCCESS: "完了",
            DialogKind.WARNING: "注意",
            DialogKind.ERROR: "エラー",
            DialogKind.CONFIRM: "確認",
        }[self]

    @property
    def tone(self) -> str:
        if self is DialogKind.SUCCESS:
            return "success"
        if self is DialogKind.WARNING:
            return "warning"
        if self is DialogKind.ERROR:
            return "error"
        return "info"


@dataclass(frozen=True)
class DialogAction:
    label: str
    result: object = None
    command: Callable[[], None] | None = None
    closes_dialog: bool = True
    primary: bool = False
    is_default: bool = False


class DialogPresenter:
    def __init__(self, root: tk.Tk, visuals: VisualManager | None = None) -> None:
        self.root = root
        self.visuals = visuals or VisualManager(root)
        self.style = self.visuals.style
        self._configure_styles()

    def ask(self, title: str, message: str, action_label: str) -> bool:
        actions = (
            DialogAction("キャンセル", result=False, is_default=True),
            DialogAction(action_label, result=True, primary=True),
        )
        try:
            return bool(
                self._show_dialog(
                    DialogKind.CONFIRM,
                    title,
                    message,
                    actions=actions,
                    cancel_result=False,
                )
            )
        except tk.TclError:
            return bool(
                messagebox.askokcancel(
                    title,
                    message,
                    parent=self.root,
                    default=messagebox.CANCEL,
                )
            )

    def ask_pdf_csv_conflict(self, title: str, message: str) -> PdfCsvConflictChoice:
        actions = (
            DialogAction(
                "キャンセル",
                result=PdfCsvConflictChoice.CANCEL,
                is_default=True,
            ),
            DialogAction("置換する", result=PdfCsvConflictChoice.REPLACE),
            DialogAction(
                "別名で保存",
                result=PdfCsvConflictChoice.SAVE_AS_NEW,
                primary=True,
            ),
        )
        try:
            result = self._show_dialog(
                DialogKind.CONFIRM,
                title,
                message,
                actions=actions,
                cancel_result=PdfCsvConflictChoice.CANCEL,
            )
            return result if isinstance(result, PdfCsvConflictChoice) else PdfCsvConflictChoice.CANCEL
        except tk.TclError:
            native_message = (
                "「はい」＝置換する\n"
                "「いいえ」＝別名で保存\n"
                "「キャンセル」＝処理しない\n\n"
                f"{message}"
            )
            result = messagebox.askyesnocancel(
                title,
                native_message,
                parent=self.root,
                default=messagebox.CANCEL,
            )
            if result is True:
                return PdfCsvConflictChoice.REPLACE
            if result is False:
                return PdfCsvConflictChoice.SAVE_AS_NEW
            return PdfCsvConflictChoice.CANCEL

    def show(
        self,
        title: str,
        message: str,
        kind: DialogKind = DialogKind.INFO,
    ) -> None:
        try:
            self._show_dialog(
                kind,
                title,
                message,
                actions=(DialogAction("閉じる", is_default=True),),
                cancel_result=None,
            )
        except tk.TclError:
            self._native_notice(kind, title, message)

    def show_help(self, title: str) -> None:
        sections = (
            (
                "1. 求人PDF整理",
                "PDFフォルダーを選択し、必要ならCSV出力フォルダーを指定します。空欄時はPDFフォルダー内のoutputを使用します。事前確認ではファイルやフォルダーを変更しません。",
            ),
            (
                "2. 比較資料PDF生成",
                "比較Markdownと元PDF対応JSONを選択し、事前確認後にPDFを生成します。EdgeまたはChromeが必要です。",
            ),
            (
                "3. 補足とショートカット",
                "警告付き完了は異常終了ではありません。Ctrl+1 / Ctrl+2: タブ切替、F5: 事前確認、F1: 使い方。",
            ),
        )
        try:
            self._show_dialog(
                DialogKind.HELP,
                title,
                "",
                actions=(DialogAction("閉じる", is_default=True),),
                cancel_result=None,
                help_sections=sections,
                preferred_width=640,
            )
        except tk.TclError:
            message = "\n\n".join(f"{heading}\n{body}" for heading, body in sections)
            self._native_notice(DialogKind.INFO, title, message)

    def _show_dialog(
        self,
        kind: DialogKind,
        title: str,
        message: str,
        *,
        actions: tuple[DialogAction, ...],
        cancel_result: object,
        help_sections: tuple[tuple[str, str], ...] | None = None,
        preferred_width: int = 560,
    ) -> object:
        result = {"value": cancel_result}
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(True, True)
        dialog.configure(background=self.visuals.palette.app_background)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        previous_grab = self.root.grab_current()

        shell = ttk.Frame(dialog, padding=(18, 14, 18, 14), style="App.Dialog.TFrame")
        shell.grid(row=0, column=0, rowspan=3, sticky="nsew")
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        accent = ttk.Frame(
            shell,
            width=self.visuals.scaled_px(4),
            style=self._accent_style(kind),
        )
        accent.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, 12))
        header = ttk.Frame(shell, style="App.Dialog.TFrame")
        header.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        ttk.Label(
            header,
            text=kind.label,
            style=self._kind_style(kind),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=title,
            style="App.DialogTitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        content_shell = ttk.Frame(shell, style="App.Dialog.TFrame")
        content_shell.grid(row=1, column=1, sticky="nsew")
        content_shell.columnconfigure(0, weight=1)
        content_shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            content_shell,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
            background=self.visuals.palette.app_background,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content_shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas, style="App.Dialog.TFrame")
        window = canvas.create_window((0, 0), anchor="nw", window=content)
        content.columnconfigure(0, weight=1)

        if help_sections:
            for row, (heading, body) in enumerate(help_sections):
                card = self.visuals.create_card(content, padding=14)
                card.grid(row=row, column=0, sticky="ew", pady=(0, 8 if row < len(help_sections) - 1 else 0))
                card.body.columnconfigure(0, weight=1)
                ttk.Label(card.body, text=heading, style=STYLE_SECTION).grid(row=0, column=0, sticky="w")
                ttk.Label(
                    card.body,
                    text=body,
                    style=STYLE_CARD_CAPTION,
                    wraplength=preferred_width - 100,
                    justify="left",
                ).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        else:
            card = self.visuals.create_card(content, padding=14)
            card.grid(row=0, column=0, sticky="ew")
            card.body.columnconfigure(0, weight=1)
            ttk.Label(
                card.body,
                text=message,
                style="App.DialogBody.TLabel",
                wraplength=preferred_width - 100,
                justify="left",
            ).grid(row=0, column=0, sticky="ew")

        footer = ttk.Frame(shell, style="App.Dialog.TFrame")
        footer.grid(row=2, column=1, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        buttons = ttk.Frame(footer, style="App.Dialog.TFrame")
        buttons.grid(row=0, column=1, sticky="e")
        default_button: ttk.Button | None = None

        def close(value: object = cancel_result) -> None:
            result["value"] = value
            dialog.destroy()

        for column, action in enumerate(actions):
            def invoke(selected: DialogAction = action) -> None:
                if selected.command:
                    selected.command()
                if selected.closes_dialog:
                    close(selected.result)

            button = ttk.Button(
                buttons,
                text=action.label,
                command=invoke,
                style=STYLE_PRIMARY_BUTTON if action.primary else STYLE_BUTTON,
                default="active" if action.is_default else "normal",
            )
            button.grid(row=0, column=column, padx=(0 if column == 0 else 8, 0))
            if action.is_default:
                default_button = button

        def sync_content(event: tk.Event | None = None) -> None:
            del event
            canvas.itemconfigure(window, width=max(1, canvas.winfo_width()))
            canvas.configure(scrollregion=canvas.bbox("all"))

        content.bind("<Configure>", sync_content, add="+")
        canvas.bind("<Configure>", sync_content, add="+")
        dialog.update_idletasks()
        left, top, right, bottom = self._work_area()
        max_width = max(460, int((right - left) * 0.8))
        max_height = max(300, int((bottom - top) * 0.8))
        width = min(preferred_width, max_width)
        requested_content = content.winfo_reqheight()
        fixed_height = header.winfo_reqheight() + footer.winfo_reqheight() + 64
        height = min(max_height, max(300, requested_content + fixed_height))
        available_content = max(120, height - fixed_height)
        canvas.configure(height=available_content)
        if requested_content > available_content:
            scrollbar.grid(row=0, column=1, sticky="ns")
        else:
            scrollbar.grid_remove()

        owner_x = self.root.winfo_rootx()
        owner_y = self.root.winfo_rooty()
        owner_w = max(1, self.root.winfo_width())
        owner_h = max(1, self.root.winfo_height())
        x = min(max(left, owner_x + (owner_w - width) // 2), max(left, right - width))
        y = min(max(top, owner_y + (owner_h - height) // 2), max(top, bottom - height))
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        def cancel(event: tk.Event | None = None) -> str:
            del event
            close(cancel_result)
            return "break"

        def enter(event: tk.Event | None = None) -> str:
            del event
            focused = dialog.focus_get()
            if isinstance(focused, ttk.Button) and "disabled" not in focused.state():
                focused.invoke()
            elif default_button is not None and "disabled" not in default_button.state():
                default_button.invoke()
            return "break"

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", cancel)
        dialog.bind("<Return>", enter)
        for sequence in _BLOCKED_SHORTCUTS:
            dialog.bind(sequence, lambda event: "break")
        canvas.bind("<MouseWheel>", lambda event: self._scroll(canvas, event))
        dialog.bind("<Prior>", lambda event: self._scroll_units(canvas, -1, "pages"))
        dialog.bind("<Next>", lambda event: self._scroll_units(canvas, 1, "pages"))
        dialog.bind("<Home>", lambda event: self._scroll_to(canvas, 0.0))
        dialog.bind("<End>", lambda event: self._scroll_to(canvas, 1.0))
        dialog.deiconify()
        dialog.lift()
        default_button and default_button.focus_set()
        dialog.grab_set()
        self._play_notification(kind)
        try:
            self.root.wait_window(dialog)
        finally:
            if previous_grab is not None and previous_grab.winfo_exists():
                try:
                    previous_grab.grab_set()
                    previous_grab.focus_set()
                except tk.TclError:
                    pass
            elif self.root.winfo_exists():
                try:
                    self.root.focus_force()
                except tk.TclError:
                    pass
        return result["value"]

    def _configure_styles(self) -> None:
        palette = self.visuals.palette
        self.style.configure("App.Dialog.TFrame", background=palette.app_background)
        self.style.configure(
            "App.DialogTitle.TLabel",
            background=palette.app_background,
            foreground=palette.text,
            font=self.visuals.fonts["dialog_title"],
        )
        self.style.configure(
            "App.DialogBody.TLabel",
            background=palette.surface,
            foreground=palette.text,
            font=self.visuals.fonts["body"],
        )
        for tone in ("info", "success", "warning", "error"):
            color = self.visuals.tone_color(tone)
            self.style.configure(f"App.Dialog.{tone.title()}Accent.TFrame", background=color)
            self.style.configure(
                f"App.Dialog.{tone.title()}Kind.TLabel",
                background=palette.app_background,
                foreground=color,
                font=self.visuals.fonts["caption"],
            )

    def refresh_visuals(self) -> None:
        self._configure_styles()

    @staticmethod
    def _scroll(canvas: tk.Canvas, event: tk.Event) -> str:
        amount = -1 if event.delta > 0 else 1
        canvas.yview_scroll(amount, "units")
        return "break"

    @staticmethod
    def _scroll_units(canvas: tk.Canvas, amount: int, units: str) -> str:
        canvas.yview_scroll(amount, units)
        return "break"

    @staticmethod
    def _scroll_to(canvas: tk.Canvas, fraction: float) -> str:
        canvas.yview_moveto(fraction)
        return "break"

    def _accent_style(self, kind: DialogKind) -> str:
        return f"App.Dialog.{kind.tone.title()}Accent.TFrame"

    def _kind_style(self, kind: DialogKind) -> str:
        return f"App.Dialog.{kind.tone.title()}Kind.TLabel"

    def _work_area(self) -> tuple[int, int, int, int]:
        if os.name == "nt":
            try:
                from ctypes import wintypes

                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
                    0x0030, 0, ctypes.byref(rect), 0
                ):
                    return rect.left, rect.top, rect.right, rect.bottom
            except (AttributeError, OSError, TypeError, ctypes.ArgumentError):
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _native_notice(self, kind: DialogKind, title: str, message: str) -> None:
        if kind is DialogKind.WARNING:
            messagebox.showwarning(title, message, parent=self.root)
        elif kind is DialogKind.ERROR:
            messagebox.showerror(title, message, parent=self.root)
        else:
            messagebox.showinfo(title, message, parent=self.root)

    @staticmethod
    def _play_notification(kind: DialogKind) -> None:
        if os.name != "nt" or kind not in {DialogKind.WARNING, DialogKind.ERROR}:
            return
        try:
            import winsound

            sound = winsound.MB_ICONEXCLAMATION if kind is DialogKind.WARNING else winsound.MB_ICONHAND
            winsound.MessageBeep(sound)
        except (ImportError, OSError, RuntimeError):
            pass


__all__ = ["DialogAction", "DialogKind", "DialogPresenter"]
