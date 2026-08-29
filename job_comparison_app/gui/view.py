from __future__ import annotations

import os
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from job_comparison_report.service import ReportBuildPlan, ReportBuildRequest
from job_pdf_tool.service import PdfOrganizationPlan, PdfOrganizationRequest

from job_comparison_app import __version__

from .controller import Controller, ViewModel
from .dialogs import DialogKind, DialogPresenter
from .pdf_outputs import (
    PdfCsvConflict,
    PdfCsvConflictChoice,
    resolve_base_csv_paths,
)
from .visuals import (
    STYLE_BUTTON,
    STYLE_CARD_BODY,
    STYLE_CARD_CAPTION,
    STYLE_CARD_ERROR,
    STYLE_CARD_SUCCESS,
    STYLE_CARD_WARNING,
    STYLE_CHECK,
    STYLE_COMPACT_METRIC_VALUE,
    STYLE_ERROR,
    STYLE_FILE,
    STYLE_FOOTER,
    STYLE_FOOTER_RULE,
    STYLE_HEADER,
    STYLE_HEADER_CAPTION,
    STYLE_HEADER_RULE,
    STYLE_METRIC_ACCENT,
    STYLE_METRIC_LABEL,
    STYLE_METRIC_VALUE,
    STYLE_NOTEBOOK,
    STYLE_PRIMARY_BUTTON,
    STYLE_ROOT,
    STYLE_SECTION,
    STYLE_SECTION_ACCENT,
    STYLE_STATUS,
    STYLE_SUBTITLE,
    STYLE_SUCCESS,
    STYLE_TITLE,
    STYLE_TREE,
    STYLE_WARNING,
    VisualManager,
)


class WindowsOutputOpener:
    def open_folder(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_dir():
            raise OSError(f"フォルダが存在しません: {resolved}")
        if os.name == "nt":
            os.startfile(str(resolved))  # type: ignore[attr-defined]
            return
        raise OSError("この環境ではフォルダを開けません。")

    def open_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise OSError(f"ファイルが存在しません: {resolved}")
        if os.name == "nt":
            os.startfile(str(resolved))  # type: ignore[attr-defined]
            return
        raise OSError("この環境ではファイルを開けません。")


class TkView:
    PDF_PRIMARY = ("対象PDF", "CSV出力対象", "リネーム対象")
    PDF_SECONDARY = ("CSV除外", "変更なし", "成功", "部分成功", "改名保留", "エラー", "PDF未検出")
    PDF_STATS = PDF_PRIMARY + PDF_SECONDARY
    REPORT_PRIMARY = ("求人件数", "元PDF", "予定ページ")
    REPORT_SECONDARY = ("一致", "修正", "併記", "記載なし", "候補者情報未確認", "要判断", "エラー")
    REPORT_STATS = REPORT_PRIMARY + REPORT_SECONDARY

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller: Controller | None = None
        self._suppress_changes = False
        self._verified_auto = True
        self._verification_auto = True
        self._busy = False
        self._last_model: ViewModel | None = None
        self._input_widgets: list[tk.Widget] = []
        self._pdf_primary_cards = []
        self._report_primary_cards = []
        self._pdf_secondary_labels: dict[str, ttk.Label] = {}
        self._report_secondary_labels: dict[str, ttk.Label] = {}
        self._tab_canvases: list[tk.Canvas] = []
        self._tab_syncs: list[Callable[[], None]] = []
        self.visuals = VisualManager(root, on_change=self._visuals_changed)
        self.style = self.visuals.style
        self.dialogs = DialogPresenter(root, self.visuals)
        self._build_variables()
        self._build_layout()
        self._bind_variable_changes()
        self._configure_tree_tags()

    def bind_controller(self, controller: Controller) -> None:
        self.controller = controller
        self.root.protocol("WM_DELETE_WINDOW", controller.request_close)
        self.root.bind("<F1>", lambda event: self._shortcut_help())
        self.root.bind("<Control-Key-1>", lambda event: self._shortcut_tab(0))
        self.root.bind("<Control-Key-2>", lambda event: self._shortcut_tab(1))
        self.root.bind("<F5>", lambda event: self._shortcut_preview())

    def _build_variables(self) -> None:
        self.pdf_input = tk.StringVar()
        self.pdf_aliases = tk.StringVar()
        self.pdf_output_dir = tk.StringVar()
        self.pdf_output_summary = tk.StringVar(value="保存先は事前確認後に表示します。")
        self.report_input = tk.StringVar()
        self.report_manifest = tk.StringVar()
        self.report_output_pdf = tk.StringVar()
        self.report_verified_md = tk.StringVar()
        self.report_verification_csv = tk.StringVar()
        self.report_date = tk.StringVar()
        self.report_keep_html = tk.BooleanVar(value=False)
        self.report_show_details = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="入力を選択してください。")
        self._pdf_stat_vars = {
            name: tk.StringVar(value="未確認" if name in self.PDF_PRIMARY else "—")
            for name in self.PDF_STATS
        }
        self._report_stat_vars = {
            name: tk.StringVar(value="未確認" if name in self.REPORT_PRIMARY else "—")
            for name in self.REPORT_STATS
        }

    def _build_layout(self) -> None:
        self.root.title("求人比較ツール")
        self.root.geometry("960x700")
        self.root.minsize(760, 600)
        self.root.resizable(True, True)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self._build_menu()
        header = ttk.Frame(self.root, padding=(20, 12, 20, 0), style=STYLE_HEADER)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="求人比較ツール", style=STYLE_TITLE).grid(
            row=0, column=0, rowspan=2, sticky="w"
        )
        ttk.Label(
            header,
            text="求人PDFを整理し、10求人の比較資料PDFを生成します。",
            style=STYLE_SUBTITLE,
        ).grid(row=0, column=1, sticky="sw", padx=(16, 0))
        ttk.Label(
            header,
            text="外部サービスへ接続せず、ファイルはこのPC内で処理します。",
            style=STYLE_HEADER_CAPTION,
        ).grid(row=1, column=1, sticky="nw", padx=(16, 0))
        ttk.Frame(
            self.root,
            height=self.visuals.scaled_px(3),
            style=STYLE_HEADER_RULE,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 0))

        self.notebook = ttk.Notebook(self.root, style=STYLE_NOTEBOOK)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=20, pady=(4, 4))
        self.pdf_tab_shell, self.pdf_tab = self._scrollable_tab()
        self.report_tab_shell, self.report_tab = self._scrollable_tab()
        self.notebook.add(self.pdf_tab_shell, text="求人PDF整理")
        self.notebook.add(self.report_tab_shell, text="比較資料PDF生成")
        self._build_pdf_tab()
        self._build_report_tab()

        footer = ttk.Frame(self.root, padding=(20, 0, 20, 4), style=STYLE_FOOTER)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        ttk.Frame(
            footer,
            height=self.visuals.scaled_px(1),
            style=STYLE_FOOTER_RULE,
        ).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=160)
        self.progress.grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.progress.grid_remove()
        self.status_label = ttk.Label(footer, textvariable=self.status_text, style=STYLE_STATUS)
        self.status_label.grid(row=1, column=1, sticky="ew")
        self.help_button = ttk.Button(
            footer, text="使い方", style=STYLE_BUTTON, command=self.show_help
        )
        self.help_button.grid(row=1, column=2, padx=(8, 0))
        self.close_button = ttk.Button(
            footer,
            text="終了",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.request_close(),
        )
        self.close_button.grid(row=1, column=3, padx=(8, 0))
        self.root.bind("<FocusIn>", self.visuals.schedule_refresh, add="+")
        self.root.update_idletasks()
        self._sync_tab_layouts()

    def _scrollable_tab(self) -> tuple[ttk.Frame, ttk.Frame]:
        shell = ttk.Frame(self.notebook, style=STYLE_ROOT)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            shell,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
            background=self.visuals.palette.app_background,
        )
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(canvas, padding=8, style=STYLE_ROOT)
        window = canvas.create_window((0, 0), anchor="nw", window=content)

        def sync(event: tk.Event | None = None) -> None:
            del event
            width = max(1, canvas.winfo_width())
            height = max(canvas.winfo_height(), content.winfo_reqheight())
            canvas.itemconfigure(window, width=width, height=height)
            canvas.configure(scrollregion=(0, 0, width, height))
            if content.winfo_reqheight() > canvas.winfo_height():
                scrollbar.grid()
            else:
                scrollbar.grid_remove()

        def sync_after_layout(event: tk.Event | None = None) -> None:
            del event
            try:
                self.root.after_idle(sync)
            except tk.TclError:
                return

        content.bind("<Configure>", sync_after_layout, add="+")
        canvas.bind("<Configure>", sync_after_layout, add="+")
        # The tab contents are populated after this wrapper is created.  Their
        # requested size does not necessarily emit another <Configure> event
        # while the canvas window is constrained, so perform one final idle
        # synchronization after the complete tab has been built.
        self._tab_canvases.append(canvas)
        self._tab_syncs.append(sync)
        return shell, content

    def _sync_tab_layouts(self) -> None:
        for sync in self._tab_syncs:
            sync()

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root)
        self.file_menu = tk.Menu(self.menu, tearoff=False)
        self.file_menu.add_command(
            label="終了", command=lambda: self.controller and self.controller.request_close()
        )
        self.help_menu = tk.Menu(self.menu, tearoff=False)
        self.help_menu.add_command(label="使い方", command=self.show_help)
        self.help_menu.add_command(label="バージョン情報", command=self.show_version)
        self.menu.add_cascade(label="ファイル", menu=self.file_menu)
        self.menu.add_cascade(label="ヘルプ", menu=self.help_menu)
        self.root.configure(menu=self.menu)

    def _build_pdf_tab(self) -> None:
        self.pdf_tab.columnconfigure(0, weight=1)
        self.pdf_tab.rowconfigure(2, weight=1)
        self._pdf_primary_cards = self._build_primary_metrics(
            self.pdf_tab, self.PDF_PRIMARY, self._pdf_stat_vars
        )

        card = self.visuals.create_card(self.pdf_tab, padding=8)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        form = card.body
        form.columnconfigure(1, weight=1)
        self._section_heading(
            form,
            "1. 入力と出力",
            "整理するPDFフォルダーと、必要に応じて設定・出力先を指定します。",
        )
        fields = ttk.Frame(form, style=STYLE_CARD_BODY)
        fields.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        fields.columnconfigure(0, weight=1, uniform="pdf_fields")
        fields.columnconfigure(1, weight=1, uniform="pdf_fields")
        self._path_field(fields, 0, 0, "PDFフォルダ（必須）", self.pdf_input, self._choose_pdf_folder)
        self._path_field(fields, 0, 1, "見出し別名JSON", self.pdf_aliases, self._choose_pdf_aliases)
        self._path_field(
            fields,
            1,
            0,
            "CSV出力フォルダー（任意）",
            self.pdf_output_dir,
            self._choose_pdf_output_dir,
            columnspan=2,
        )
        ttk.Label(
            form,
            text="空欄の場合は、PDFフォルダー内の「output」に求人情報.csvと処理レポート.csvを保存します。",
            style=STYLE_CARD_CAPTION,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(3, 0))

        result_card = self.visuals.create_card(self.pdf_tab, padding=8)
        result_card.grid(row=2, column=0, sticky="nsew")
        result = result_card.body
        result.columnconfigure(0, weight=1)
        result.rowconfigure(4, weight=1)
        self._section_heading(
            result,
            "2. 事前確認と実行",
            "事前確認ではPDF名やCSVを変更しません。内容を確認してから本実行してください。",
        )
        actions = ttk.Frame(result, style=STYLE_CARD_BODY)
        actions.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        actions.columnconfigure(1, weight=1)
        left = ttk.Frame(actions, style=STYLE_CARD_BODY)
        left.grid(row=0, column=0, sticky="w")
        right = ttk.Frame(actions, style=STYLE_CARD_BODY)
        right.grid(row=0, column=2, sticky="e")
        self.pdf_preview_button = ttk.Button(
            left,
            text="事前確認",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.preview_pdf(),
        )
        self.pdf_preview_button.grid(row=0, column=0)
        self.pdf_apply_button = ttk.Button(
            left,
            text="本実行",
            style=STYLE_PRIMARY_BUTTON,
            command=lambda: self.controller and self.controller.apply_pdf(),
        )
        self.pdf_apply_button.grid(row=0, column=1, padx=(8, 0))
        self.pdf_open_button = ttk.Button(
            right,
            text="出力フォルダーを開く",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.open_pdf_output_folder(),
        )
        self.pdf_open_button.grid(row=0, column=0)
        self._pdf_secondary_labels = self._build_secondary_metrics(
            result, row=2, names=self.PDF_SECONDARY, variables=self._pdf_stat_vars
        )
        ttk.Label(
            result,
            textvariable=self.pdf_output_summary,
            style=STYLE_CARD_CAPTION,
            justify="left",
            wraplength=820,
        ).grid(row=3, column=0, sticky="ew", pady=(0, 5))
        self.pdf_tree, self.pdf_empty = self._build_tree(
            result,
            row=4,
            columns=("status", "old", "new", "company", "position", "csv", "message"),
            headings=("状態", "元ファイル名", "変更後ファイル名", "会社名", "募集ポジション", "CSV", "警告・除外理由"),
            widths=(80, 180, 190, 140, 170, 55, 260),
            empty_text="PDFフォルダーを選択し、「事前確認」を実行すると対象一覧が表示されます。",
        )

    def _build_report_tab(self) -> None:
        self.report_tab.columnconfigure(0, weight=1)
        self.report_tab.rowconfigure(2, weight=1)
        self._report_primary_cards = self._build_primary_metrics(
            self.report_tab, self.REPORT_PRIMARY, self._report_stat_vars
        )

        card = self.visuals.create_card(self.report_tab, padding=8)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        form = card.body
        form.columnconfigure(1, weight=1)
        self._section_heading(
            form,
            "1. 入力と出力",
            "比較Markdown、元PDF対応JSON、生成するPDFの保存先を指定します。",
        )
        fields = ttk.Frame(form, style=STYLE_CARD_BODY)
        fields.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        fields.columnconfigure(0, weight=1, uniform="report_fields")
        fields.columnconfigure(1, weight=1, uniform="report_fields")
        self._path_field(fields, 0, 0, "比較Markdown（必須）", self.report_input, self._choose_report_input)
        self._path_field(fields, 0, 1, "元PDF対応JSON（必須）", self.report_manifest, self._choose_report_manifest)
        self._path_field(fields, 1, 0, "出力PDF（必須）", self.report_output_pdf, self._choose_report_output, "保存先…", columnspan=2)
        ttk.Label(form, text="表紙日付", style=STYLE_FILE).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        options = ttk.Frame(form, style=STYLE_CARD_BODY)
        options.grid(row=2, column=1, columnspan=2, sticky="ew", pady=2)
        options.columnconfigure(1, weight=1)
        date_entry = ttk.Entry(options, textvariable=self.report_date, width=16)
        date_entry.grid(row=0, column=0, sticky="w")
        keep = ttk.Checkbutton(
            options,
            text="中間HTMLを保存",
            variable=self.report_keep_html,
            style=STYLE_CHECK,
        )
        keep.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self._input_widgets.extend([date_entry, keep])
        details_toggle = ttk.Checkbutton(
            form,
            text="詳細な出力先を表示",
            variable=self.report_show_details,
            command=self._toggle_report_details,
            style=STYLE_CHECK,
        )
        details_toggle.grid(row=3, column=1, sticky="w", pady=(3, 0))
        self._input_widgets.append(details_toggle)
        self.report_details = ttk.Frame(form, style=STYLE_CARD_BODY)
        self.report_details.columnconfigure(1, weight=1)
        self._path_row(self.report_details, 0, "検証済みMarkdown", self.report_verified_md, self._choose_verified_md, "保存先…")
        self._path_row(self.report_details, 1, "検証結果CSV", self.report_verification_csv, self._choose_verification_csv, "保存先…")

        result_card = self.visuals.create_card(self.report_tab, padding=8)
        result_card.grid(row=2, column=0, sticky="nsew")
        result = result_card.body
        result.columnconfigure(0, weight=1)
        result.rowconfigure(3, weight=1)
        self._section_heading(
            result,
            "2. 事前確認と実行",
            "10求人と元PDFの照合結果を確認してから比較資料PDFを生成してください。",
        )
        actions = ttk.Frame(result, style=STYLE_CARD_BODY)
        actions.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        actions.columnconfigure(1, weight=1)
        left = ttk.Frame(actions, style=STYLE_CARD_BODY)
        left.grid(row=0, column=0, sticky="w")
        right = ttk.Frame(actions, style=STYLE_CARD_BODY)
        right.grid(row=0, column=2, sticky="e")
        self.report_preview_button = ttk.Button(
            left,
            text="事前確認",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.preview_report(),
        )
        self.report_preview_button.grid(row=0, column=0)
        self.report_apply_button = ttk.Button(
            left,
            text="PDFを生成",
            style=STYLE_PRIMARY_BUTTON,
            command=lambda: self.controller and self.controller.build_report(),
        )
        self.report_apply_button.grid(row=0, column=1, padx=(8, 0))
        self.report_open_pdf_button = ttk.Button(
            right,
            text="PDFを開く",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.open_report_pdf(),
        )
        self.report_open_pdf_button.grid(row=0, column=0)
        self.report_open_folder_button = ttk.Button(
            right,
            text="出力フォルダーを開く",
            style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.open_report_output_folder(),
        )
        self.report_open_folder_button.grid(row=0, column=1, padx=(8, 0))
        self._report_secondary_labels = self._build_secondary_metrics(
            result, row=2, names=self.REPORT_SECONDARY, variables=self._report_stat_vars
        )
        self.report_tree, self.report_empty = self._build_tree(
            result,
            row=3,
            columns=("rank", "company", "position", "field", "verdict", "value", "message", "source"),
            headings=("順位", "会社名", "募集ポジション", "対象項目", "判定", "最終採用値", "メッセージ", "元PDF"),
            widths=(45, 150, 170, 100, 85, 180, 250, 170),
            empty_text="比較Markdownと元PDF対応JSONを選択し、「事前確認」を実行すると照合結果が表示されます。",
        )

    def _build_primary_metrics(self, parent, names, variables):
        row = ttk.Frame(parent, style=STYLE_ROOT)
        row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        cards = []
        for column, name in enumerate(names):
            row.columnconfigure(column, weight=1, uniform="primary_metrics")
            card = self.visuals.create_card(row, padding=8)
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 6, 0 if column == 2 else 6),
            )
            accent = ttk.Frame(
                card.body,
                width=self.visuals.scaled_px(28),
                height=self.visuals.scaled_px(3),
                style=STYLE_METRIC_ACCENT,
            )
            accent.grid(row=0, column=0, sticky="w", pady=(0, self.visuals.scaled_px(4)))
            ttk.Label(card.body, text=name, style=STYLE_METRIC_LABEL).grid(row=1, column=0, sticky="w")
            ttk.Label(card.body, textvariable=variables[name], style=STYLE_METRIC_VALUE).grid(
                row=2, column=0, sticky="w", pady=(2, 0)
            )
            cards.append(card)
        return cards

    def _build_secondary_metrics(self, parent, *, row: int, names, variables):
        grid = ttk.Frame(parent, style=STYLE_CARD_BODY)
        grid.grid(row=row, column=0, sticky="ew", pady=(0, 5))
        for column in range(7):
            grid.columnconfigure(column, weight=1, uniform="secondary_metrics")
        labels: dict[str, ttk.Label] = {}
        for index, name in enumerate(names):
            cell = ttk.Frame(grid, style=STYLE_CARD_BODY, padding=(5, 1))
            cell.grid(row=0, column=index, sticky="ew")
            ttk.Label(cell, text=name, style=STYLE_METRIC_LABEL).grid(row=0, column=0, sticky="w")
            value = ttk.Label(cell, textvariable=variables[name], style=STYLE_COMPACT_METRIC_VALUE)
            value.grid(row=1, column=0, sticky="w")
            labels[name] = value
        return labels

    def _section_heading(self, parent, title: str, caption: str) -> None:
        header = ttk.Frame(parent, style=STYLE_CARD_BODY)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.columnconfigure(1, weight=1)
        accent = ttk.Frame(
            header,
            width=self.visuals.scaled_px(4),
            height=self.visuals.scaled_px(18),
            style=STYLE_SECTION_ACCENT,
        )
        accent.grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=title, style=STYLE_SECTION).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(self.visuals.scaled_px(12), 0),
        )
        ttk.Label(header, text=caption, style=STYLE_CARD_CAPTION).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )

    def _path_row(self, parent, row: int, label: str, variable: tk.StringVar, command, button_text="選択…") -> ttk.Entry:
        ttk.Label(parent, text=label, style=STYLE_FILE).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        button = ttk.Button(parent, text=button_text, command=command, style=STYLE_BUTTON)
        button.grid(row=row, column=2, padx=(8, 0), pady=2)
        self._input_widgets.extend([entry, button])
        return entry

    def _path_field(
        self,
        parent,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        command,
        button_text: str = "選択…",
        *,
        columnspan: int = 1,
    ) -> ttk.Entry:
        field = ttk.Frame(parent, style=STYLE_CARD_BODY, padding=(0, 1))
        field.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=(0 if column == 0 else 5, 5 if column == 0 and columnspan == 1 else 0),
        )
        field.columnconfigure(0, weight=1)
        ttk.Label(field, text=label, style=STYLE_FILE).grid(row=0, column=0, columnspan=2, sticky="w")
        entry = ttk.Entry(field, textvariable=variable)
        entry.grid(row=1, column=0, sticky="ew", pady=(1, 0))
        button = ttk.Button(field, text=button_text, command=command, style=STYLE_BUTTON)
        button.grid(row=1, column=1, padx=(5, 0), pady=(1, 0))
        self._input_widgets.extend([entry, button])
        return entry

    def _build_tree(self, parent, *, row, columns, headings, widths, empty_text):
        container = ttk.Frame(parent, style=STYLE_CARD_BODY)
        container.grid(row=row, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        tree = ttk.Treeview(container, columns=columns, show="headings", height=4, style=STYLE_TREE)
        for column, heading, width in zip(columns, headings, widths, strict=True):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=45, stretch=True)
        vertical = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        empty = ttk.Label(
            container,
            text=empty_text,
            style=STYLE_CARD_CAPTION,
            anchor="center",
            justify="center",
            wraplength=560,
        )
        empty.place(relx=0.5, rely=0.5, anchor="center")
        empty.lift()
        return tree, empty

    def _bind_variable_changes(self) -> None:
        for variable in (self.pdf_input, self.pdf_aliases, self.pdf_output_dir):
            variable.trace_add("write", lambda *args: self._changed_pdf())
        for variable in (self.report_input, self.report_manifest, self.report_date, self.report_keep_html):
            variable.trace_add("write", lambda *args: self._changed_report())
        self.report_output_pdf.trace_add("write", lambda *args: self._report_output_changed())
        self.report_verified_md.trace_add("write", lambda *args: self._verified_output_changed())
        self.report_verification_csv.trace_add("write", lambda *args: self._verification_output_changed())

    def _changed_pdf(self) -> None:
        if not self._suppress_changes and self.controller:
            self.controller.invalidate_pdf()

    def _changed_report(self) -> None:
        if not self._suppress_changes and self.controller:
            self.controller.invalidate_report()

    def _report_output_changed(self) -> None:
        if self._suppress_changes:
            return
        raw = self.report_output_pdf.get().strip()
        if raw:
            output = Path(raw)
            self._suppress_changes = True
            try:
                if self._verified_auto:
                    self.report_verified_md.set(str(output.with_name(f"{output.stem}_検証済み.md")))
                if self._verification_auto:
                    self.report_verification_csv.set(str(output.with_name(f"{output.stem}_検証結果.csv")))
            finally:
                self._suppress_changes = False
        if self.controller:
            self.controller.invalidate_report()

    def _verified_output_changed(self) -> None:
        if self._suppress_changes:
            return
        self._verified_auto = False
        if self.controller:
            self.controller.invalidate_report()

    def _verification_output_changed(self) -> None:
        if self._suppress_changes:
            return
        self._verification_auto = False
        if self.controller:
            self.controller.invalidate_report()

    def _set_variables(self, callback) -> None:
        self._suppress_changes = True
        try:
            callback()
        finally:
            self._suppress_changes = False
        if self.controller:
            self.controller.invalidate_report()

    def _choose_pdf_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="求人PDFがあるフォルダーを選択")
        if selected:
            self.pdf_input.set(selected)

    def _choose_pdf_aliases(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="見出し別名JSONを選択", filetypes=(("JSON", "*.json"),))
        if selected:
            self.pdf_aliases.set(selected)

    def _choose_pdf_output_dir(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="CSV出力フォルダーを選択")
        if selected:
            self.pdf_output_dir.set(selected)

    def _choose_report_input(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="比較Markdownを選択", filetypes=(("Markdown", "*.md"),))
        if not selected:
            return
        source = Path(selected)
        stem = re.sub(r"^#\s*", "", source.stem).strip() or "求人比較資料"

        def update() -> None:
            self.report_input.set(str(source))
            output = source.with_name(f"{stem}.pdf")
            self.report_output_pdf.set(str(output))
            if self._verified_auto:
                self.report_verified_md.set(str(output.with_name(f"{output.stem}_検証済み.md")))
            if self._verification_auto:
                self.report_verification_csv.set(str(output.with_name(f"{output.stem}_検証結果.csv")))

        self._set_variables(update)

    def _choose_report_manifest(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="元PDF対応JSONを選択", filetypes=(("JSON", "*.json"),))
        if selected:
            self.report_manifest.set(selected)

    def _choose_report_output(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self.root, title="比較資料PDFの保存先", defaultextension=".pdf", filetypes=(("PDF", "*.pdf"),))
        if not selected:
            return
        output = Path(selected)

        def update() -> None:
            self.report_output_pdf.set(str(output))
            if self._verified_auto:
                self.report_verified_md.set(str(output.with_name(f"{output.stem}_検証済み.md")))
            if self._verification_auto:
                self.report_verification_csv.set(str(output.with_name(f"{output.stem}_検証結果.csv")))

        self._set_variables(update)

    def _choose_verified_md(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self.root, title="検証済みMarkdownの保存先", defaultextension=".md", filetypes=(("Markdown", "*.md"),))
        if selected:
            self._verified_auto = False
            self.report_verified_md.set(selected)

    def _choose_verification_csv(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self.root, title="検証結果CSVの保存先", defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if selected:
            self._verification_auto = False
            self.report_verification_csv.set(selected)

    def _toggle_report_details(self) -> None:
        if self.report_show_details.get():
            self.report_details.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        else:
            self.report_details.grid_remove()

    def _shortcut_tab(self, index: int) -> str:
        if not self._busy:
            self.notebook.select(index)
        return "break"

    def _shortcut_preview(self) -> str:
        if not self._busy:
            self._preview_active()
        return "break"

    def _shortcut_help(self) -> str:
        if not self._busy:
            self.show_help()
        return "break"

    def _preview_active(self) -> None:
        if not self.controller:
            return
        if self.notebook.index(self.notebook.select()) == 0:
            self.controller.preview_pdf()
        else:
            self.controller.preview_report()

    def current_pdf_request(self) -> PdfOrganizationRequest:
        input_dir = Path(self.pdf_input.get())
        raw_output_dir = self.pdf_output_dir.get().strip()
        paths = resolve_base_csv_paths(
            input_dir,
            Path(raw_output_dir) if raw_output_dir else None,
        )
        return PdfOrganizationRequest(
            input_dir=input_dir,
            aliases_path=Path(self.pdf_aliases.get()) if self.pdf_aliases.get().strip() else None,
            output_csv=paths.output_csv,
            report_csv=paths.report_csv,
        )

    def ask_pdf_csv_conflict(self, conflict: PdfCsvConflict) -> PdfCsvConflictChoice:
        suggested = conflict.suggested_paths
        existing = "\n".join(f"- {path.name}" for path in conflict.existing_paths)
        suggestion = (
            f"{suggested.output_csv.name}\n{suggested.report_csv.name}"
            if suggested is not None
            else "—"
        )
        message = (
            f"出力フォルダー:\n{conflict.base_paths.output_dir}\n\n"
            f"既に存在するCSV:\n{existing}\n\n"
            "置換する:\n"
            "求人情報CSVは追加列とPDF未検出行を保持して更新し、"
            "処理レポートCSVは今回の結果で再生成します。\n\n"
            f"別名で保存する候補:\n{suggestion}\n"
            "既存CSVは変更しません。"
        )
        return self.dialogs.ask_pdf_csv_conflict("既存のCSVがあります", message)

    def current_report_request(self) -> ReportBuildRequest:
        return ReportBuildRequest(
            input_markdown=Path(self.report_input.get()),
            source_manifest=Path(self.report_manifest.get()),
            output_pdf=Path(self.report_output_pdf.get()),
            verified_markdown=Path(self.report_verified_md.get()),
            verification_csv=Path(self.report_verification_csv.get()),
            report_date=self.report_date.get().strip() or None,
            keep_html=self.report_keep_html.get(),
        )

    def ask_pdf_commit(self, plan: PdfOrganizationPlan) -> bool:
        summary = plan.summary
        overwrite = [path.name for path in (plan.output_csv, plan.report_csv) if path.exists()]
        output_mode = self._last_model.pdf_output_mode if self._last_model else None
        mode_note = {
            "新規保存": "新しいCSVを作成します。",
            "既存CSVを更新": "求人情報CSVの追加列とPDF未検出行を保持し、処理レポートCSVは再生成します。",
            "別名で保存": "既存CSVを変更せず、別名のCSVを新規作成します。",
        }.get(output_mode or "", "")
        message = (
            f"対象PDF: {len(plan.results)}件\n"
            f"リネーム: {summary['リネーム対象']}件\n"
            f"CSV出力: {summary['CSV出力対象']}件\n"
            f"CSV除外: {summary['CSV除外']}件\n"
            f"PDF未検出の既存CSV行: {len(plan.table.stale_rows)}件\n\n"
            f"保存方法: {output_mode or '新規保存'}\n"
            f"出力フォルダー: {plan.output_csv.parent}\n"
            f"求人情報CSV: {plan.output_csv.name}\n"
            f"処理レポートCSV: {plan.report_csv.name}\n"
            f"上書き対象: {', '.join(overwrite) if overwrite else 'なし'}\n"
            f"{mode_note}\n\n"
            "除外されたPDFは移動・削除しません。PDF名とCSVを確定しますか？"
        )
        return self.dialogs.ask("求人PDF整理を実行しますか？", message, "実行")

    def ask_report_commit(self, plan: ReportBuildPlan) -> bool:
        outputs = [path.name for path in plan.target_paths]
        overwrite = [path.name for path in plan.target_paths if path.exists()]
        counts = " / ".join(
            f"{name}: {plan.verdict_counts.get(name, 0)}"
            for name in ("一致", "修正", "併記", "記載なし", "候補者情報未確認")
        )
        message = (
            f"求人: {len(plan.document.jobs)}件 / 元PDF: {len(plan.document.jobs)}件\n"
            f"{counts}\n"
            f"警告: {plan.warning_count}件\n\n"
            "生成するファイル:\n- " + "\n- ".join(outputs) + "\n\n"
            f"上書き対象: {', '.join(overwrite) if overwrite else 'なし'}\n\n"
            "A4縦12ページとして生成・検証しますか？"
        )
        return self.dialogs.ask("比較資料PDFを生成しますか？", message, "生成")

    def show_info(self, title: str, message: str) -> None:
        self.dialogs.show(title, message, DialogKind.SUCCESS)

    def show_warning(self, title: str, message: str) -> None:
        self.dialogs.show(title, message, DialogKind.WARNING)

    def show_error(self, title: str, message: str) -> None:
        self.dialogs.show(title, message, DialogKind.ERROR)

    def show_help(self) -> None:
        if not self._busy:
            self.dialogs.show_help("求人比較ツールの使い方")

    def show_version(self) -> None:
        if not self._busy:
            self.dialogs.show("バージョン情報", f"求人比較ツール {__version__}", DialogKind.INFO)

    def render(self, model: ViewModel) -> None:
        self._last_model = model
        self._busy = model.busy
        self.status_text.set(model.status)
        self.status_label.configure(
            style={"success": STYLE_SUCCESS, "warning": STYLE_WARNING, "error": STYLE_ERROR}.get(
                model.tone, STYLE_STATUS
            )
        )
        if model.busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)
            self.progress.grid_remove()

        pdf_ready = bool(self.pdf_input.get().strip() and Path(self.pdf_input.get()).is_dir())
        report_ready = bool(
            self.report_input.get().strip()
            and Path(self.report_input.get()).is_file()
            and self.report_manifest.get().strip()
            and Path(self.report_manifest.get()).is_file()
            and self.report_output_pdf.get().strip()
            and self.report_verified_md.get().strip()
            and self.report_verification_csv.get().strip()
        )
        self.pdf_preview_button.configure(state="normal" if pdf_ready and not model.busy else "disabled")
        self.pdf_apply_button.configure(state="normal" if pdf_ready and not model.busy else "disabled")
        self.pdf_open_button.configure(state="normal" if model.pdf_result and not model.busy else "disabled")
        self.report_preview_button.configure(state="normal" if report_ready and not model.busy else "disabled")
        self.report_apply_button.configure(state="normal" if report_ready and not model.busy else "disabled")
        self.report_open_pdf_button.configure(state="normal" if model.report_result and not model.busy else "disabled")
        self.report_open_folder_button.configure(state="normal" if model.report_result and not model.busy else "disabled")
        self.help_button.configure(state="disabled" if model.busy else "normal")
        self.help_menu.entryconfigure(0, state="disabled" if model.busy else "normal")
        self.help_menu.entryconfigure(1, state="disabled" if model.busy else "normal")
        for widget in self._input_widgets:
            try:
                widget.configure(state="disabled" if model.busy else "normal")
            except tk.TclError:
                pass
        for index in range(2):
            self.notebook.tab(index, state="disabled" if model.busy else "normal")
        self._render_pdf_plan(model.pdf_plan, model.pdf_output_mode)
        self._render_report_plan(model.report_plan)

    def _render_pdf_plan(self, plan: PdfOrganizationPlan | None, output_mode: str | None = None) -> None:
        self._clear_tree(self.pdf_tree)
        if plan is None:
            self._reset_metrics(self.PDF_PRIMARY, self.PDF_SECONDARY, self._pdf_stat_vars)
            self.pdf_empty.place(relx=0.5, rely=0.5, anchor="center")
            self.pdf_empty.lift()
            self._apply_secondary_tones(self._pdf_secondary_labels, {})
            self.pdf_output_summary.set("保存先は事前確認後に表示します。")
            return
        values = {name: 0 for name in self.PDF_STATS}
        values.update(plan.summary)
        values["対象PDF"] = len(plan.results)
        values["PDF未検出"] = len(plan.table.stale_rows)
        self._set_metrics(values, self._pdf_stat_vars)
        self.pdf_empty.place_forget()
        self.pdf_output_summary.set(
            f"保存方法: {output_mode or '新規保存'}　保存先: {plan.output_csv.parent}\n"
            f"求人情報CSV: {plan.output_csv.name}　処理レポートCSV: {plan.report_csv.name}"
        )
        for result in plan.results:
            messages = [result.error] if result.error else []
            messages.extend(issue.message for issue in result.required_issues)
            if not messages:
                messages.extend(
                    report.message
                    for report in result.reports
                    if report.classification in {"warning", "conflict", "error"}
                )
            tone = "warning" if result.status in {"部分成功", "改名保留"} else "error" if result.status == "エラー" else "normal"
            self.pdf_tree.insert(
                "",
                "end",
                values=(
                    result.status,
                    result.source_path.name,
                    result.proposed_name or "—",
                    result.company.value,
                    result.resolved_position or result.raw_position.value,
                    "対象" if result.is_main_csv_eligible else "除外",
                    " / ".join(messages[:3]),
                ),
                tags=(tone,),
            )
        tones = {"部分成功": "warning", "改名保留": "warning", "エラー": "error", "CSV除外": "warning", "PDF未検出": "warning"}
        self._apply_secondary_tones(self._pdf_secondary_labels, values, tones)

    def _render_report_plan(self, plan: ReportBuildPlan | None) -> None:
        self._clear_tree(self.report_tree)
        if plan is None:
            self._reset_metrics(self.REPORT_PRIMARY, self.REPORT_SECONDARY, self._report_stat_vars)
            self.report_empty.place(relx=0.5, rely=0.5, anchor="center")
            self.report_empty.lift()
            self._apply_secondary_tones(self._report_secondary_labels, {})
            return
        values = {name: 0 for name in self.REPORT_STATS}
        values.update(plan.verdict_counts)
        values["求人件数"] = len(plan.document.jobs)
        values["元PDF"] = len(plan.document.jobs)
        values["予定ページ"] = 12
        self._set_metrics(values, self._report_stat_vars)
        self.report_empty.place_forget()
        for finding in plan.findings:
            tone = (
                "warning"
                if finding.verdict in {"修正", "併記", "記載なし", "候補者情報未確認"}
                else "error"
                if finding.verdict in {"要判断", "エラー"}
                else "normal"
            )
            self.report_tree.insert(
                "",
                "end",
                values=(
                    finding.rank,
                    finding.company,
                    finding.position,
                    finding.field,
                    finding.verdict,
                    finding.final_value,
                    finding.message,
                    Path(finding.source_pdf).name,
                ),
                tags=(tone,),
            )
        tones = {"修正": "warning", "併記": "warning", "記載なし": "warning", "候補者情報未確認": "warning", "要判断": "error", "エラー": "error"}
        self._apply_secondary_tones(self._report_secondary_labels, values, tones)

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    @staticmethod
    def _reset_metrics(primary, secondary, variables) -> None:
        for name in primary:
            variables[name].set("未確認")
        for name in secondary:
            variables[name].set("—")

    @staticmethod
    def _set_metrics(values, variables) -> None:
        for name, variable in variables.items():
            variable.set(str(values.get(name, 0)))

    def _apply_secondary_tones(self, labels, values, tones=None) -> None:
        tones = tones or {}
        for name, label in labels.items():
            raw = values.get(name, 0)
            positive = isinstance(raw, int) and raw > 0
            tone = tones.get(name, "normal") if positive else "normal"
            style = {"success": STYLE_CARD_SUCCESS, "warning": STYLE_CARD_WARNING, "error": STYLE_CARD_ERROR}.get(
                tone, STYLE_COMPACT_METRIC_VALUE
            )
            label.configure(style=style)

    def _configure_tree_tags(self) -> None:
        for tree in (getattr(self, "pdf_tree", None), getattr(self, "report_tree", None)):
            if tree is None:
                continue
            tree.tag_configure("normal", foreground=self.visuals.palette.text)
            tree.tag_configure("warning", foreground=self.visuals.tone_color("warning"))
            tree.tag_configure("error", foreground=self.visuals.tone_color("error"))

    def _visuals_changed(self) -> None:
        for canvas in self._tab_canvases:
            canvas.configure(background=self.visuals.palette.app_background)
        if hasattr(self, "dialogs"):
            self.dialogs.refresh_visuals()
        self._configure_tree_tags()
        if self._last_model is not None:
            self.status_label.configure(
                style={"success": STYLE_SUCCESS, "warning": STYLE_WARNING, "error": STYLE_ERROR}.get(
                    self._last_model.tone, STYLE_STATUS
                )
            )

    def post(self, callback) -> None:
        self.root.after(0, callback)

    def close(self) -> None:
        self.root.destroy()


__all__ = ["DialogPresenter", "TkView", "WindowsOutputOpener"]
