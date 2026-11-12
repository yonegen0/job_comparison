from __future__ import annotations

import os
import re
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from job_comparison_report.service import ReportBuildPlan, ReportBuildRequest
from job_pdf_tool.service import PdfOrganizationPlan, PdfOrganizationRequest
from pdf_job_exporter.service import PdfTextExportPlan, PdfTextExportRequest
from recruit_job_tool.candidate_profile import _CandidateProfileFieldError
from recruit_job_tool.gui.controller import CandidateProfileDecision
from recruit_job_tool.models import AnalysisOutputMode

from job_comparison_app import __version__

from .candidate_conditions import CandidateConditionsPanel
from .pdf_copy_panel import PdfCopyPanel
from .controller import Controller, ViewModel
from .dispatcher import MainThreadDispatcher
from .dialogs import DialogAction, DialogKind, DialogPresenter
from .export_outputs import ExportOutputConflict, resolve_export_output
from .pdf_outputs import (
    LegacyCsvMigrationChoice, OrganizationOutputConflict, OutputConflictChoice,
    resolve_base_output_paths,
)
from .recruit_feature import RecruitFeaturePanel
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


TAB_CANDIDATE = 0
TAB_RECRUIT = 1
TAB_PDF = 2
TAB_EXPORT = 3
TAB_COPY = 4
TAB_REPORT = 5
PDF_PROVIDERS = ("jobins", "circus")


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
    PDF_PRIMARY = ("対象PDF", "JSON出力対象", "リネーム対象")
    PDF_SECONDARY = ("JSON除外", "変更なし", "成功", "部分成功", "改名保留", "エラー", "PDF未検出")
    PDF_STATS = PDF_PRIMARY + PDF_SECONDARY
    EXPORT_PRIMARY = ("対象PDF", "ページ", "抽出文字")
    EXPORT_SECONDARY = ("警告", "テキストなしページ", "NUL置換", "出力サイズ")
    EXPORT_STATS = EXPORT_PRIMARY + EXPORT_SECONDARY
    REPORT_PRIMARY = ("求人件数", "元PDF", "予定ページ")
    REPORT_SECONDARY = ("一致", "修正", "併記", "記載なし", "候補者情報未確認", "要判断", "エラー")
    REPORT_STATS = REPORT_PRIMARY + REPORT_SECONDARY

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._dispatcher = MainThreadDispatcher(root)
        self.controller: Controller | None = None
        self._suppress_changes = False
        self._verified_auto = True
        self._verification_auto = True
        self._busy = False
        self._last_model: ViewModel | None = None
        self._input_widgets: list[tk.Widget] = []
        self.pdf_widgets: dict[str, dict] = {provider: {} for provider in PDF_PROVIDERS}
        self._selected_pdf_provider = "jobins"
        self._changing_pdf_programmatically = False
        self._report_primary_cards = []
        self._export_primary_cards = []
        self._report_secondary_labels: dict[str, ttk.Label] = {}
        self._export_secondary_labels: dict[str, ttk.Label] = {}
        self._tab_canvases: list[tk.Canvas] = []
        self._tab_syncs: list[Callable[[], None]] = []
        self._selected_tab_index = 0
        self._changing_tab_programmatically = False
        self.visuals = VisualManager(root, on_change=self._visuals_changed)
        self.style = self.visuals.style
        self.style.configure("App.TNotebook.Tab", padding=(10, 8))
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
        self.root.bind("<Control-Key-3>", lambda event: self._shortcut_tab(2))
        self.root.bind("<Control-Key-4>", lambda event: self._shortcut_tab(3))
        self.root.bind("<Control-Key-5>", lambda event: self._shortcut_tab(4))
        self.root.bind("<Control-Key-6>", lambda event: self._shortcut_tab(5))
        self.root.bind("<Alt-j>", lambda event: self._shortcut_recruit("select_jobins"))
        self.root.bind("<Alt-c>", lambda event: self._shortcut_recruit("select_careerbank"))
        self.root.bind("<Control-o>", lambda event: self._shortcut_recruit("choose_html"))
        self.root.bind("<Control-i>", lambda event: self._shortcut_recruit("import_selected"))
        self.root.bind("<Control-e>", lambda event: self._shortcut_recruit("export_csv"))
        self.root.bind("<Control-r>", lambda event: self._shortcut_recruit("restore"))
        self.root.bind("<Control-g>", lambda event: self._shortcut_recruit("generate_analysis"))
        self.root.bind(
            "<Control-Shift-O>",
            lambda event: self._shortcut_recruit("open_output_folder"),
        )
        self.root.bind("<F5>", lambda event: self._shortcut_preview())

    def _build_variables(self) -> None:
        self.pdf_inputs = {
            provider: {
                "input_dir": tk.StringVar(),
                "aliases": tk.StringVar(),
                "output_dir": tk.StringVar(),
                "output_mode": tk.StringVar(value=AnalysisOutputMode.JSON_WITH_GUIDE.value),
            }
            for provider in PDF_PROVIDERS
        }
        for provider in PDF_PROVIDERS:
            self.pdf_widgets[provider]["output_hint"] = tk.StringVar()
            self.pdf_widgets[provider]["output_summary"] = tk.StringVar(
                value="保存先は事前確認後に表示します。"
            )
            self.pdf_widgets[provider]["stat_vars"] = {
                name: tk.StringVar(value="未確認" if name in self.PDF_PRIMARY else "—")
                for name in self.PDF_STATS
            }
        self.export_input = tk.StringVar()
        self.export_output = tk.StringVar(value="PDFフォルダーを選択すると表示します。")
        self.report_input = tk.StringVar()
        self.report_manifest = tk.StringVar()
        self.report_output_pdf = tk.StringVar()
        self.report_verified_md = tk.StringVar()
        self.report_verification_csv = tk.StringVar()
        self.report_date = tk.StringVar()
        self.report_keep_html = tk.BooleanVar(value=False)
        self.report_show_details = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="入力を選択してください。")
        self._report_stat_vars = {
            name: tk.StringVar(value="未確認" if name in self.REPORT_PRIMARY else "—")
            for name in self.REPORT_STATS
        }
        self._export_stat_vars = {
            name: tk.StringVar(value="未確認" if name in self.EXPORT_PRIMARY else "—")
            for name in self.EXPORT_STATS
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
            text="転職者条件の入力から求人取得・PDF整理・比較資料生成まで、1つの画面で行います。",
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
        self.candidate_tab_shell, self.candidate_tab = self._scrollable_tab()
        self.candidate_tab_canvas = self._tab_canvases[TAB_CANDIDATE]
        self.recruit_tab_shell, self.recruit_tab = self._scrollable_tab()
        self.pdf_tab_shell, self.pdf_tab = self._scrollable_tab()
        self.pdf_tab_canvas = self._tab_canvases[TAB_PDF]
        self.export_tab_shell, self.export_tab = self._scrollable_tab()
        self.copy_tab_shell, self.copy_tab = self._scrollable_tab()
        self.report_tab_shell, self.report_tab = self._scrollable_tab()
        self.notebook.add(self.candidate_tab_shell, text="転職者条件")
        self.notebook.add(self.recruit_tab_shell, text="求人取得")
        self.notebook.add(self.pdf_tab_shell, text="求人PDF整理")
        self.notebook.add(self.export_tab_shell, text="求人PDF全文統合テキスト化")
        self.notebook.add(self.copy_tab_shell, text="対象PDFコピー")
        self.notebook.add(self.report_tab_shell, text="比較資料PDF生成")
        self.notebook.bind("<<NotebookTabChanged>>", self._top_tab_changed, add="+")
        self._select_top_tab(TAB_CANDIDATE)
        self.candidate_panel = CandidateConditionsPanel(
            root=self.root,
            parent=self.candidate_tab,
            visuals=self.visuals,
        )
        self.recruit_panel = RecruitFeaturePanel(
            root=self.root,
            parent=self.recruit_tab,
            visuals=self.visuals,
            dialogs=self.dialogs,
            post_callback=self.post,
            candidate_profile_callback=self._resolve_candidate_profile,
        )
        self._build_pdf_tab()
        self._build_export_tab()
        self.copy_panel = PdfCopyPanel(self, self.copy_tab)
        self._build_report_tab()
        self.root.bind(
            "<MouseWheel>",
            self._route_candidate_mousewheel,
            add="+",
        )
        self.root.bind("<MouseWheel>", self._route_copy_mousewheel, add="+")

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
        self.pdf_tab.rowconfigure(0, weight=1)
        # A fixed notebook height can shrink the results tree below four rows.
        # Explicitly size the notebook from the fully constructed provider pages
        # so that the surrounding tab canvas, not the tree, handles overflow.
        self.pdf_provider_notebook = ttk.Notebook(self.pdf_tab, style=STYLE_NOTEBOOK)
        self.pdf_provider_notebook.grid(row=0, column=0, sticky="nsew")
        pages: list[ttk.Frame] = []
        self._changing_pdf_programmatically = True
        try:
            for provider, label in (("jobins", "JoBins"), ("circus", "circus")):
                page = ttk.Frame(self.pdf_provider_notebook, padding=4, style=STYLE_ROOT)
                page.columnconfigure(0, weight=1)
                page.rowconfigure(2, weight=1)
                self.pdf_provider_notebook.add(page, text=label)
                self._build_pdf_provider_page(page, provider)
                pages.append(page)
            self.pdf_provider_notebook.select(0)
        finally:
            self._changing_pdf_programmatically = False
        self.pdf_provider_notebook.update_idletasks()
        self.pdf_provider_notebook.configure(
            height=max(page.winfo_reqheight() for page in pages)
        )
        self.pdf_provider_notebook.bind("<<NotebookTabChanged>>", self._pdf_tab_changed, add="+")

    def _build_pdf_provider_page(self, page: ttk.Frame, provider: str) -> None:
        inputs = self.pdf_inputs[provider]
        widgets = self.pdf_widgets[provider]
        variables = widgets["stat_vars"]
        widgets["primary_cards"] = self._build_primary_metrics(
            page, self.PDF_PRIMARY, variables
        )

        card = self.visuals.create_card(page, padding=8)
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
        self._path_field(
            fields, 0, 0, "PDFフォルダ（必須）", inputs["input_dir"],
            lambda provider=provider: self._choose_pdf_folder(provider),
        )
        self._path_field(
            fields, 0, 1, "見出し別名JSON", inputs["aliases"],
            lambda provider=provider: self._choose_pdf_aliases(provider),
        )
        self._path_field(
            fields, 1, 0, "出力フォルダー（任意）", inputs["output_dir"],
            lambda provider=provider: self._choose_pdf_output_dir(provider),
            columnspan=2,
        )
        ttk.Label(
            form,
            textvariable=widgets["output_hint"],
            style=STYLE_CARD_CAPTION,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(3, 0))
        output_modes = ttk.Frame(form, style=STYLE_CARD_BODY)
        output_modes.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for index, (label, mode) in enumerate((
            ("JSON＋プロンプトMarkdown", AnalysisOutputMode.JSON_WITH_GUIDE),
            ("JSONのみ", AnalysisOutputMode.JSON_ONLY),
        )):
            button = ttk.Radiobutton(
                output_modes, text=label, variable=inputs["output_mode"], value=mode.value
            )
            button.grid(row=0, column=index, sticky="w", padx=(0 if index == 0 else 16, 0))
            self._input_widgets.append(button)
        self._update_pdf_output_hint(provider)

        result_card = self.visuals.create_card(page, padding=8)
        result_card.grid(row=2, column=0, sticky="nsew")
        result = result_card.body
        result.columnconfigure(0, weight=1)
        result.rowconfigure(4, weight=1)
        self._section_heading(
            result,
            "2. 事前確認と実行",
            "事前確認ではPDF名やJSONを変更しません。内容を確認してから本実行してください。",
        )
        actions = ttk.Frame(result, style=STYLE_CARD_BODY)
        actions.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        actions.columnconfigure(1, weight=1)
        left = ttk.Frame(actions, style=STYLE_CARD_BODY)
        left.grid(row=0, column=0, sticky="w")
        right = ttk.Frame(actions, style=STYLE_CARD_BODY)
        right.grid(row=0, column=2, sticky="e")
        widgets["preview_button"] = ttk.Button(
            left, text="事前確認", style=STYLE_BUTTON,
            command=lambda provider=provider: self._invoke_pdf_for(provider, "preview_pdf"),
        )
        widgets["preview_button"].grid(row=0, column=0)
        widgets["apply_button"] = ttk.Button(
            left, text="本実行", style=STYLE_PRIMARY_BUTTON,
            command=lambda provider=provider: self._invoke_pdf_for(provider, "apply_pdf"),
        )
        widgets["apply_button"].grid(row=0, column=1, padx=(8, 0))
        widgets["open_button"] = ttk.Button(
            right, text="出力フォルダーを開く", style=STYLE_BUTTON,
            command=lambda provider=provider: self._invoke_pdf_for(provider, "open_pdf_output_folder"),
        )
        widgets["open_button"].grid(row=0, column=0)
        widgets["secondary_labels"] = self._build_secondary_metrics(
            result, row=2, names=self.PDF_SECONDARY, variables=variables
        )
        ttk.Label(
            result, textvariable=widgets["output_summary"], style=STYLE_CARD_CAPTION,
            justify="left", wraplength=820,
        ).grid(row=3, column=0, sticky="ew", pady=(0, 5))
        widgets["tree"], widgets["empty"] = self._build_tree(
            result,
            row=4,
            columns=("status", "old", "new", "company", "position", "jobid", "json", "message"),
            headings=("状態", "元ファイル名", "変更後ファイル名", "会社名", "募集ポジション", "求人ID", "JSON", "警告・除外理由"),
            widths=(80, 180, 190, 140, 170, 80, 55, 260),
            empty_text="PDFフォルダーを選択し、「事前確認」を実行すると対象一覧が表示されます。",
        )

    def _invoke_pdf_for(self, provider: str, method: str) -> None:
        if self.controller and not self._busy and self.selected_pdf_provider() == provider:
            getattr(self.controller, method)()

    def _build_export_tab(self) -> None:
        self.export_tab.columnconfigure(0, weight=1)
        self.export_tab.rowconfigure(2, weight=1)
        self._export_primary_cards = self._build_primary_metrics(
            self.export_tab, self.EXPORT_PRIMARY, self._export_stat_vars
        )

        card = self.visuals.create_card(self.export_tab, padding=8)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        form = card.body
        form.columnconfigure(1, weight=1)
        self._section_heading(
            form, "1. 入力と出力",
            "選択フォルダー直下のPDFだけを対象にします。OCRや本文の要約・補正は行いません。",
        )
        fields = ttk.Frame(form, style=STYLE_CARD_BODY)
        fields.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        fields.columnconfigure(0, weight=1)
        self._path_field(fields, 0, 0, "PDFフォルダー（必須）", self.export_input,
                         self._choose_export_folder, columnspan=2)
        copy_button = ttk.Button(
            form, text="整理タブと同じフォルダーを使用", style=STYLE_BUTTON,
            command=self._copy_pdf_folder_to_export,
        )
        copy_button.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._input_widgets.append(copy_button)
        ttk.Label(form, text="出力予定Markdown", style=STYLE_FILE).grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(form, textvariable=self.export_output, style=STYLE_CARD_CAPTION,
                  justify="left", wraplength=760).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(4, 0))

        result_card = self.visuals.create_card(self.export_tab, padding=8)
        result_card.grid(row=2, column=0, sticky="nsew")
        result = result_card.body
        result.columnconfigure(0, weight=1)
        result.rowconfigure(3, weight=1)
        self._section_heading(
            result, "2. 事前確認と実行",
            "対象PDF、ページ数、警告を確認してからMarkdownを生成してください。",
        )
        actions = ttk.Frame(result, style=STYLE_CARD_BODY)
        actions.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        actions.columnconfigure(1, weight=1)
        left = ttk.Frame(actions, style=STYLE_CARD_BODY); left.grid(row=0, column=0, sticky="w")
        right = ttk.Frame(actions, style=STYLE_CARD_BODY); right.grid(row=0, column=2, sticky="e")
        self.export_preview_button = ttk.Button(left, text="事前確認", style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.preview_export())
        self.export_preview_button.grid(row=0, column=0)
        self.export_apply_button = ttk.Button(left, text="Markdownを生成", style=STYLE_PRIMARY_BUTTON,
            command=lambda: self.controller and self.controller.apply_export())
        self.export_apply_button.grid(row=0, column=1, padx=(8, 0))
        self.export_open_button = ttk.Button(right, text="Markdownを開く", style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.open_export_markdown())
        self.export_open_button.grid(row=0, column=0)
        self.export_folder_button = ttk.Button(right, text="出力フォルダーを開く", style=STYLE_BUTTON,
            command=lambda: self.controller and self.controller.open_export_output_folder())
        self.export_folder_button.grid(row=0, column=1, padx=(8, 0))
        self._export_secondary_labels = self._build_secondary_metrics(
            result, row=2, names=self.EXPORT_SECONDARY, variables=self._export_stat_vars
        )
        self.export_tree, self.export_empty = self._build_tree(
            result, row=3,
            columns=("number", "pdf", "pages", "characters", "status", "warning"),
            headings=("No.", "PDF名", "ページ", "抽出文字", "状態", "警告"),
            widths=(55, 280, 80, 110, 90, 360),
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
        for provider in PDF_PROVIDERS:
            for variable in self.pdf_inputs[provider].values():
                variable.trace_add(
                    "write", lambda *args, provider=provider: self._changed_pdf(provider)
                )
        self.export_input.trace_add("write", lambda *args: self._changed_export())
        for variable in (self.report_input, self.report_manifest, self.report_date, self.report_keep_html):
            variable.trace_add("write", lambda *args: self._changed_report())
        self.report_output_pdf.trace_add("write", lambda *args: self._report_output_changed())
        self.report_verified_md.trace_add("write", lambda *args: self._verified_output_changed())
        self.report_verification_csv.trace_add("write", lambda *args: self._verification_output_changed())

    def selected_pdf_provider(self) -> str:
        return self._selected_pdf_provider

    def _changed_pdf(self, provider: str) -> None:
        self._update_pdf_output_hint(provider)
        if (
            provider == self._selected_pdf_provider
            and not self._suppress_changes
            and not self._busy
            and self.controller
        ):
            self.controller.invalidate_pdf()

    def _update_pdf_output_hint(self, provider: str) -> None:
        mode = self.pdf_inputs[provider]["output_mode"].get()
        files = "JSON2個と比較指示Markdown" if mode == AnalysisOutputMode.JSON_WITH_GUIDE.value else "JSON2個"
        self.pdf_widgets[provider]["output_hint"].set(
            f"出力先が空欄の場合はPDFフォルダー内の「output」に{files}を保存します。"
        )

    def _pdf_tab_changed(self, event: tk.Event) -> None:
        del event
        if self._changing_pdf_programmatically:
            return
        selected = self.pdf_provider_notebook.select()
        if not selected:
            return
        try:
            index = self.pdf_provider_notebook.index(selected)
        except tk.TclError:
            return
        if not 0 <= index < len(PDF_PROVIDERS):
            return
        provider = PDF_PROVIDERS[index]
        if self._busy:
            self._set_pdf_provider_busy(True)
            return
        if provider == self._selected_pdf_provider:
            return
        self._selected_pdf_provider = provider
        if self.controller:
            self.controller.invalidate_pdf()
        else:
            for current in PDF_PROVIDERS:
                self._render_pdf_plan(None, provider=current)
        self.pdf_tab_canvas.yview_moveto(0.0)
        self.root.after_idle(self._tab_syncs[TAB_PDF])

    def _set_pdf_provider_busy(self, busy: bool) -> None:
        self._changing_pdf_programmatically = True
        try:
            self.pdf_provider_notebook.state(["!disabled"])
            for index in range(self.pdf_provider_notebook.index("end")):
                self.pdf_provider_notebook.tab(index, state="normal")
            self.pdf_provider_notebook.select(
                PDF_PROVIDERS.index(self._selected_pdf_provider)
            )
            if busy:
                self.pdf_provider_notebook.state(["disabled"])
        finally:
            self._changing_pdf_programmatically = False

    def _changed_report(self) -> None:
        if not self._suppress_changes and self.controller:
            self.controller.invalidate_report()

    def _changed_export(self) -> None:
        if self._suppress_changes:
            return
        raw = self.export_input.get().strip()
        self.export_output.set(str(resolve_export_output(Path(raw))) if raw else "PDFフォルダーを選択すると表示します。")
        if self.controller:
            self.controller.invalidate_export()

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

    def _choose_pdf_folder(self, provider: str) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="求人PDFがあるフォルダーを選択")
        if selected:
            self.pdf_inputs[provider]["input_dir"].set(selected)

    def _choose_pdf_aliases(self, provider: str) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root, title="見出し別名JSONを選択", filetypes=(("JSON", "*.json"),)
        )
        if selected:
            self.pdf_inputs[provider]["aliases"].set(selected)

    def _choose_pdf_output_dir(self, provider: str) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="JSON出力フォルダーを選択")
        if selected:
            self.pdf_inputs[provider]["output_dir"].set(selected)

    def _choose_export_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="全文統合テキスト化する求人PDFフォルダーを選択")
        if selected:
            self.export_input.set(selected)

    def _copy_pdf_folder_to_export(self) -> None:
        source = self.pdf_inputs[self.selected_pdf_provider()]["input_dir"].get().strip()
        if source:
            self.export_input.set(source)

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
            self._select_top_tab(index)
        return "break"

    def _route_candidate_mousewheel(self, event: tk.Event) -> str | None:
        if (
            self._busy
            or self._selected_top_tab() != TAB_CANDIDATE
            or getattr(event, "delta", 0) == 0
        ):
            return None
        target = getattr(event, "widget", None)
        if not isinstance(target, tk.Misc):
            return None
        try:
            if not target.winfo_exists():
                return None
        except tk.TclError:
            return None
        if not self._widget_is_within(target, self.candidate_tab_shell):
            return None
        if any(
            self._widget_is_within(target, input_widget)
            for input_widget in self.candidate_panel.widgets.values()
        ):
            return None
        if any(
            isinstance(widget, ttk.Scrollbar)
            for widget in self._widget_ancestors(target)
        ):
            return None
        amount = -1 if event.delta > 0 else 1
        self.candidate_tab_canvas.yview_scroll(amount, "units")
        return "break"

    def _route_copy_mousewheel(self, event: tk.Event) -> str | None:
        if self._busy or self._selected_top_tab() != TAB_COPY or not event.delta:
            return None
        widget = event.widget
        if not isinstance(widget, tk.Misc) or not self._widget_is_within(widget, self.copy_tab_shell):
            return None
        if any(isinstance(parent, (tk.Text, ttk.Treeview, ttk.Scrollbar))
               for parent in self._widget_ancestors(widget)):
            return None
        self._tab_canvases[TAB_COPY].yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    @staticmethod
    def _widget_ancestors(widget: tk.Misc) -> tuple[tk.Misc, ...]:
        ancestors: list[tk.Misc] = []
        current: tk.Misc | None = widget
        while current is not None:
            ancestors.append(current)
            parent = getattr(current, "master", None)
            current = parent if isinstance(parent, tk.Misc) else None
        return tuple(ancestors)

    @classmethod
    def _widget_is_within(cls, widget: tk.Misc, ancestor: tk.Misc) -> bool:
        return ancestor in cls._widget_ancestors(widget)

    def _shortcut_preview(self) -> str:
        if not self._busy:
            self._preview_active()
        return "break"

    def _shortcut_help(self) -> str:
        if not self._busy:
            self.show_help()
        return "break"

    def _shortcut_recruit(self, action: str) -> str:
        if (
            not self._busy
            and self.controller is not None
            and self._selected_top_tab() == TAB_RECRUIT
        ):
            self.controller.recruit_action(action)
        return "break"

    def _preview_active(self) -> None:
        if not self.controller:
            return
        selected = self._selected_top_tab()
        if selected == TAB_PDF:
            self.controller.preview_pdf()
        elif selected == TAB_EXPORT:
            self.controller.preview_export()
        elif selected == TAB_REPORT:
            self.controller.preview_report()
        elif selected == TAB_COPY:
            self.controller.preview_copy()

    def _resolve_candidate_profile(self) -> CandidateProfileDecision:
        missing = self.candidate_panel.missing_required_fields()
        if missing:
            choice = self.dialogs.choose(
                "転職者条件が未入力です",
                "転職者条件の必須項目が未入力です。現在入力されている職歴・資格なども含め、"
                "すべての転職者条件を無視して一般比較用ファイルを作成しますか？",
                actions=(
                    DialogAction(
                        "転職者条件へ戻る",
                        result="candidate",
                        is_default=True,
                    ),
                    DialogAction(
                        "一般比較として作成",
                        result="general",
                        primary=True,
                    ),
                ),
                cancel_result="candidate",
            )
            if choice == "general":
                self.candidate_panel.clear_errors()
                return CandidateProfileDecision(proceed=True)
            self._select_top_tab(TAB_CANDIDATE)
            self.candidate_panel.show_missing_required(missing)
            return CandidateProfileDecision(proceed=False)
        try:
            profile = self.candidate_panel.profile()
        except _CandidateProfileFieldError as error:
            self._select_top_tab(TAB_CANDIDATE)
            self.candidate_panel.focus_field(error.field)
            return CandidateProfileDecision(proceed=False)
        return CandidateProfileDecision(proceed=True, profile=profile)

    def request_candidate_profile(self) -> CandidateProfileDecision:
        return self._resolve_candidate_profile()

    def _top_tab_changed(self, event: tk.Event) -> None:
        del event
        if self._changing_tab_programmatically:
            return
        selected = self.notebook.select()
        if not selected:
            return
        try:
            index = self.notebook.index(selected)
        except tk.TclError:
            return
        if 0 <= index < self.notebook.index("end"):
            self._selected_tab_index = index

    def _selected_top_tab(self) -> int:
        selected = self.notebook.select()
        if selected:
            try:
                index = self.notebook.index(selected)
            except tk.TclError:
                index = self._selected_tab_index
            else:
                if 0 <= index < self.notebook.index("end"):
                    self._selected_tab_index = index
                    return index
        if 0 <= self._selected_tab_index < self.notebook.index("end"):
            return self._selected_tab_index
        self._selected_tab_index = 0
        return 0

    def _select_top_tab(self, index: int) -> None:
        if not 0 <= index < self.notebook.index("end"):
            index = 0
        self._changing_tab_programmatically = True
        try:
            self.notebook.select(index)
            self._selected_tab_index = index
        finally:
            self._changing_tab_programmatically = False

    def _set_top_notebook_busy(self, busy: bool) -> None:
        selected = self._selected_top_tab()
        self._changing_tab_programmatically = True
        try:
            self.notebook.state(["!disabled"])
            for index in range(self.notebook.index("end")):
                self.notebook.tab(index, state="normal")
            self.notebook.select(selected)
            if busy:
                self.notebook.state(["disabled"])
        finally:
            self._changing_tab_programmatically = False

    def current_pdf_request(self) -> PdfOrganizationRequest:
        provider = self.selected_pdf_provider()
        inputs = self.pdf_inputs[provider]
        input_dir = Path(inputs["input_dir"].get())
        raw_output_dir = inputs["output_dir"].get().strip()
        paths = resolve_base_output_paths(
            input_dir,
            Path(raw_output_dir) if raw_output_dir else None,
            include_guide=inputs["output_mode"].get() == AnalysisOutputMode.JSON_WITH_GUIDE.value,
        )
        return PdfOrganizationRequest(
            input_dir=input_dir,
            aliases_path=Path(inputs["aliases"].get()) if inputs["aliases"].get().strip() else None,
            job_info_json=paths.job_info_json,
            report_json=paths.report_json,
            comparison_guide_path=paths.comparison_guide_path,
            provider=provider,
        )

    def ask_organization_output_conflict(self, conflict: OrganizationOutputConflict) -> OutputConflictChoice:
        suggested = conflict.suggested_paths
        existing = "\n".join(f"- {path.name}" for path in conflict.existing_paths)
        suggestion = (
            "\n".join(path.name for path in suggested.targets)
            if suggested is not None
            else "—"
        )
        message = (
            f"出力フォルダー:\n{conflict.base_paths.output_dir}\n\n"
            f"既に存在する出力ファイル:\n{existing}\n\n"
            "置換する:\n"
            "求人情報JSONは追加項目とPDF未検出jobを保持して更新し、"
            "処理レポートJSONと比較指示Markdownは今回の内容で再生成します。\n\n"
            f"別名で保存する候補:\n{suggestion}\n"
            "既存のJSONとMarkdownは変更しません。"
        )
        return self.dialogs.ask_output_conflict("既存の出力ファイルがあります", message)

    def choose_legacy_csv(self, candidates: tuple[Path, ...]) -> Path | None:
        selected = filedialog.askopenfilename(
            parent=self.root, title="移行する旧求人情報CSVを選択",
            initialdir=str(candidates[0].parent) if candidates else None,
            filetypes=(("CSV", "*.csv"),),
        )
        return Path(selected) if selected else None

    def ask_legacy_csv_migration(self, source: Path, destination: Path) -> LegacyCsvMigrationChoice:
        message = (
            f"移行元CSV:\n{source}\n\n移行先JSON:\n{destination}\n\n"
            "標準項目、追加列、PDF未検出行をJSONへ引き継ぎます。\n"
            "移行元CSVは変更・削除しません。JSON作成後はCSVを参照しません。"
        )
        return self.dialogs.ask_legacy_csv_migration("旧求人情報CSVを取り込みますか？", message)

    def current_export_request(self) -> PdfTextExportRequest:
        input_dir = Path(self.export_input.get())
        return PdfTextExportRequest(input_dir, resolve_export_output(input_dir))

    def ask_export_output_conflict(self, conflict: ExportOutputConflict) -> OutputConflictChoice:
        message = (
            f"出力フォルダー:\n{conflict.base_path.parent}\n\n"
            f"既存Markdown: {conflict.base_path.name}\n"
            f"別名候補: {conflict.suggested_path.name if conflict.suggested_path else '—'}\n\n"
            "置換時は既存Markdownを今回の抽出結果で更新します。別名保存では既存ファイルを変更しません。"
        )
        return self.dialogs.ask_output_conflict("既存のMarkdownがあります", message)

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
        overwrite = [path.name for path in (plan.job_info_json, plan.report_json, plan.comparison_guide_path) if path is not None and path.exists()]
        output_mode = self._last_model.pdf_output_mode if self._last_model else None
        mode_note = {
            "新規保存": "新しいJSONを作成します。",
            "既存JSONを更新": "求人情報JSONの追加項目とPDF未検出jobを保持し、処理レポートJSONは再生成します。",
            "別名で新規保存": "既存JSONを変更せず、別名のJSONを新規作成します。",
        }.get(output_mode or "", "")
        migration_source = self._last_model.pdf_migration_source if self._last_model else None
        message = (
            f"対象PDF: {len(plan.results)}件\n"
            f"リネーム: {summary['リネーム対象']}件\n"
            f"JSON出力: {summary['JSON出力対象']}件\n"
            f"JSON除外: {summary['JSON除外']}件\n"
            f"PDF未検出job: {len(plan.job_info.stale_jobs)}件\n\n"
            f"保存方法: {output_mode or '新規保存'}\n"
            f"出力フォルダー: {plan.job_info_json.parent}\n"
            f"求人情報JSON: {plan.job_info_json.name}\n"
            f"処理レポートJSON: {plan.report_json.name}\n"
            f"比較指示書: {plan.comparison_guide_path.name if plan.comparison_guide_path else 'なし'}\n"
            f"比較方法: {'転職者条件付き' if plan.candidate_applied else '一般比較'}\n"
            f"上書き対象: {', '.join(overwrite) if overwrite else 'なし'}\n"
            f"旧CSV移行: {migration_source if migration_source else 'なし'}\n"
            f"{'旧CSVは変更しません。' if migration_source else ''}\n"
            f"{mode_note}\n\n"
            "除外されたPDFは移動・削除しません。PDF名・JSON・比較指示書を確定しますか？"
        )
        return self.dialogs.ask("求人PDF整理を実行しますか？", message, "実行")

    def ask_export_commit(self, plan: PdfTextExportPlan) -> bool:
        counts = {"no_text": 0, "nul_replaced": 0, "large_chars": 0, "large_bytes": 0}
        for warning in plan.warnings:
            counts[warning.code] = counts.get(warning.code, 0) + 1
        mode = self._last_model.export_output_mode if self._last_model else None
        message = (
            f"PDF: {plan.document_count}件\nページ: {plan.page_count}\n抽出文字: {plan.character_count:,}\n"
            f"出力サイズ: {plan.byte_count:,}バイト\n警告: {len(plan.warnings)}件\n"
            f"テキストなしページ: {counts['no_text']} / NUL置換: {counts['nul_replaced']}\n\n"
            f"出力フォルダー: {plan.request.output_markdown.parent}\nMarkdown: {plan.request.output_markdown.name}\n"
            f"保存方法: {mode or '新規保存'}\n\n"
            "OCRは行いません。PDF本文はNULの一対一置換以外は加工しません。生成しますか？"
        )
        return self.dialogs.ask("PDF全文統合テキスト化Markdownを生成しますか？", message, "生成")

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
        self.copy_panel.render(model)
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
            if model.export_progress is not None:
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=100, value=self._export_progress_percent(model.export_progress))
            else:
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)
            self.progress.grid_remove()

        active_pdf_provider = self.selected_pdf_provider()
        report_ready = bool(
            self.report_input.get().strip()
            and Path(self.report_input.get()).is_file()
            and self.report_manifest.get().strip()
            and Path(self.report_manifest.get()).is_file()
            and self.report_output_pdf.get().strip()
            and self.report_verified_md.get().strip()
            and self.report_verification_csv.get().strip()
        )
        export_ready = bool(self.export_input.get().strip() and Path(self.export_input.get()).is_dir())
        for provider in PDF_PROVIDERS:
            widgets = self.pdf_widgets[provider]
            folder = self.pdf_inputs[provider]["input_dir"].get().strip()
            pdf_ready = bool(folder and Path(folder).is_dir())
            enabled = "normal" if pdf_ready and not model.busy else "disabled"
            widgets["preview_button"].configure(state=enabled)
            widgets["apply_button"].configure(state=enabled)
            widgets["open_button"].configure(
                state="normal" if provider == active_pdf_provider and model.pdf_result and not model.busy else "disabled"
            )
        self.export_preview_button.configure(state="normal" if export_ready and not model.busy else "disabled")
        self.export_apply_button.configure(state="normal" if export_ready and not model.busy else "disabled")
        self.export_open_button.configure(state="normal" if model.export_result and not model.busy else "disabled")
        self.export_folder_button.configure(state="normal" if model.export_result and not model.busy else "disabled")
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
        self.candidate_panel.set_host_busy(model.busy)
        self.recruit_panel.set_host_busy(model.busy)
        self._set_top_notebook_busy(model.busy)
        self._set_pdf_provider_busy(model.busy)
        for provider in PDF_PROVIDERS:
            self._render_pdf_plan(
                model.pdf_plan if provider == active_pdf_provider else None,
                model.pdf_output_mode if provider == active_pdf_provider else None,
                candidate_confirmed=model.busy or model.pdf_result is not None,
                provider=provider,
            )
        self._render_export_plan(model.export_plan)
        self._render_report_plan(model.report_plan)

    @staticmethod
    def _export_progress_percent(event) -> float:
        if event.stage == "page" and event.document_count:
            page_fraction = event.page_number / max(1, event.page_count)
            return min(95.0, ((event.document_index - 1 + page_fraction) / event.document_count) * 95.0)
        return {"discovered": 0.0, "building": 96.0, "committing": 98.0, "complete": 100.0}.get(event.stage, 0.0)

    def _render_pdf_plan(
        self,
        plan: PdfOrganizationPlan | None,
        output_mode: str | None = None,
        *,
        candidate_confirmed: bool = False,
        provider: str | None = None,
    ) -> None:
        widgets = self.pdf_widgets[provider or self.selected_pdf_provider()]
        tree = widgets["tree"]
        empty = widgets["empty"]
        variables = widgets["stat_vars"]
        self._clear_tree(tree)
        if plan is None:
            self._reset_metrics(self.PDF_PRIMARY, self.PDF_SECONDARY, variables)
            empty.place(relx=0.5, rely=0.5, anchor="center")
            empty.lift()
            self._apply_secondary_tones(widgets["secondary_labels"], {})
            widgets["output_summary"].set("保存先は事前確認後に表示します。")
            return
        values = {name: 0 for name in self.PDF_STATS}
        values.update(plan.summary)
        values["対象PDF"] = len(plan.results)
        values["PDF未検出"] = len(plan.job_info.stale_jobs)
        self._set_metrics(values, variables)
        empty.place_forget()
        widgets["output_summary"].set(
            f"保存方法: {output_mode or '新規保存'}　保存先: {plan.job_info_json.parent}\n"
            f"求人情報JSON: {plan.job_info_json.name}　処理レポートJSON: {plan.report_json.name}"
            + (f"\n比較指示書: {plan.comparison_guide_path.name}　" +
               (('転職者条件付き' if plan.candidate_applied else '一般比較') if candidate_confirmed else '転職者条件は本実行時に確認します。')
               if plan.comparison_guide_path is not None else "")
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
            tree.insert(
                "",
                "end",
                values=(
                    result.status,
                    result.source_path.name,
                    result.proposed_name or "—",
                    result.company.value,
                    result.resolved_position or result.raw_position.value,
                    result.job_id or "—",
                    "対象" if result.is_job_info_eligible else "除外",
                    " / ".join(messages[:3]),
                ),
                tags=(tone,),
            )
        tones = {"部分成功": "warning", "改名保留": "warning", "エラー": "error", "JSON除外": "warning", "PDF未検出": "warning"}
        self._apply_secondary_tones(widgets["secondary_labels"], values, tones)

    def _render_export_plan(self, plan: PdfTextExportPlan | None) -> None:
        self._clear_tree(self.export_tree)
        if plan is None:
            self._reset_metrics(self.EXPORT_PRIMARY, self.EXPORT_SECONDARY, self._export_stat_vars)
            self.export_empty.place(relx=0.5, rely=0.5, anchor="center")
            self.export_empty.lift()
            self._apply_secondary_tones(self._export_secondary_labels, {})
            return
        warning_counts: dict[str, int] = {}
        for warning in plan.warnings:
            warning_counts[warning.code] = warning_counts.get(warning.code, 0) + 1
        values = {
            "対象PDF": plan.document_count, "ページ": plan.page_count,
            "抽出文字": f"{plan.character_count:,}", "警告": len(plan.warnings),
            "テキストなしページ": warning_counts.get("no_text", 0),
            "NUL置換": warning_counts.get("nul_replaced", 0),
            "出力サイズ": f"{plan.byte_count:,} B",
        }
        self._set_metrics(values, self._export_stat_vars)
        self.export_empty.place_forget()
        warnings_by_file: dict[str, list[str]] = {}
        for warning in plan.warnings:
            if warning.filename:
                warnings_by_file.setdefault(warning.filename, []).append(warning.message)
        for document in plan.documents:
            messages = warnings_by_file.get(document.filename, [])
            self.export_tree.insert("", "end", values=(
                document.index, document.filename, document.page_count,
                f"{document.character_count:,}", "警告" if messages else "正常",
                " / ".join(messages[:3]),
            ), tags=("warning" if messages else "normal",))
        self._apply_secondary_tones(self._export_secondary_labels, values, {
            "警告": "warning", "テキストなしページ": "warning", "NUL置換": "warning"
        })

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
        trees = [
            self.pdf_widgets[provider]["tree"] for provider in PDF_PROVIDERS
            if "tree" in self.pdf_widgets[provider]
        ]
        trees.extend((getattr(self, "export_tree", None), getattr(self, "report_tree", None)))
        for tree in trees:
            if tree is None:
                continue
            tree.tag_configure("normal", foreground=self.visuals.palette.text)
            tree.tag_configure("warning", foreground=self.visuals.tone_color("warning"))
            tree.tag_configure("error", foreground=self.visuals.tone_color("error"))

    def _visuals_changed(self) -> None:
        self.style.configure("App.TNotebook.Tab", padding=(10, 8))
        if hasattr(self, "copy_panel"):
            palette = self.visuals.palette
            self.copy_panel.editor.configure(background=palette.surface, foreground=palette.text,
                                             insertbackground=palette.text, selectbackground=palette.accent)
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
        self._dispatcher.post(callback)

    def close(self) -> None:
        self._dispatcher.close()
        self.root.destroy()


__all__ = [
    "DialogPresenter",
    "TAB_CANDIDATE",
    "TAB_COPY",
    "TAB_EXPORT",
    "TAB_PDF",
    "TAB_RECRUIT",
    "TAB_REPORT",
    "TkView",
    "WindowsOutputOpener",
]
