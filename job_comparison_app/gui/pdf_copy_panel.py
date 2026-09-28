"""Selected PDF copy panel using the application's existing visual primitives."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .view import TkView
    from .controller import ViewModel

from ..errors import InputValidationError
from ..pdf_copy import CopyStatus, PdfCopyRequest, PdfCopyPlan, PdfCopyDecision, PdfCopyEntry
from .dialogs import DialogAction
from .pdf_outputs import OutputConflictChoice
from .visuals import STYLE_CARD_BODY, STYLE_FILE, STYLE_CARD_CAPTION, STYLE_BUTTON, STYLE_PRIMARY_BUTTON


class PdfCopyPanel:
    def __init__(self, view: TkView, parent: ttk.Frame) -> None:
        self.view = view
        self.folder = tk.StringVar(master=parent)
        self.output = tk.StringVar(master=parent, value="フォルダー選択後に表示")
        self.hint = tk.StringVar(master=parent)
        self.metrics = {name: tk.StringVar(master=parent, value="未確認") for name in
                        ("対象件数", "コピー成功", "未発見", "失敗")}
        self._displayed = None
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        view._build_primary_metrics(parent, tuple(self.metrics), self.metrics)
        card = view.visuals.create_card(parent, padding=8)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        form = card.body
        form.columnconfigure(0, weight=1)
        view._section_heading(form, "1. 入力と出力", "フォルダー直下から、指定したPDFだけをコピーします。")
        fields = ttk.Frame(form, style=STYLE_CARD_BODY)
        fields.grid(row=1, column=0, sticky="ew")
        fields.columnconfigure(0, weight=1)
        view._path_field(fields, 0, 0, "PDFフォルダー（必須）", self.folder, self.choose_folder, "参照…")
        ttk.Label(form, text="PDFファイル名一覧（必須・1行1ファイル名）", style=STYLE_FILE).grid(row=2, column=0, sticky="w")
        editor_frame = ttk.Frame(form, style=STYLE_CARD_BODY)
        editor_frame.grid(row=3, column=0, sticky="ew")
        editor_frame.columnconfigure(0, weight=1)
        palette = view.visuals.palette
        self.editor = tk.Text(editor_frame, height=6, width=40, wrap="word", undo=True,
                              font=view.visuals.fonts["body"], relief="solid", borderwidth=1,
                              background=palette.surface, foreground=palette.text,
                              insertbackground=palette.text, selectbackground=palette.accent)
        self.editor.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(editor_frame, command=self.editor.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.editor.configure(yscrollcommand=scrollbar.set)
        self.editor.bind("<<Modified>>", self.modified)
        self.editor.bind("<Tab>", lambda event: self.focus_next(False))
        self.editor.bind("<Shift-Tab>", lambda event: self.focus_next(True))
        self.editor.bind("<MouseWheel>", self.wheel)
        view._input_widgets.append(self.editor)
        ttk.Label(form, text="拡張子.pdfを含めて入力してください。例：株式会社○○（営業職）.pdf", style=STYLE_CARD_CAPTION).grid(row=4, column=0, sticky="w")
        ttk.Label(form, textvariable=self.hint, style=STYLE_CARD_CAPTION).grid(row=5, column=0, sticky="w")
        ttk.Label(form, textvariable=self.output, style=STYLE_CARD_CAPTION, wraplength=650).grid(row=6, column=0, sticky="w")
        result_card = view.visuals.create_card(parent, padding=8)
        result_card.grid(row=2, column=0, sticky="nsew")
        body = result_card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        view._section_heading(body, "2. 事前確認と実行", "未発見・不正入力を確認してからコピーしてください。")
        actions = ttk.Frame(body, style=STYLE_CARD_BODY)
        actions.grid(row=1, column=0, sticky="ew", pady=4)
        actions.columnconfigure(2, weight=1)
        self.preview = ttk.Button(actions, text="事前確認", style=STYLE_BUTTON, command=lambda: self.action("preview_copy"))
        self.preview.grid(row=0, column=0)
        self.apply = ttk.Button(actions, text="PDFをコピー", style=STYLE_PRIMARY_BUTTON, command=lambda: self.action("apply_copy"))
        self.apply.grid(row=0, column=1, padx=8)
        self.open = ttk.Button(actions, text="出力フォルダーを開く", style=STYLE_BUTTON, command=lambda: self.action("open_copy_output_folder"))
        self.open.grid(row=0, column=3)
        self.tree, self.empty = view._build_tree(body, row=2,
            columns=("line", "input", "output", "status", "reason"),
            headings=("行番号", "入力ファイル名", "保存先ファイル名", "状態", "理由"),
            widths=(55, 230, 230, 100, 240), empty_text="PDFフォルダーとファイル名一覧を入力し、事前確認してください。")
        self.folder.trace_add("write", self.changed)

    def action(self, name: str) -> None:
        if self.view.controller and not self.view._busy:
            getattr(self.view.controller, name)()

    def choose_folder(self) -> None:
        current = self.folder.get().strip()
        selected = filedialog.askdirectory(parent=self.view.root, title="PDFフォルダーを選択", mustexist=True,
                                          initialdir=current if current and Path(current).is_dir() else str(Path.home()))
        if selected:
            self.folder.set(selected)

    def modified(self, event: tk.Event | None = None) -> None:
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            self.changed()

    def changed(self, *_args: str) -> None:
        value = self.folder.get().strip()
        self.output.set(f"コピー先：{Path(value) / '対象pdf'}" if value else "フォルダー選択後に表示")
        self.action("invalidate_copy")

    def focus_next(self, previous: bool) -> str:
        target = self.editor.tk_focusPrev() if previous else self.editor.tk_focusNext()
        target.focus_set()
        return "break"

    def wheel(self, event: tk.Event) -> str:
        self.editor.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def request(self) -> PdfCopyRequest:
        if not self.folder.get().strip():
            raise InputValidationError("PDFフォルダーを指定してください。")
        return PdfCopyRequest(Path(self.folder.get().strip()), self.editor.get("1.0", "end-1c"))

    def render(self, model: ViewModel) -> None:
        ready = bool(self.folder.get().strip() and self.editor.get("1.0", "end-1c").strip())
        for button in (self.preview, self.apply):
            button.configure(state="normal" if ready and not model.busy else "disabled")
        self.open.configure(state="normal" if model.copy_result and model.copy_result.output_dir.is_dir() and not model.busy else "disabled")
        data = model.copy_result or model.copy_plan
        if data == self._displayed:
            return
        self._displayed = data
        entries = data.entries if data else ()
        self.tree.delete(*self.tree.get_children())
        for entry in entries:
            self.tree.insert("", "end", values=(entry.line, entry.name, entry.destination, entry.status.value, entry.reason))
        success = (CopyStatus.NEW, CopyStatus.REPLACED, CopyStatus.RENAMED)
        counts = (sum(e.status not in (CopyStatus.INVALID, CopyStatus.DUPLICATE) for e in entries),
                  sum(e.status in success for e in entries), sum(e.status == CopyStatus.MISSING for e in entries),
                  sum(e.status == CopyStatus.FAILED for e in entries))
        for variable, value in zip(self.metrics.values(), counts):
            variable.set(str(value) if data else "未確認")
        invalid = sum(e.status == CopyStatus.INVALID for e in entries)
        duplicates = sum(e.status == CopyStatus.DUPLICATE for e in entries)
        self.hint.set("有効なPDFファイル名を入力してください。" if data and not counts[0] else f"不正入力：{invalid}件　重複除外：{duplicates}件")
        if entries:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def ask_conflict(self, entry: PdfCopyEntry, index: int, count: int, output: Path) -> OutputConflictChoice:
        return self.view.dialogs.choose(f"同名PDFがあります（{index} / {count}）",
            f"対象PDF：{entry.name}\n既存の保存先：{output / entry.destination}",
            actions=(DialogAction("置換", result=OutputConflictChoice.REPLACE),
                     DialogAction("別名保存", result=OutputConflictChoice.SAVE_AS_NEW),
                     DialogAction("キャンセル", result=OutputConflictChoice.CANCEL, is_default=True)),
            cancel_result=OutputConflictChoice.CANCEL)

    def ask_commit(self, plan: PdfCopyPlan, decisions: tuple[PdfCopyDecision, ...]) -> bool:
        overwrite = sum(d.replace_existing for d in decisions)
        originals = {e.line: e.name for e in plan.entries}
        renamed = sum(not d.replace_existing and d.destination != originals[d.line] for d in decisions)
        excluded = [e for e in plan.entries if e.status not in (CopyStatus.READY, CopyStatus.CONFLICT)]
        details = '\n'.join(f"{status.value}：{sum(e.status == status for e in excluded)}件" for status in
                            (CopyStatus.MISSING, CopyStatus.INVALID, CopyStatus.FAILED, CopyStatus.DUPLICATE))
        return self.view.dialogs.ask("PDFをコピーしますか？",
            f"コピー先：{plan.output_dir}\n実行：{len(decisions)}件\n新規：{len(decisions)-overwrite-renamed}件　置換：{overwrite}件　別名：{renamed}件\n{details}",
            "コピー実行")
