from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from job_comparison_report.models import BuildResult
from job_comparison_report.service import ReportBuildPlan, ReportBuildRequest
from job_pdf_tool.service import PdfOrganizationPlan, PdfOrganizationRequest, PdfOrganizationResult
from pdf_job_exporter.models import ProgressEvent
from pdf_job_exporter.service import PdfTextExportPlan, PdfTextExportRequest, PdfTextExportResult

from ..application import JobComparisonApplication
from ..pdf_copy import PdfCopyPlan, PdfCopyResult, resolve_decisions, CopyStatus
from .pdf_copy_panel import PdfCopyPanel
from ..errors import InputChangedError, InputValidationError, JobComparisonAppError
from .export_outputs import ExportOutputConflict, inspect_export_output, resolve_numbered_export_output
from .pdf_outputs import (
    LegacyCsvMigrationChoice, OrganizationOutputConflict, OrganizationOutputPaths,
    OutputConflictChoice, discover_legacy_main_csv_candidates, inspect_output_conflict,
    resolve_numbered_output_paths, validate_legacy_csv,
)
from .recruit_feature import RecruitFeatureCoordinator, RecruitFeaturePanel
from recruit_job_tool.models import JobSource
from recruit_job_tool.gui.controller import CandidateProfileDecision


@dataclass(frozen=True, slots=True)
class ViewModel:
    copy_plan: PdfCopyPlan | None = None
    copy_result: PdfCopyResult | None = None
    busy: bool = False
    status: str = "入力を選択してください。"
    tone: str = "normal"
    pdf_plan: PdfOrganizationPlan | None = None
    export_plan: PdfTextExportPlan | None = None
    report_plan: ReportBuildPlan | None = None
    pdf_result: PdfOrganizationResult | None = None
    export_result: PdfTextExportResult | None = None
    report_result: BuildResult | None = None
    pdf_output_mode: str | None = None
    pdf_migration_source: Path | None = None
    pdf_migration_mode: str | None = None
    export_output_mode: str | None = None
    export_progress: ProgressEvent | None = None


class View(Protocol):
    copy_panel: PdfCopyPanel
    def render(self, model: ViewModel) -> None: ...
    def current_pdf_request(self) -> PdfOrganizationRequest: ...
    def request_candidate_profile(self) -> CandidateProfileDecision: ...
    def current_export_request(self) -> PdfTextExportRequest: ...
    def current_report_request(self) -> ReportBuildRequest: ...
    def ask_organization_output_conflict(self, conflict: OrganizationOutputConflict) -> OutputConflictChoice: ...
    def choose_legacy_csv(self, candidates: tuple[Path, ...]) -> Path | None: ...
    def ask_legacy_csv_migration(self, source: Path, destination: Path) -> LegacyCsvMigrationChoice: ...
    def ask_export_output_conflict(self, conflict: ExportOutputConflict) -> OutputConflictChoice: ...
    def ask_pdf_commit(self, plan: PdfOrganizationPlan) -> bool: ...
    def ask_export_commit(self, plan: PdfTextExportPlan) -> bool: ...
    def ask_report_commit(self, plan: ReportBuildPlan) -> bool: ...
    def show_info(self, title: str, message: str) -> None: ...
    def show_warning(self, title: str, message: str) -> None: ...
    def show_error(self, title: str, message: str) -> None: ...
    def post(self, callback: Callable[[], None]) -> None: ...
    def close(self) -> None: ...


class OutputOpener(Protocol):
    def open_folder(self, path: Path) -> None: ...
    def open_file(self, path: Path) -> None: ...


class TaskRunner(Protocol):
    def submit(self, callback: Callable[[], object]) -> Future: ...
    def shutdown(self) -> None: ...


class ExecutorTaskRunner:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-comparison")
    def submit(self, callback: Callable[[], object]) -> Future:
        return self._executor.submit(callback)
    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)


T = TypeVar("T")


class Controller:
    def invalidate_copy(self) -> None:
        if self.model.busy:
            return
        self.model = replace(self.model, copy_plan=None, copy_result=None,
                             status="対象PDFコピーの入力が変更されました。事前確認してください。", tone="normal")
        self.view.render(self.model)

    def preview_copy(self) -> None:
        self._prepare_copy(False)

    def apply_copy(self) -> None:
        self._prepare_copy(True)

    def _prepare_copy(self, commit: bool) -> None:
        if self.model.busy:
            return
        try:
            request = self.view.copy_panel.request()
        except InputValidationError as exc:
            self.view.show_error("入力を確認してください", str(exc))
            return
        previous = self.model.copy_plan if self.model.copy_result is None else None

        def prepare() -> PdfCopyPlan:
            plan = self.application.prepare_pdf_copy(request)
            if commit and previous is not None and previous != plan:
                raise InputChangedError("確認後に対象PDFが変更されました。事前確認してください。")
            return plan

        self.model = replace(self.model, export_progress=None)
        self._submit("対象PDFを確認しています…", prepare,
                     self._copy_apply_ready if commit else self._copy_preview_ready,
                     keep_busy=commit)

    def _copy_preview_ready(self, plan: PdfCopyPlan) -> None:
        self.model = replace(self.model, busy=False, copy_plan=plan, copy_result=None,
                             status="事前確認が完了しました。", tone="normal")
        self.view.render(self.model)

    def _copy_apply_ready(self, plan: PdfCopyPlan) -> None:
        self.model = replace(self.model, copy_plan=plan, copy_result=None)
        self.view.render(self.model)
        try:
            conflicts = [e for e in plan.entries if e.status == CopyStatus.CONFLICT]
            choices = {}
            for index, entry in enumerate(conflicts, 1):
                choice = self.view.copy_panel.ask_conflict(entry, index, len(conflicts), plan.output_dir)
                if choice == OutputConflictChoice.CANCEL:
                    self._cancel_copy()
                    return
                choices[entry.line] = choice == OutputConflictChoice.REPLACE
            decisions = resolve_decisions(plan, choices)
            if not decisions:
                self._copy_finished(PdfCopyResult(plan.output_dir, plan.entries))
                return
            if not self.view.copy_panel.ask_commit(plan, decisions):
                self._cancel_copy()
                return
        except Exception:
            self._cancel_copy()
            self.view.show_error("コピーを開始できません", "保存方法の確認に失敗しました。")
            return
        self._submit("PDFをコピーしています…",
                     lambda: self.application.commit_pdf_copy(plan, decisions),
                     self._copy_finished, already_busy=True)

    def _cancel_copy(self) -> None:
        self.model = replace(self.model, busy=False, status="コピーをキャンセルしました。", tone="normal")
        self.view.render(self.model)

    def _copy_finished(self, result: PdfCopyResult) -> None:
        self.model = replace(self.model, busy=False, copy_result=result,
                             status="コピー結果を確認してください。" if result.warning else "PDFをコピーしました。",
                             tone="warning" if result.warning else "success")
        self.view.render(self.model)

    def open_copy_output_folder(self) -> None:
        if self.model.copy_plan and not self.model.busy:
            self._open(lambda: self.output_opener.open_folder(self.model.copy_plan.output_dir))

    def __init__(self, *, view: View, application: JobComparisonApplication,
                 output_opener: OutputOpener, task_runner: TaskRunner | None = None) -> None:
        self.view, self.application, self.output_opener = view, application, output_opener
        self.task_runner = task_runner or ExecutorTaskRunner()
        self._recruit_busy = False
        recruit_panel = getattr(view, "recruit_panel", None)
        self.recruit = (
            RecruitFeatureCoordinator(
                panel=recruit_panel,
                task_runner=self.task_runner,
                on_global_state=self._recruit_state_changed,
            )
            if isinstance(recruit_panel, RecruitFeaturePanel)
            else None
        )
        self.model = ViewModel()
        self._operation_token = 0
        self._pdf_choice: OutputConflictChoice | None = None
        self._pdf_choice_key: tuple[Path, Path | None, Path, bool, str] | None = None
        self._migration_choice: LegacyCsvMigrationChoice | None = None
        self._migration_source: Path | None = None
        self._migration_key: tuple[Path, Path] | None = None
        self._pending_pdf_output_mode: str | None = None
        self._pending_migration_mode: str | None = None
        self._export_choice: OutputConflictChoice | None = None
        self._export_choice_key: tuple[Path, Path] | None = None
        self._pending_export_output_mode: str | None = None

    def start(self) -> None:
        self.view.render(self.model)
        if self.recruit is not None:
            self.view.post(self.recruit.probe)

    def _recruit_state_changed(self, busy: bool, status: str, tone: str) -> None:
        was_busy = self._recruit_busy
        self._recruit_busy = busy
        if busy or was_busy or not self.model.busy:
            self.model = replace(self.model, busy=busy, status=status, tone=tone)
            self.view.render(self.model)

    def recruit_action(self, action: str) -> None:
        if self.recruit is None:
            return
        if action == "select_jobins":
            self.recruit.forward("select_tab", JobSource.JOBINS)
        elif action == "select_careerbank":
            self.recruit.forward("select_tab", JobSource.CAREERBANK)
        else:
            self.recruit.forward(action)

    def invalidate_pdf(self) -> None:
        if self.model.busy: return
        self._clear_pdf_selections()
        self.model = replace(self.model, pdf_plan=None, pdf_result=None, pdf_output_mode=None,
            pdf_migration_source=None, pdf_migration_mode=None,
            status="求人PDF整理の入力が変更されています。事前確認してください。", tone="normal")
        self.view.render(self.model)

    def invalidate_export(self) -> None:
        if self.model.busy: return
        self._export_choice = None; self._export_choice_key = None
        self.model = replace(self.model, export_plan=None, export_result=None,
            export_output_mode=None, export_progress=None,
            status="求人PDF全文統合テキスト化の入力が変更されています。事前確認してください。", tone="normal")
        self.view.render(self.model)

    def invalidate_report(self) -> None:
        if self.model.busy: return
        self.model = replace(self.model, report_plan=None, report_result=None,
            status="比較資料PDF生成の入力が変更されています。事前確認してください。", tone="normal")
        self.view.render(self.model)

    def preview_pdf(self) -> None: self._start_pdf_prepare(False)
    def apply_pdf(self) -> None: self._start_pdf_prepare(True)

    def _start_pdf_prepare(self, for_commit: bool) -> None:
        if self.model.busy:
            return
        try:
            request = self.view.current_pdf_request()
            if not request.input_dir.is_dir():
                raise InputValidationError("PDFフォルダーが存在しません。")
            if request.job_info_json is None or request.report_json is None:
                raise InputValidationError("JSON出力先を解決できませんでした。")
            request = replace(request, candidate_profile=None)
            paths = OrganizationOutputPaths(request.job_info_json.parent.resolve(strict=False),
                request.job_info_json.resolve(strict=False), request.report_json.resolve(strict=False), request.comparison_guide_path)
            conflict = inspect_output_conflict(paths)
        except JobComparisonAppError as exc:
            self._reject_start("PDF", exc); return
        except (OSError, ValueError) as exc:
            self._reject_start("PDF", InputValidationError(str(exc))); return
        if for_commit and request.comparison_guide_path is not None:
            decision = self.view.request_candidate_profile()
            if not decision.proceed:
                self._cancel_pdf("転職者条件を確認してください。本実行を中止しました。")
                return
            request = replace(request, candidate_profile=decision.profile)
        aliases = request.aliases_path.resolve(strict=False) if request.aliases_path else None
        key = (request.input_dir.resolve(strict=False), aliases, paths.output_dir, paths.comparison_guide_path is not None, request.provider)
        if key != self._pdf_choice_key:
            self._clear_pdf_selections()
            self._pdf_choice_key = key
        choice = self._pdf_choice
        if conflict.existing_paths and choice is None:
            choice = self.view.ask_organization_output_conflict(conflict)
            if choice is OutputConflictChoice.CANCEL:
                self._cancel_pdf("JSON出力の選択をキャンセルしました。"); return
            self._pdf_choice, self._pdf_choice_key = choice, key
        if choice is OutputConflictChoice.SAVE_AS_NEW:
            output_mode, require_new = "別名で新規保存", True
        elif choice is OutputConflictChoice.REPLACE:
            output_mode, require_new = "既存JSONを更新", False
        else:
            output_mode, require_new = "新規保存", True
        import_csv: Path | None = None
        migration_mode = "移行なし"
        if not paths.job_info_json.exists() and choice is not OutputConflictChoice.SAVE_AS_NEW:
            migration_key = (request.input_dir.resolve(strict=False), paths.output_dir, request.provider)
            if migration_key != self._migration_key:
                self._migration_choice = None; self._migration_source = None; self._migration_key = migration_key
            candidates = discover_legacy_main_csv_candidates(request.input_dir, paths.output_dir)
            source = self._migration_source if self._migration_source in candidates else (
                candidates[0] if len(candidates) == 1 else None
            )
            if len(candidates) > 1:
                source = source or self.view.choose_legacy_csv(candidates)
                if source is None:
                    self._cancel_pdf("旧求人情報CSVの選択をキャンセルしました。"); return
                source = validate_legacy_csv(source)
                if source not in {candidate.resolve(strict=False) for candidate in candidates}:
                    self._reject_start("PDF", InputValidationError("候補一覧にないCSVは移行元に指定できません。")); return
            if source is not None:
                migration_choice = self._migration_choice if self._migration_source == source else None
                if migration_choice is None:
                    migration_choice = self.view.ask_legacy_csv_migration(source, paths.job_info_json)
                    if migration_choice is LegacyCsvMigrationChoice.CANCEL:
                        self._cancel_pdf("旧求人情報CSVの移行選択をキャンセルしました。"); return
                    self._migration_choice, self._migration_source = migration_choice, source
                if migration_choice is LegacyCsvMigrationChoice.IMPORT:
                    import_csv, migration_mode = source, "旧CSVから取り込み"
                else: migration_mode = "新規作成（旧CSVを使用しない）"
        self._pending_pdf_output_mode, self._pending_migration_mode = output_mode, migration_mode
        self.model = replace(self.model, export_progress=None)
        def prepare() -> PdfOrganizationPlan:
            final = request
            if choice is OutputConflictChoice.SAVE_AS_NEW:
                numbered = resolve_numbered_output_paths(paths)
                final = replace(final, job_info_json=numbered.job_info_json, report_json=numbered.report_json,
                                comparison_guide_path=numbered.comparison_guide_path)
            final = replace(final, import_main_csv=import_csv)
            if require_new and any(path and path.exists() for path in (final.job_info_json, final.report_json, final.comparison_guide_path)):
                raise InputChangedError("JSON出力先が確認中に変更されました。もう一度事前確認してください。")
            plan = self.application.prepare_pdf(final)
            targets = {path.resolve() for path in (plan.job_info_json, plan.report_json, plan.comparison_guide_path) if path is not None}
            if require_new and any(fp.path in targets and fp.exists for fp in plan.file_fingerprints):
                raise InputChangedError("JSON出力先が確認中に変更されました。もう一度事前確認してください。")
            return plan
        self._submit("本実行前に求人PDFを解析しています…" if for_commit else "求人PDFを解析しています…",
            prepare, self._pdf_apply_ready if for_commit else self._pdf_preview_ready,
            keep_busy=for_commit, on_error=self._clear_pdf_selections)

    def _clear_pdf_selections(self) -> None:
        self._pdf_choice = None; self._pdf_choice_key = None
        self._migration_choice = None; self._migration_source = None; self._migration_key = None

    def _cancel_pdf(self, status: str) -> None:
        self.model = replace(self.model, pdf_plan=None, pdf_result=None, pdf_output_mode=None,
            pdf_migration_source=None, pdf_migration_mode=None, status=status, tone="normal")
        self.view.render(self.model)

    def preview_export(self) -> None: self._start_export_prepare(False)
    def apply_export(self) -> None: self._start_export_prepare(True)

    def _start_export_prepare(self, for_commit: bool) -> None:
        try:
            request = self.view.current_export_request(); conflict = inspect_export_output(request.output_markdown)
        except JobComparisonAppError as exc:
            self._reject_start("Export", exc); return
        except (OSError, ValueError) as exc:
            self._reject_start("Export", InputValidationError(str(exc))); return
        key = (request.input_dir.resolve(strict=False), conflict.base_path)
        if key != self._export_choice_key: self._export_choice = None; self._export_choice_key = None
        choice = self._export_choice
        if conflict.existing_path and choice is None:
            choice = self.view.ask_export_output_conflict(conflict)
            if choice is OutputConflictChoice.CANCEL:
                self.model = replace(self.model, export_plan=None, export_result=None,
                    export_output_mode=None, status="Markdown出力の選択をキャンセルしました。", tone="normal")
                self.view.render(self.model); return
            self._export_choice, self._export_choice_key = choice, key
        if choice is OutputConflictChoice.SAVE_AS_NEW: mode, require_new = "別名で新規保存", True
        elif choice is OutputConflictChoice.REPLACE: mode, require_new = "既存Markdownを置換", False
        else: mode, require_new = "新規保存", True
        self._pending_export_output_mode = mode
        self.model = replace(self.model, export_progress=None)
        token = self._operation_token + 1
        def progress(event: ProgressEvent) -> None:
            self.view.post(lambda: self._accept_export_progress(token, event))
        def prepare() -> PdfTextExportPlan:
            final = replace(request, output_markdown=resolve_numbered_export_output(conflict.base_path)) if choice is OutputConflictChoice.SAVE_AS_NEW else request
            if require_new and final.output_markdown.exists():
                raise InputChangedError("Markdown出力先が確認中に変更されました。")
            plan = self.application.prepare_pdf_text_export(final, progress)
            if require_new and plan.output_fingerprint.exists:
                raise InputChangedError("Markdown出力先が確認中に変更されました。")
            return plan
        self._submit("本実行前にPDF全文を抽出しています…" if for_commit else "PDF全文を抽出しています…",
            prepare, self._export_apply_ready if for_commit else self._export_preview_ready,
            keep_busy=for_commit)

    def _accept_export_progress(self, token: int, event: ProgressEvent) -> None:
        if token != self._operation_token or not self.model.busy: return
        self.model = replace(self.model, export_progress=event, status=self._progress_text(event)); self.view.render(self.model)

    @staticmethod
    def _progress_text(event: ProgressEvent) -> str:
        if event.stage == "page":
            return f"{event.document_index} / {event.document_count} PDF　{event.filename}　ページ {event.page_number} / {event.page_count}"
        return {"discovered": f"対象PDFを確認しました（{event.document_count}件）",
            "document": f"{event.document_index} / {event.document_count} PDF　{event.filename}",
            "building": "Markdownを構築・検証しています…", "committing": "Markdownを確定しています…",
            "complete": "Markdownの生成が完了しました。"}.get(event.stage, "PDF全文を処理しています…")

    def _reject_start(self, kind: str, error: JobComparisonAppError) -> None:
        changes = {"status": error.user_message, "tone": "error"}
        if kind == "PDF": changes.update(pdf_plan=None, pdf_result=None)
        else: changes.update(export_plan=None, export_result=None)
        self.model = replace(self.model, **changes); self.view.render(self.model)
        self.view.show_error("処理を開始できません", str(error))

    def preview_report(self) -> None:
        self.model = replace(self.model, export_progress=None)
        request = self.view.current_report_request()
        self._submit("Markdownと元PDFを照合しています…", lambda: self.application.prepare_report(request), self._report_preview_ready)

    def build_report(self) -> None:
        self.model = replace(self.model, export_progress=None)
        request = self.view.current_report_request()
        def prepare() -> ReportBuildPlan:
            plan = self.application.prepare_report(request); self.application.ensure_report_runtime(); return plan
        self._submit("生成前にMarkdownと元PDFを再確認しています…", prepare, self._report_apply_ready, keep_busy=True)

    def _pdf_preview_ready(self, plan: PdfOrganizationPlan) -> None:
        self.model = replace(self.model, busy=False, status="事前確認が完了しました。ファイルは変更していません。",
            tone="warning" if plan.level.value == "warning" else "success", pdf_plan=plan, pdf_result=None,
            pdf_output_mode=self._pending_pdf_output_mode, pdf_migration_source=plan.request.import_main_csv,
            pdf_migration_mode=self._pending_migration_mode); self.view.render(self.model)

    def _pdf_apply_ready(self, plan: PdfOrganizationPlan) -> None:
        self.model = replace(self.model, pdf_plan=plan, pdf_result=None,
            pdf_output_mode=self._pending_pdf_output_mode, pdf_migration_source=plan.request.import_main_csv,
            pdf_migration_mode=self._pending_migration_mode); self.view.render(self.model)
        if not self.view.ask_pdf_commit(plan):
            self.model = replace(self.model, busy=False, status="本実行をキャンセルしました。", tone="normal"); self.view.render(self.model); return
        self._submit("PDF名・JSON・比較指示書を確定しています…", lambda: self.application.commit_pdf(plan), self._pdf_commit_finished, already_busy=True)

    def _pdf_commit_finished(self, result: PdfOrganizationResult) -> None:
        invalidate = bool(self.model.export_plan and self.model.export_plan.request.input_dir.resolve() == result.plan.request.input_dir.resolve())
        warning = result.level.value == "warning"
        self.model = replace(self.model, busy=False, status="警告付きで求人PDF整理が完了しました。" if warning else "求人PDF整理が完了しました。",
            tone="warning" if warning else "success", pdf_result=result,
            export_plan=None if invalidate else self.model.export_plan, export_result=None if invalidate else self.model.export_result)
        self.view.render(self.model)
        count = sum(result.plan.summary.get(n, 0) for n in ("部分成功", "改名保留", "エラー")) + len(result.job_info.stale_jobs)
        message = (f"保存方法: {self.model.pdf_output_mode or '新規保存'}\n出力フォルダー: {result.plan.job_info_json.parent}\n"
            f"求人情報JSON: {result.plan.job_info_json.name}\n処理レポートJSON: {result.plan.report_json.name}\n"
            f"旧CSV移行: {self.model.pdf_migration_mode or '移行なし'}\n警告: {count}件")
        if result.plan.comparison_guide_path is not None:
            message += (
                f"\n比較指示書: {result.plan.comparison_guide_path.name}\n"
                f"比較方法: {'転職者条件付き' if result.plan.candidate_applied else '一般比較'}\n\n"
                "次に「求人PDF全文統合テキスト化」で整理後のPDFから全文Markdownを生成してください。\n"
                "求人情報JSON・PDF全文Markdown・比較指示書をAIへ添付し、生成された比較Markdownを比較資料PDF生成で使用します。"
            )
            if result.plan.summary.get("JSON出力対象", 0) == 0 and not result.job_info.stale_jobs:
                message += "\n比較対象の求人はありません。処理結果を確認してください。"
        (self.view.show_warning if warning else self.view.show_info)("求人PDF整理が完了しました", message)

    def _export_preview_ready(self, plan: PdfTextExportPlan) -> None:
        self.model = replace(self.model, busy=False, status="事前確認が完了しました。Markdownは作成していません。",
            tone="warning" if plan.level.value == "warning" else "success", export_plan=plan, export_result=None,
            export_output_mode=self._pending_export_output_mode); self.view.render(self.model)

    def _export_apply_ready(self, plan: PdfTextExportPlan) -> None:
        self.model = replace(self.model, export_plan=plan, export_result=None, export_output_mode=self._pending_export_output_mode); self.view.render(self.model)
        if not self.view.ask_export_commit(plan):
            self.model = replace(self.model, busy=False, status="Markdown生成をキャンセルしました。", tone="normal"); self.view.render(self.model); return
        token = self._operation_token + 1
        def progress(event: ProgressEvent) -> None: self.view.post(lambda: self._accept_export_progress(token, event))
        self._submit("Markdownを確定しています…", lambda: self.application.commit_pdf_text_export(plan, progress), self._export_commit_finished, already_busy=True)

    def _export_commit_finished(self, result: PdfTextExportResult) -> None:
        warning = result.level.value == "warning"
        self.model = replace(self.model, busy=False, status="警告付きでMarkdownを生成しました。" if warning else "Markdownを生成しました。",
            tone="warning" if warning else "success", export_result=result); self.view.render(self.model)
        message = (f"保存方法: {self.model.export_output_mode or '新規保存'}\n出力フォルダー: {result.output_path.parent}\n"
            f"Markdown: {result.output_path.name}\nPDF: {result.document_count}件\nページ: {result.page_count}\n"
            f"抽出文字: {result.character_count:,}\n出力サイズ: {result.byte_count:,}バイト\n警告: {len(result.warnings)}件")
        (self.view.show_warning if warning else self.view.show_info)("PDF全文統合テキスト化が完了しました", message)

    def _report_preview_ready(self, plan: ReportBuildPlan) -> None:
        self.model = replace(self.model, busy=False, status="事前確認が完了しました。成果物は生成していません。",
            tone="warning" if plan.level.value == "warning" else "success", report_plan=plan, report_result=None); self.view.render(self.model)

    def _report_apply_ready(self, plan: ReportBuildPlan) -> None:
        self.model = replace(self.model, report_plan=plan, report_result=None); self.view.render(self.model)
        if not self.view.ask_report_commit(plan):
            self.model = replace(self.model, busy=False, status="PDF生成をキャンセルしました。", tone="normal"); self.view.render(self.model); return
        self._submit("A4・12ページの比較資料PDFを生成しています…", lambda: self.application.commit_report(plan), self._report_commit_finished, already_busy=True)

    def _report_commit_finished(self, result: BuildResult) -> None:
        warning = result.warning_count > 0
        self.model = replace(self.model, busy=False, status="警告付きで比較資料PDFを生成しました。" if warning else "比較資料PDFを生成しました。",
            tone="warning" if warning else "success", report_result=result); self.view.render(self.model)
        message = f"PDF: {result.output_pdf.name if result.output_pdf else ''}\nページ数: {result.page_count}\n警告: {result.warning_count}件"
        (self.view.show_warning if warning else self.view.show_info)("比較資料PDFを生成しました", message)

    def open_pdf_output_folder(self) -> None:
        if self.model.pdf_plan: self._open(lambda: self.output_opener.open_folder(self.model.pdf_plan.job_info_json.parent))
    def open_export_output_folder(self) -> None:
        if self.model.export_plan: self._open(lambda: self.output_opener.open_folder(self.model.export_plan.request.output_markdown.parent))
    def open_export_markdown(self) -> None:
        if self.model.export_result: self._open(lambda: self.output_opener.open_file(self.model.export_result.output_path))
    def open_report_output_folder(self) -> None:
        if self.model.report_plan: self._open(lambda: self.output_opener.open_folder(self.model.report_plan.request.output_pdf.parent))
    def open_report_pdf(self) -> None:
        if self.model.report_result and self.model.report_result.output_pdf: self._open(lambda: self.output_opener.open_file(self.model.report_result.output_pdf))

    def _open(self, callback: Callable[[], None]) -> None:
        try: callback()
        except OSError as exc: self.view.show_error("開けませんでした", f"ファイルまたはフォルダーを開けませんでした。\n{exc}")

    def request_close(self) -> None:
        if self.model.busy:
            self.view.show_warning("処理中です", "処理が完了してから終了してください。"); return
        if self.recruit is not None:
            self.recruit.shutdown()
        self.task_runner.shutdown(); self.view.close()

    def _submit(self, status: str, callback: Callable[[], T], on_success: Callable[[T], None], *,
                keep_busy: bool = False, already_busy: bool = False, on_error: Callable[[], None] | None = None) -> None:
        if self.model.busy and not already_busy: return
        self._operation_token += 1; token = self._operation_token
        self.model = replace(self.model, busy=True, status=status, tone="normal"); self.view.render(self.model)
        future = self.task_runner.submit(callback)
        future.add_done_callback(lambda done: self.view.post(lambda: self._complete(token, done, on_success, keep_busy, on_error)))

    def _complete(self, token: int, future: Future, on_success: Callable[[T], None], keep_busy: bool,
                  on_error: Callable[[], None] | None) -> None:
        if token != self._operation_token: return
        try: result = future.result()
        except JobComparisonAppError as exc:
            if on_error: on_error()
            self.model = replace(self.model, busy=False, status=exc.user_message, tone="error"); self.view.render(self.model)
            self.view.show_error("処理を完了できませんでした", str(exc)); return
        except Exception:
            if on_error: on_error()
            self.model = replace(self.model, busy=False, status="予期しないエラーが発生しました。", tone="error"); self.view.render(self.model)
            self.view.show_error("処理を完了できませんでした", "予期しないエラーが発生しました（エラーコード: APP001）。"); return
        if not keep_busy: self.model = replace(self.model, busy=False)
        on_success(result)


__all__ = ["Controller", "ExecutorTaskRunner", "ViewModel"]
