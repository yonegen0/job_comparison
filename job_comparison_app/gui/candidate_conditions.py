"""Session-only candidate conditions form for AI job comparison."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from recruit_job_tool.candidate_profile import (
    CANDIDATE_AGE_CHOICES,
    CANDIDATE_EDUCATION_CHOICES,
    CANDIDATE_GENDER_CHOICES,
    CandidateProfile,
    _CandidateProfileFieldError,
)

from .visuals import (
    STYLE_CARD_BODY,
    STYLE_CARD_CAPTION,
    STYLE_CARD_ERROR,
    STYLE_FILE,
    STYLE_SECTION,
    VisualManager,
)


_REQUIRED_FIELDS = ("age", "gender", "education")
_FIELD_LABELS = {
    "age": "年齢",
    "gender": "性別",
    "education": "学歴",
    "work_history": "職歴",
    "qualifications": "資格",
}


class CandidateConditionsPanel:
    """Collect and validate candidate conditions without persisting them."""

    def __init__(
        self,
        *,
        root: tk.Tk,
        parent: ttk.Frame,
        visuals: VisualManager,
    ) -> None:
        self.root = root
        self.parent = parent
        self.visuals = visuals
        self.variables = {
            "age": tk.StringVar(),
            "gender": tk.StringVar(),
            "education": tk.StringVar(),
        }
        self.errors = {
            field: tk.StringVar(value="")
            for field in (*_REQUIRED_FIELDS, "work_history", "qualifications")
        }
        self.widgets: dict[str, tk.Widget] = {}
        self._build()

    def _build(self) -> None:
        self.parent.columnconfigure(0, weight=1)

        notice = self.visuals.create_card(self.parent, padding=14)
        notice.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(notice.body, text="転職者条件", style=STYLE_SECTION).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            notice.body,
            text=(
                "AI比較で求人の必須条件を確認するために必要な範囲だけ入力してください。"
                "入力内容はこの起動中だけ保持し、LOCALAPPDATA、求人JSON、CSV、"
                "保存求人、実行ログには保存しません。\n"
                "条件付き比較を実行すると生成したMarkdownガイドには記録されます。"
                "氏名、住所、連絡先など不要な個人情報は入力しないでください。"
            ),
            style=STYLE_CARD_CAPTION,
            justify="left",
            wraplength=760,
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        required = self.visuals.create_card(self.parent, padding=14)
        required.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        required.body.columnconfigure(0, weight=1)
        ttk.Label(required.body, text="必須項目", style=STYLE_SECTION).grid(
            row=0, column=0, sticky="w"
        )
        for offset, (field, label, choices) in enumerate(
            (
                ("age", "年齢（必須）", CANDIDATE_AGE_CHOICES),
                ("gender", "性別（必須）", CANDIDATE_GENDER_CHOICES),
                ("education", "学歴（必須）", CANDIDATE_EDUCATION_CHOICES),
            )
        ):
            row = 1 + offset * 3
            ttk.Label(required.body, text=label, style=STYLE_FILE).grid(
                row=row, column=0, sticky="w", pady=(7 if offset else 5, 0)
            )
            combo = ttk.Combobox(
                required.body,
                textvariable=self.variables[field],
                values=choices,
                state="readonly",
                width=36,
            )
            combo.grid(row=row + 1, column=0, sticky="ew", pady=(2, 0))
            combo.bind(
                "<<ComboboxSelected>>",
                lambda event, name=field: self.clear_error(name),
                add="+",
            )
            self.widgets[field] = combo
            ttk.Label(
                required.body,
                textvariable=self.errors[field],
                style=STYLE_CARD_ERROR,
            ).grid(row=row + 2, column=0, sticky="w")

        optional = self.visuals.create_card(self.parent, padding=14)
        optional.grid(row=2, column=0, sticky="ew")
        optional.body.columnconfigure(0, weight=1)
        ttk.Label(optional.body, text="任意項目", style=STYLE_SECTION).grid(
            row=0, column=0, sticky="w"
        )
        for offset, (field, label, height, initial) in enumerate(
            (
                ("work_history", "職歴（任意・4,000文字以内）", 5, ""),
                ("qualifications", "資格（任意・4,000文字以内）", 4, "普通運転免許"),
            )
        ):
            row = 1 + offset * 3
            ttk.Label(optional.body, text=label, style=STYLE_FILE).grid(
                row=row, column=0, sticky="w", pady=(7 if offset else 5, 0)
            )
            editor = tk.Text(
                optional.body,
                height=height,
                width=44,
                wrap="word",
                undo=True,
                font=self.visuals.fonts["body"],
                relief="solid",
                borderwidth=1,
                background=self.visuals.palette.surface,
                foreground=self.visuals.palette.text,
                insertbackground=self.visuals.palette.text,
                selectbackground=self.visuals.palette.accent,
            )
            editor.insert("1.0", initial)
            editor.grid(row=row + 1, column=0, sticky="ew", pady=(2, 0))
            editor.bind("<KeyPress>", lambda event, name=field: self.clear_error(name), add="+")
            editor.bind("<Tab>", self._focus_next, add="+")
            editor.bind("<Shift-Tab>", self._focus_previous, add="+")
            self.widgets[field] = editor
            ttk.Label(
                optional.body,
                textvariable=self.errors[field],
                style=STYLE_CARD_ERROR,
            ).grid(row=row + 2, column=0, sticky="w")

    def missing_required_fields(self) -> tuple[str, ...]:
        return tuple(field for field in _REQUIRED_FIELDS if not self.variables[field].get())

    def show_missing_required(self, fields: tuple[str, ...]) -> None:
        self.clear_errors()
        for field in fields:
            self.errors[field].set(f"{_FIELD_LABELS[field]}を選択してください。")
        if fields:
            self.focus_field(fields[0])

    def profile(self) -> CandidateProfile:
        self.clear_errors()
        try:
            return CandidateProfile(
                age=self.variables["age"].get(),
                gender=self.variables["gender"].get(),
                education=self.variables["education"].get(),
                work_history=self._text_value("work_history"),
                qualifications=self._text_value("qualifications"),
            )
        except _CandidateProfileFieldError as error:
            self.errors[error.field].set(str(error))
            self.focus_field(error.field)
            raise

    def clear_error(self, field: str) -> None:
        error = self.errors.get(field)
        if error is not None:
            error.set("")

    def clear_errors(self) -> None:
        for error in self.errors.values():
            error.set("")

    def focus_field(self, field: str) -> None:
        widget = self.widgets.get(field)
        if widget is not None:
            widget.focus_set()

    def set_host_busy(self, busy: bool) -> None:
        for field in _REQUIRED_FIELDS:
            self.widgets[field].configure(state="disabled" if busy else "readonly")
        for field in ("work_history", "qualifications"):
            self.widgets[field].configure(state="disabled" if busy else "normal")

    def _text_value(self, field: str) -> str:
        widget = self.widgets[field]
        if not isinstance(widget, tk.Text):
            return ""
        return widget.get("1.0", "end-1c")

    @staticmethod
    def _focus_next(event: tk.Event) -> str:
        target = CandidateConditionsPanel._tk_focus_target(event.widget, forward=True)
        if target is not None:
            target.focus_set()
        return "break"

    @staticmethod
    def _focus_previous(event: tk.Event) -> str:
        target = CandidateConditionsPanel._tk_focus_target(event.widget, forward=False)
        if target is not None:
            target.focus_set()
        return "break"

    @staticmethod
    def _tk_focus_target(widget: tk.Misc, *, forward: bool) -> tk.Misc | None:
        resolver = widget.tk_focusNext if forward else widget.tk_focusPrev
        try:
            return resolver()
        except tk.TclError as exc:
            if "invalid command name" not in str(exc):
                raise
            tk_library = widget.tk.call("set", "tk_library")
            focus_script = widget.tk.call("file", "join", tk_library, "focus.tcl")
            widget.tk.call("source", focus_script)
            return resolver()


__all__ = ["CandidateConditionsPanel"]
