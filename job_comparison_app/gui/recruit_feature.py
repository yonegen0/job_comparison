"""Embedded JoBins/CareerBank feature for the combined desktop application."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from enum import Enum
import ctypes
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Protocol

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.application import ProviderHealth, ProviderStatus, RecruitJobApplication
from recruit_job_tool.errors import AlreadyRunningError, RecruitJobToolError
from recruit_job_tool.gui.controller import (
    AnalysisDialogSpec,
    CandidateProfileDecision,
    Controller as RecruitController,
    DialogSpec,
    GlobalPhase,
    ViewModel as RecruitControllerViewModel,
)
from recruit_job_tool.migration import MigrationManager
from recruit_job_tool.models import AnalysisOutputMode, JobSource
from recruit_job_tool.constants import MAX_HTML_FILES_PER_IMPORT
from recruit_job_tool.single_instance import SingleInstanceLock
from recruit_job_tool.workflow import ImportMode

from .dialogs import DialogAction, DialogKind, DialogPresenter
from .visuals import (
    STYLE_BUTTON,
    STYLE_CARD_BODY,
    STYLE_CARD_CAPTION,
    STYLE_FILE,
    STYLE_METRIC_LABEL,
    STYLE_METRIC_VALUE,
    STYLE_NOTEBOOK,
    STYLE_PRIMARY_BUTTON,
    STYLE_ROOT,
    STYLE_SECTION,
    VisualManager,
)


class RecruitFeaturePhase(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    CHECKING = "CHECKING"
    READY = "READY"
    LOCKED = "LOCKED"
    BUSY = "BUSY"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class RecruitFeatureViewModel:
    phase: RecruitFeaturePhase = RecruitFeaturePhase.UNINITIALIZED
    active_provider: JobSource = JobSource.JOBINS
    providers: dict[JobSource, ProviderStatus | None] = field(
        default_factory=lambda: {provider: None for provider in JobSource}
    )
    selected_paths: dict[JobSource, tuple[Path, ...]] = field(
        default_factory=lambda: {provider: () for provider in JobSource}
    )
    modes: dict[JobSource, ImportMode] = field(
        default_factory=lambda: {provider: ImportMode.REPLACE for provider in JobSource}
    )
    status_text: str = "求人取得機能を確認しています…"
    tone: str = "normal"
    busy: bool = False
    can_initialize: bool = True
    can_retry: bool = False


class TaskRunner(Protocol):
    def submit(self, callback: Callable[[], object]) -> Future: ...


class RecruitFeaturePanel:
    """Tk panel implementing the existing recruit controller's View protocol."""

    def __init__(
        self,
        *,
        root: tk.Tk,
        parent: ttk.Frame,
        visuals: VisualManager,
        dialogs: DialogPresenter,
        post_callback: Callable[[Callable[[], None]], None],
        candidate_profile_callback: Callable[[], CandidateProfileDecision],
    ) -> None:
        self.root = root
        self.parent = parent
        self.visuals = visuals
        self.dialogs = dialogs
        self._post_callback = post_callback
        self._candidate_profile_callback = candidate_profile_callback
        self.controller: RecruitController | None = None
        self._state_callback: Callable[[RecruitControllerViewModel], None] | None = None
        self._initialize_command: Callable[[], None] | None = None
        self._host_busy = False
        self._controller_model: RecruitControllerViewModel | None = None
        self._phase = RecruitFeaturePhase.UNINITIALIZED
        self._rendering_provider_selection = False
        self._last_provider = JobSource.JOBINS
        self._controls: dict[JobSource, dict[str, tk.Widget]] = {}
        self._paths = {provider: tk.StringVar(value="HTML未選択") for provider in JobSource}
        self._path_lists: dict[JobSource, tk.Listbox] = {}
        self._modes = {
            provider: tk.StringVar(value=ImportMode.REPLACE.value) for provider in JobSource
        }
        self._output_modes = {
            provider: tk.StringVar(value=AnalysisOutputMode.JSON_WITH_GUIDE.value) for provider in JobSource
        }
        self._current = {provider: tk.StringVar(value="未初期化") for provider in JobSource}
        self._csv = {provider: tk.StringVar(value="未初期化") for provider in JobSource}
        self._restore = {provider: tk.StringVar(value="未初期化") for provider in JobSource}
        self._build()

    def set_state_callback(
        self, callback: Callable[[RecruitControllerViewModel], None]
    ) -> None:
        self._state_callback = callback

    def set_initialize_command(self, callback: Callable[[], None]) -> None:
        self._initialize_command = callback

    def bind_controller(self, controller: RecruitController | None) -> None:
        self.controller = controller

    def _build(self) -> None:
        self.parent.columnconfigure(0, weight=1)

        self.setup_card = self.visuals.create_card(self.parent, padding=16)
        self.setup_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        setup = self.setup_card.body
        setup.columnconfigure(0, weight=1)
        self.setup_title = ttk.Label(setup, text="求人取得機能の初期設定", style=STYLE_SECTION)
        self.setup_title.grid(row=0, column=0, sticky="w")
        self.setup_message = ttk.Label(
            setup,
            text=(
                "JoBinsとCareerBankの保存済み検索結果HTMLを取り込むには、"
                "既存データの確認または新規開始が必要です。\n"
                "設定を開始するまで、PDF関連の3機能はそのまま利用できます。"
            ),
            style=STYLE_CARD_CAPTION,
            justify="left",
            wraplength=720,
        )
        self.setup_message.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        self.setup_button = ttk.Button(
            setup,
            text="初期設定を開始",
            style=STYLE_PRIMARY_BUTTON,
            command=lambda: self._initialize_command and self._initialize_command(),
        )
        self.setup_button.grid(row=2, column=0, sticky="w")

        self.provider_notebook = ttk.Notebook(self.parent, style=STYLE_NOTEBOOK)
        self.provider_notebook.grid(row=1, column=0, sticky="nsew")
        for provider in JobSource:
            frame = ttk.Frame(self.provider_notebook, padding=4, style=STYLE_ROOT)
            frame.columnconfigure(0, weight=1)
            self.provider_notebook.add(frame, text=_label(provider))
            self._build_provider_tab(provider, frame)
        self.provider_notebook.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")
        self.provider_notebook.select(0)
        self.provider_notebook.grid_remove()

    def _build_provider_tab(self, provider: JobSource, frame: ttk.Frame) -> None:
        metrics = ttk.Frame(frame, style=STYLE_ROOT)
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            metrics.columnconfigure(column, weight=1, uniform=f"recruit-{provider.value}")
        for column, (title, variable) in enumerate(
            (
                ("現在の求人", self._current[provider]),
                ("CSV", self._csv[provider]),
                ("復元可能", self._restore[provider]),
            )
        ):
            card = self.visuals.create_card(metrics, padding=8)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column < 2 else 0)
            ttk.Label(card.body, text=title, style=STYLE_METRIC_LABEL).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(card.body, textvariable=variable, style=STYLE_METRIC_VALUE).grid(
                row=1, column=0, sticky="w", pady=(4, 0)
            )

        import_card = self.visuals.create_card(frame, padding=10)
        import_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        body = import_card.body
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="1. 求人HTMLを取り込む", style=STYLE_SECTION).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            body,
            text="Ctrl・ShiftキーでHTMLを最大5個選択できます。一覧の順に取り込みます。",
            style=STYLE_CARD_CAPTION,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
        selection = ttk.Frame(body, style=STYLE_CARD_BODY)
        selection.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        selection.columnconfigure(0, weight=1)
        ttk.Label(selection, textvariable=self._paths[provider], style=STYLE_CARD_CAPTION).grid(
            row=0, column=0, sticky="w"
        )
        path_entry = tk.Listbox(selection, height=MAX_HTML_FILES_PER_IMPORT, exportselection=False)
        path_entry.grid(row=1, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(selection, orient="horizontal", command=path_entry.xview)
        scrollbar.grid(row=2, column=0, sticky="ew")
        path_entry.configure(xscrollcommand=scrollbar.set)
        self._path_lists[provider] = path_entry
        choose_button = ttk.Button(
            body,
            text=f"HTMLを選択（最大{MAX_HTML_FILES_PER_IMPORT}個）",
            style=STYLE_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "choose_html"),
        )
        choose_button.grid(row=2, column=1, sticky="e")

        modes = ttk.Frame(body, style=STYLE_CARD_BODY)
        modes.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 8))
        replace_button = ttk.Radiobutton(
            modes,
            text="置換",
            value=ImportMode.REPLACE.value,
            variable=self._modes[provider],
            command=lambda p=provider: self._mode_changed(p),
        )
        replace_button.grid(row=0, column=0, sticky="w")
        merge_button = ttk.Radiobutton(
            modes,
            text="追加・更新",
            value=ImportMode.MERGE.value,
            variable=self._modes[provider],
            command=lambda p=provider: self._mode_changed(p),
        )
        merge_button.grid(row=0, column=1, sticky="w", padx=(16, 0))
        import_button = ttk.Button(
            body,
            text="取り込む",
            style=STYLE_PRIMARY_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "import_selected"),
        )
        import_button.grid(row=4, column=0, columnspan=2, sticky="w")

        output_card = self.visuals.create_card(frame, padding=10)
        output_card.grid(row=2, column=0, sticky="ew")
        output = output_card.body
        output.columnconfigure(0, weight=1)
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="2. 出力・データ管理", style=STYLE_SECTION).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        export_button = ttk.Button(
            output,
            text="CSVを再出力",
            style=STYLE_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "export_csv"),
        )
        export_button.grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        restore_button = ttk.Button(
            output,
            text="直前状態へ戻す",
            style=STYLE_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "restore"),
        )
        restore_button.grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))
        output_modes = ttk.Frame(output, style=STYLE_CARD_BODY)
        output_modes.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        guide_mode = ttk.Radiobutton(
            output_modes, text="JSON＋プロンプトMarkdown", variable=self._output_modes[provider],
            value=AnalysisOutputMode.JSON_WITH_GUIDE.value,
            command=lambda p=provider: self._output_mode_changed(p),
        )
        guide_mode.grid(row=0, column=0, sticky="w")
        json_mode = ttk.Radiobutton(
            output_modes, text="JSONのみ", variable=self._output_modes[provider],
            value=AnalysisOutputMode.JSON_ONLY.value,
            command=lambda p=provider: self._output_mode_changed(p),
        )
        json_mode.grid(row=0, column=1, sticky="w", padx=(16, 0))
        analysis_button = ttk.Button(
            output,
            text="AI比較ファイルを作成",
            style=STYLE_PRIMARY_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "generate_analysis"),
        )
        analysis_button.grid(row=3, column=0, sticky="ew", padx=(0, 4))
        folder_button = ttk.Button(
            output,
            text="出力フォルダーを開く",
            style=STYLE_BUTTON,
            command=lambda p=provider: self._invoke_for(p, "open_output_folder"),
        )
        folder_button.grid(row=3, column=1, sticky="ew", padx=(4, 0))

        self._controls[provider] = {
            "path": path_entry,
            "choose_html": choose_button,
            "replace": replace_button,
            "merge": merge_button,
            "import_": import_button,
            "export": export_button,
            "restore": restore_button,
            "analysis": analysis_button,
            "output_guide": guide_mode,
            "output_json": json_mode,
            "folder": folder_button,
        }

    def set_phase(
        self,
        phase: RecruitFeaturePhase,
        message: str,
        *,
        can_initialize: bool = False,
        can_retry: bool = False,
    ) -> None:
        self._phase = phase
        if phase in {RecruitFeaturePhase.READY, RecruitFeaturePhase.BUSY}:
            self.setup_card.grid_remove()
            self.provider_notebook.grid()
            return
        self.provider_notebook.grid_remove()
        self.setup_card.grid()
        self.setup_message.configure(text=message)
        self.setup_button.configure(
            text="再試行" if can_retry else "初期設定を開始",
            state="normal" if (can_initialize or can_retry) and not self._host_busy else "disabled",
        )

    def set_host_busy(self, busy: bool) -> None:
        self._host_busy = busy
        if self._controller_model is not None:
            self._render_controls(self._controller_model)
        elif self._phase not in {RecruitFeaturePhase.READY, RecruitFeaturePhase.BUSY}:
            enabled = self._phase in {
                RecruitFeaturePhase.UNINITIALIZED,
                RecruitFeaturePhase.LOCKED,
                RecruitFeaturePhase.ERROR,
            }
            self.setup_button.configure(state="normal" if enabled and not busy else "disabled")

    def render(self, view_model: RecruitControllerViewModel) -> None:
        self._controller_model = view_model
        self.set_phase(
            RecruitFeaturePhase.BUSY if view_model.busy else RecruitFeaturePhase.READY,
            view_model.status_text,
        )
        active_index = 0 if view_model.active_provider is JobSource.JOBINS else 1
        self._rendering_provider_selection = True
        try:
            self.provider_notebook.state(["!disabled"])
            for index in range(self.provider_notebook.index("end")):
                self.provider_notebook.tab(index, state="normal")
            self.provider_notebook.select(active_index)
            self._last_provider = view_model.active_provider
            for provider, provider_model in view_model.providers.items():
                status = provider_model.status
                paths = provider_model.selected_paths
                self._paths[provider].set(
                    f"選択済み：{len(paths)} / {MAX_HTML_FILES_PER_IMPORT}個" if paths else "HTML未選択"
                )
                listing = self._path_lists[provider]
                names = [path.name.casefold() for path in paths]
                labels = tuple(
                    f"{index}. {path.name}" +
                    (f" （{path.parent}）" if names.count(path.name.casefold()) > 1 else "")
                    for index, path in enumerate(paths, 1)
                )
                if listing.get(0, tk.END) != labels:
                    listing.delete(0, tk.END)
                    for label in labels:
                        listing.insert(tk.END, label)
                self._modes[provider].set(provider_model.mode.value)
                self._output_modes[provider].set(provider_model.output_mode.value)
                if status is None:
                    self._current[provider].set("確認中")
                    self._csv[provider].set("確認中")
                    self._restore[provider].set("確認中")
                else:
                    self._current[provider].set(
                        "利用不可"
                        if status.current_count is None
                        else f"{status.current_count}件"
                    )
                    if status.output_error:
                        csv_text = "出力先エラー"
                    elif not status.output_configured:
                        csv_text = "出力先未設定"
                    else:
                        csv_text = "作成済み" if status.csv_exists else "未作成"
                    self._csv[provider].set(csv_text)
                    self._restore[provider].set(
                        "あり" if status.restore_available else "なし"
                    )
            self._render_controls(view_model)
        finally:
            self._rendering_provider_selection = False
        if self._state_callback is not None:
            self._state_callback(view_model)

    def _render_controls(self, view_model: RecruitControllerViewModel) -> None:
        effective_busy = self._host_busy or view_model.busy
        self.provider_notebook.state(["!disabled"])
        for index in range(self.provider_notebook.index("end")):
            self.provider_notebook.tab(index, state="normal")
        for provider in JobSource:
            provider_model = view_model.providers[provider]
            status = provider_model.status
            unavailable = status is None or status.health is ProviderHealth.UNAVAILABLE
            replace_required = bool(
                status and status.health is ProviderHealth.REPLACE_REQUIRED
            )
            output_ready = bool(
                status and status.output_configured and not status.output_error
            )
            current_count = status.current_count if status else 0
            enabled = {
                "choose_html": not effective_busy and not unavailable,
                "replace": not effective_busy and not unavailable,
                "merge": not effective_busy and not unavailable and not replace_required
                and bool(status and not status.output_error),
                "import_": not effective_busy and not unavailable
                and bool(provider_model.selected_paths),
                "export": not effective_busy and bool(status)
                and status.health is ProviderHealth.READY and bool(current_count) and output_ready,
                "restore": not effective_busy and bool(status)
                and status.health is ProviderHealth.READY and status.restore_available and output_ready,
                "analysis": not effective_busy and bool(status)
                and status.health is ProviderHealth.READY and bool(current_count) and output_ready,
                "folder": not effective_busy and output_ready,
                "output_guide": not effective_busy and not unavailable,
                "output_json": not effective_busy and not unavailable,
            }
            for name, widget in self._controls[provider].items():
                if name == "path":
                    continue
                widget.configure(state="normal" if enabled[name] else "disabled")
        if effective_busy:
            self.provider_notebook.state(["disabled"])

    def _invoke_for(self, provider: JobSource, method: str) -> None:
        if self.controller is None or self._host_busy:
            return
        self.controller.select_tab(provider)
        getattr(self.controller, method)()

    def _mode_changed(self, provider: JobSource) -> None:
        if self.controller is None or self._host_busy:
            return
        try:
            mode = ImportMode(self._modes[provider].get())
        except ValueError:
            mode = ImportMode.REPLACE
        self.controller.select_tab(provider)
        self.controller.change_mode(mode)

    def _output_mode_changed(self, provider: JobSource) -> None:
        if self.controller is None or self._host_busy or self.controller.model.busy:
            return
        mode = AnalysisOutputMode(self._output_modes[provider].get())
        self.controller.select_tab(provider)
        self.controller.change_output_mode(mode)

    def _tab_changed(self, event: tk.Event) -> None:
        del event
        if (
            self._rendering_provider_selection
            or self.controller is None
            or self._host_busy
            or self.controller.model.busy
            or not self.provider_notebook.select()
        ):
            return
        provider = self.notebook_provider()
        if provider is self.controller.model.active_provider:
            return
        self.controller.select_tab(provider)

    def select_provider(self, provider: JobSource) -> None:
        if self.controller is not None:
            self.controller.select_tab(provider)

    def notebook_provider(self) -> JobSource:
        selected = self.provider_notebook.select()
        if selected:
            try:
                index = self.provider_notebook.index(selected)
            except tk.TclError:
                pass
            else:
                provider = JobSource.JOBINS if index == 0 else JobSource.CAREERBANK
                self._last_provider = provider
                return provider
        if self._controller_model is not None:
            return self._controller_model.active_provider
        return self._last_provider

    def ask_replace(self, dialog_spec: DialogSpec) -> bool:
        return self.dialogs.ask(dialog_spec.title, dialog_spec.message, "置き換える")

    def ask_restore(self, dialog_spec: DialogSpec) -> bool:
        return self.dialogs.ask(dialog_spec.title, dialog_spec.message, "直前状態へ戻す")

    def show_info(self, dialog_spec: DialogSpec) -> None:
        self.dialogs.show(dialog_spec.title, dialog_spec.message, DialogKind.INFO)

    def show_warning(self, dialog_spec: DialogSpec) -> None:
        self.dialogs.show(dialog_spec.title, dialog_spec.message, DialogKind.WARNING)

    def show_error(self, dialog_spec: DialogSpec) -> None:
        self.dialogs.show(dialog_spec.title, dialog_spec.message, DialogKind.ERROR)

    def show_analysis_result(self, dialog_spec: AnalysisDialogSpec) -> None:
        if not dialog_spec.guide_requested:
            message = (
                f"{dialog_spec.message}\n\n求人JSON: {dialog_spec.json_basename}\n\n"
                "転職者条件は使用していません。求人JSONだけを保存しました。\n"
                "分析用JSONだけでAIの一般比較を利用できます。"
            )
        elif dialog_spec.candidate_applied:
            guide = dialog_spec.guide_basename or "作成できませんでした"
            message = (
                f"{dialog_spec.message}\n\n分析用求人JSON（必須）: {dialog_spec.json_basename}\n"
                f"転職者条件入りガイド（必須）: {guide}\n\n"
                "AIへ2ファイルを一緒に添付してください。"
            )
        else:
            guide = dialog_spec.guide_basename or "作成できませんでした（JSONは利用できます）"
            message = (
                f"{dialog_spec.message}\n\n分析用JSON（必須）: {dialog_spec.json_basename}\n"
                f"詳細ガイド（任意）: {guide}\n\n"
                "転職者条件は使用していません。分析用JSONだけでAI比較を利用できます。"
            )
        self.dialogs.show(dialog_spec.title, message, DialogKind.SUCCESS)

    def request_candidate_profile(self) -> CandidateProfileDecision:
        return self._candidate_profile_callback()

    def focus(self, control_id: str) -> None:
        key = "import_" if control_id == "import" else control_id
        widget = self._controls[self.notebook_provider()].get(key)
        if widget is not None:
            widget.focus_set()

    def post(self, callback: Callable[[], None]) -> None:
        self._post_callback(callback)

    def copy_text(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def close(self) -> None:
        self.root.destroy()

    def choose_html(self, *, initial_directory: Path | None) -> tuple[Path, ...]:
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title=f"求人検索結果HTMLを選択（最大{MAX_HTML_FILES_PER_IMPORT}個）",
            initialdir=str(initial_directory) if initial_directory else None,
            filetypes=(("HTML", "*.html *.htm"),),
        )
        return tuple(Path(path) for path in selected)

    def open(self, path: Path) -> None:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise OSError("出力フォルダーが存在しません")
        if os.name != "nt":
            raise OSError("この環境ではフォルダーを開けません")
        os.startfile(resolved)  # type: ignore[attr-defined]

    def ask_adopt_existing(self) -> bool:
        return self.dialogs.ask(
            "既存データを使用しますか？",
            "保存先に既存データがあります。検証して求人取得機能で使用しますか？",
            "既存データを使用",
        )

    def ask_for_legacy_folder(self) -> bool:
        result = self.dialogs.choose(
            "旧版データの確認",
            "旧版の求人整理ツールで保存したデータがありますか？\n"
            "ない場合は新規データとして開始します。",
            actions=(
                DialogAction("新規に開始", result=False, is_default=True),
                DialogAction("旧版フォルダーを選択", result=True, primary=True),
            ),
            cancel_result=False,
        )
        return bool(result)

    def choose_legacy_folder(self) -> Path | None:
        selected = filedialog.askdirectory(
            parent=self.root, title="旧版の求人整理ツールフォルダーを選択"
        )
        return Path(selected) if selected else None

    def ask_migrate(self) -> bool | None:
        result = self.dialogs.choose(
            "旧データを移行しますか？",
            "旧版の求人データを新しい保存先へコピーして使用しますか？\n"
            "旧版のファイルは変更・削除しません。",
            actions=(
                DialogAction("キャンセル", result=None, is_default=True),
                DialogAction("新規に開始", result=False),
                DialogAction("移行して使用", result=True, primary=True),
            ),
            cancel_result=None,
        )
        return result if result in {True, False, None} else None


class RecruitFeatureCoordinator:
    """Owns lazy initialization and adapts the standalone controller to the host."""

    def __init__(
        self,
        *,
        panel: RecruitFeaturePanel,
        task_runner: TaskRunner,
        on_global_state: Callable[[bool, str, str], None],
    ) -> None:
        self.panel = panel
        self.task_runner = task_runner
        self.on_global_state = on_global_state
        self.model = RecruitFeatureViewModel()
        self._lock: SingleInstanceLock | None = None
        self._controller: RecruitController | None = None
        self._token = 0
        panel.set_initialize_command(self.initialize_or_retry)
        panel.set_state_callback(self._controller_rendered)

    @property
    def controller(self) -> RecruitController | None:
        return self._controller

    def probe(self) -> None:
        self._set_phase(
            RecruitFeaturePhase.CHECKING,
            "既存の求人取得データを確認しています…",
            busy=True,
        )
        try:
            paths = AppPaths.from_local_app_data()
            lock = SingleInstanceLock().acquire()
            self._lock = lock
            manager = MigrationManager(paths)
            if not manager.is_ready():
                self._release_lock()
                self._set_phase(
                    RecruitFeaturePhase.UNINITIALIZED,
                    "求人取得機能はまだ初期設定されていません。\n"
                    "設定を開始するまで、PDF関連の3機能はそのまま利用できます。",
                    can_initialize=True,
                )
                return
            self._run_activation(
                lambda: _initialize_ready_application(paths, manager),
                "既存の求人取得データを読み込んでいます…",
            )
        except AlreadyRunningError:
            self._set_phase(
                RecruitFeaturePhase.LOCKED,
                "独立版の求人整理ツールが起動しています（APP003）。\n"
                "独立版を終了してから再試行してください。PDF関連の3機能は利用できます。",
                can_retry=True,
                tone="warning",
            )
        except RecruitJobToolError as error:
            self._release_lock()
            self._set_phase(
                RecruitFeaturePhase.ERROR,
                f"求人取得機能を確認できませんでした（{error.error_code}）。\n"
                "保存先を確認して再試行してください。",
                can_retry=True,
                tone="error",
            )

    def initialize_or_retry(self) -> None:
        if self.model.phase is RecruitFeaturePhase.LOCKED:
            self.probe()
            return
        self.initialize()

    def initialize(self) -> None:
        if self.model.busy or self._controller is not None:
            return
        try:
            paths = AppPaths.from_local_app_data()
            lock = SingleInstanceLock().acquire()
            manager = MigrationManager(paths)
            self._lock = lock
            manager.recover_crash_stage()

            if manager.is_ready():
                self._run_activation(
                    lambda: _initialize_ready_application(paths, manager),
                    "既存の求人取得データを読み込んでいます…",
                )
                return

            if paths.root.exists():
                if not self.panel.ask_adopt_existing():
                    self._cancel_initialization()
                    return
                self._run_activation(
                    lambda: _adopt_application(paths, manager),
                    "既存の求人取得データを検証しています…",
                )
                return

            candidates = manager.discover_candidates(
                executable_dir=_executable_directory(),
                cwd=Path.cwd(),
                documents_dir=_documents_directory(),
            )
            selected = candidates[0] if candidates else None
            source_kind = "automatic"
            if selected is None:
                if self.panel.ask_for_legacy_folder():
                    chosen = self.panel.choose_legacy_folder()
                    if chosen is None:
                        self._cancel_initialization()
                        return
                    selected = _normalize_selected_root(chosen)
                    source_kind = "selected"
                else:
                    self._run_activation(
                        lambda: _fresh_application(paths, manager, declined=False),
                        "求人取得データを初期化しています…",
                    )
                    return

            decision = self.panel.ask_migrate()
            if decision is None:
                self._cancel_initialization()
            elif decision:
                assert selected is not None
                self._run_activation(
                    lambda: _migrate_application(
                        paths, manager, selected, source_kind=source_kind
                    ),
                    "旧版の求人取得データを移行しています…",
                )
            else:
                self._run_activation(
                    lambda: _fresh_application(paths, manager, declined=True),
                    "求人取得データを初期化しています…",
                )
        except AlreadyRunningError:
            self._release_lock()
            self._set_phase(
                RecruitFeaturePhase.LOCKED,
                "独立版の求人整理ツールが起動しています（APP003）。\n"
                "独立版を終了してから再試行してください。",
                can_retry=True,
                tone="warning",
            )
        except RecruitJobToolError as error:
            self._bootstrap_failed(error)
        except (OSError, ValueError):
            self._bootstrap_failed(None)

    def _run_activation(
        self,
        work: Callable[[], RecruitJobApplication],
        message: str,
    ) -> None:
        self._token += 1
        token = self._token
        self._set_phase(RecruitFeaturePhase.CHECKING, message, busy=True)
        future = self.task_runner.submit(work)
        future.add_done_callback(
            lambda done: self.panel.post(lambda: self._activation_complete(token, done))
        )

    def _activation_complete(self, token: int, future: Future) -> None:
        if token != self._token:
            return
        try:
            application = future.result()
        except RecruitJobToolError as error:
            self._bootstrap_failed(error)
            return
        except Exception:
            self._bootstrap_failed(None)
            return
        controller = RecruitController(
            view=self.panel,
            file_picker=self.panel,
            application=application,
            output_folder_opener=self.panel,
            task_runner=self.task_runner,
        )
        self._controller = controller
        self.panel.bind_controller(controller)
        try:
            controller.start()
        except RecruitJobToolError as error:
            self._activation_start_failed(error)
        except Exception:
            self._activation_start_failed(None)

    def _activation_start_failed(self, error: RecruitJobToolError | None) -> None:
        self._token += 1
        self.panel.bind_controller(None)
        self._controller = None
        self._bootstrap_failed(error)

    def _controller_rendered(self, controller_model: RecruitControllerViewModel) -> None:
        phase = RecruitFeaturePhase.BUSY if controller_model.busy else RecruitFeaturePhase.READY
        if controller_model.phase is GlobalPhase.ERROR:
            phase = RecruitFeaturePhase.ERROR
        tone = "error" if controller_model.phase is GlobalPhase.ERROR else (
            "success" if controller_model.phase is GlobalPhase.SUCCESS else "normal"
        )
        self.model = RecruitFeatureViewModel(
            phase=phase,
            active_provider=controller_model.active_provider,
            providers={
                provider: provider_model.status
                for provider, provider_model in controller_model.providers.items()
            },
            selected_paths={
                provider: provider_model.selected_paths
                for provider, provider_model in controller_model.providers.items()
            },
            modes={
                provider: provider_model.mode
                for provider, provider_model in controller_model.providers.items()
            },
            status_text=controller_model.status_text,
            tone=tone,
            busy=controller_model.busy,
        )
        self.on_global_state(controller_model.busy, controller_model.status_text, tone)

    def _set_phase(
        self,
        phase: RecruitFeaturePhase,
        message: str,
        *,
        busy: bool = False,
        can_initialize: bool = False,
        can_retry: bool = False,
        tone: str = "normal",
    ) -> None:
        self.model = replace(
            self.model,
            phase=phase,
            status_text=message,
            tone=tone,
            busy=busy,
            can_initialize=can_initialize,
            can_retry=can_retry,
        )
        self.panel.set_phase(
            phase,
            message,
            can_initialize=can_initialize,
            can_retry=can_retry,
        )
        self.on_global_state(busy, message, tone)

    def _cancel_initialization(self) -> None:
        self._release_lock()
        self._set_phase(
            RecruitFeaturePhase.UNINITIALIZED,
            "初期設定をキャンセルしました。求人取得データは変更していません。",
            can_initialize=True,
        )

    def _bootstrap_failed(self, error: RecruitJobToolError | None) -> None:
        code = error.error_code if error is not None else "APP001"
        self._release_lock()
        self._set_phase(
            RecruitFeaturePhase.ERROR,
            f"求人取得機能を初期化できませんでした（{code}）。\n"
            "使い方の対処方法を確認して再試行してください。",
            can_retry=True,
            tone="error",
        )
        self.panel.dialogs.show(
            "求人取得機能を初期化できません",
            f"エラーコード: {code}\n使い方の対処方法を確認してください。",
            DialogKind.ERROR,
        )

    def forward(self, method: str, *args) -> None:
        if self._controller is not None and not self.model.busy:
            getattr(self._controller, method)(*args)

    def shutdown(self) -> None:
        self._token += 1
        self._release_lock()

    def _release_lock(self) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            try:
                lock.release()
            except RecruitJobToolError:
                pass


def _initialize_ready_application(
    paths: AppPaths, manager: MigrationManager
) -> RecruitJobApplication:
    manager.recover_crash_stage()
    if not manager.is_ready():
        raise RuntimeError("migration marker disappeared")
    paths.initialize()
    return RecruitJobApplication(paths)


def _adopt_application(paths: AppPaths, manager: MigrationManager) -> RecruitJobApplication:
    manager.adopt_existing()
    paths.initialize()
    return RecruitJobApplication(paths)


def _fresh_application(
    paths: AppPaths, manager: MigrationManager, *, declined: bool
) -> RecruitJobApplication:
    manager.create_fresh(declined=declined)
    paths.initialize()
    return RecruitJobApplication(paths)


def _migrate_application(
    paths: AppPaths,
    manager: MigrationManager,
    source: Path,
    *,
    source_kind: str,
) -> RecruitJobApplication:
    inventory = manager.inspect_source(source)
    manager.migrate(inventory, source_kind=source_kind)
    paths.initialize()
    return RecruitJobApplication(paths)


def _normalize_selected_root(path: Path) -> Path:
    selected = path.resolve(strict=True)
    if selected.name.lower() == "data" and any(
        (selected / name).exists() for name in ("work", "backup", "output")
    ):
        return selected.parent
    return selected


def _executable_directory() -> Path:
    return Path(sys.executable).resolve().parent


def _documents_directory() -> Path | None:
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    try:
        result = ctypes.WinDLL("shell32", use_last_error=True).SHGetFolderPathW(
            None, 5, None, 0, buffer
        )
    except (AttributeError, OSError):
        return None
    return Path(buffer.value) if result == 0 and buffer.value else None


def _label(provider: JobSource) -> str:
    return "JoBins" if provider is JobSource.JOBINS else "CareerBank"


__all__ = [
    "RecruitFeatureCoordinator",
    "RecruitFeaturePanel",
    "RecruitFeaturePhase",
    "RecruitFeatureViewModel",
]
