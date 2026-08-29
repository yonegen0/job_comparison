from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from job_comparison_report.models import BuildResult
from job_comparison_report.service import ReportBuildPlan, ReportBuildRequest
from job_pdf_tool.service import PdfOrganizationPlan, PdfOrganizationRequest, PdfOrganizationResult

from ..application import JobComparisonApplication
from ..errors import InputChangedError, InputValidationError, JobComparisonAppError
from .pdf_outputs import (
    PdfCsvConflict,
    PdfCsvConflictChoice,
    PdfCsvOutputPaths,
    inspect_csv_conflict,
    resolve_numbered_csv_paths,
)


@dataclass(frozen=True, slots=True)
class ViewModel:
    busy: bool = False
    status: str = "入力を選択してください。"
    tone: str = "normal"
    pdf_plan: PdfOrganizationPlan | None = None
    report_plan: ReportBuildPlan | None = None
    pdf_result: PdfOrganizationResult | None = None
    report_result: BuildResult | None = None
    pdf_output_mode: str | None = None


class View(Protocol):
    def render(self, model: ViewModel) -> None: ...
    def current_pdf_request(self) -> PdfOrganizationRequest: ...
    def current_report_request(self) -> ReportBuildRequest: ...
    def ask_pdf_csv_conflict(self, conflict: PdfCsvConflict) -> PdfCsvConflictChoice: ...
    def ask_pdf_commit(self, plan: PdfOrganizationPlan) -> bool: ...
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
    def __init__(
        self,
        *,
        view: View,
        application: JobComparisonApplication,
        output_opener: OutputOpener,
        task_runner: TaskRunner | None = None,
    ) -> None:
        self.view = view
        self.application = application
        self.output_opener = output_opener
        self.task_runner = task_runner or ExecutorTaskRunner()
        self.model = ViewModel()
        self._operation_token = 0
        self._pdf_csv_choice: PdfCsvConflictChoice | None = None
        self._pdf_csv_choice_key: tuple[Path, Path | None, Path] | None = None
        self._pending_pdf_output_mode: str | None = None

    def start(self) -> None:
        self.view.render(self.model)

    def invalidate_pdf(self) -> None:
        if self.model.busy:
            return
        self._pdf_csv_choice = None
        self._pdf_csv_choice_key = None
        self._pending_pdf_output_mode = None
        self.model = replace(
            self.model,
            pdf_plan=None,
            pdf_result=None,
            pdf_output_mode=None,
            status="求人PDF整理の入力が変更されています。事前確認してください。",
            tone="normal",
        )
        self.view.render(self.model)

    def invalidate_report(self) -> None:
        if self.model.busy:
            return
        self.model = replace(
            self.model,
            report_plan=None,
            report_result=None,
            status="比較資料PDF生成の入力が変更されています。事前確認してください。",
            tone="normal",
        )
        self.view.render(self.model)

    def preview_pdf(self) -> None:
        self._start_pdf_prepare(for_commit=False)

    def apply_pdf(self) -> None:
        self._start_pdf_prepare(for_commit=True)

    def _start_pdf_prepare(self, *, for_commit: bool) -> None:
        try:
            request = self.view.current_pdf_request()
            if request.output_csv is None or request.report_csv is None:
                raise InputValidationError("CSV出力先を解決できませんでした。")
            paths = PdfCsvOutputPaths(
                request.output_csv.parent.resolve(strict=False),
                request.output_csv.resolve(strict=False),
                request.report_csv.resolve(strict=False),
            )
            conflict = inspect_csv_conflict(paths)
        except JobComparisonAppError as exc:
            self._reject_pdf_start(exc)
            return
        except (OSError, ValueError) as exc:
            self._reject_pdf_start(InputValidationError(str(exc)))
            return

        aliases = request.aliases_path.resolve(strict=False) if request.aliases_path else None
        choice_key = (
            request.input_dir.resolve(strict=False),
            aliases,
            paths.output_dir,
        )
        if choice_key != self._pdf_csv_choice_key:
            self._pdf_csv_choice = None
            self._pdf_csv_choice_key = None

        choice = self._pdf_csv_choice
        if conflict.existing_paths and choice is None:
            choice = self.view.ask_pdf_csv_conflict(conflict)
            if choice is PdfCsvConflictChoice.CANCEL:
                self.model = replace(
                    self.model,
                    pdf_plan=None,
                    pdf_result=None,
                    pdf_output_mode=None,
                    status="CSV出力の選択をキャンセルしました。",
                    tone="normal",
                )
                self.view.render(self.model)
                return
            self._pdf_csv_choice = choice
            self._pdf_csv_choice_key = choice_key

        if choice is PdfCsvConflictChoice.SAVE_AS_NEW:
            output_mode = "別名で保存"
            require_new = True
        elif choice is PdfCsvConflictChoice.REPLACE:
            output_mode = "既存CSVを更新" if conflict.existing_paths else "新規保存"
            require_new = not conflict.existing_paths
        else:
            output_mode = "新規保存"
            require_new = True

        self._pending_pdf_output_mode = output_mode

        def prepare() -> PdfOrganizationPlan:
            final_request = request
            if choice is PdfCsvConflictChoice.SAVE_AS_NEW:
                numbered = resolve_numbered_csv_paths(paths)
                final_request = replace(
                    request,
                    output_csv=numbered.output_csv,
                    report_csv=numbered.report_csv,
                )
            targets = (final_request.output_csv, final_request.report_csv)
            if require_new and any(path is not None and path.exists() for path in targets):
                raise InputChangedError(
                    "CSV出力先が確認中に変更されました。もう一度事前確認してください。"
                )
            plan = self.application.prepare_pdf(final_request)
            if require_new:
                expected = {plan.output_csv.resolve(), plan.report_csv.resolve()}
                if any(item.path in expected and item.exists for item in plan.file_fingerprints):
                    raise InputChangedError(
                        "CSV出力先が確認中に変更されました。もう一度事前確認してください。"
                    )
            return plan

        self._submit(
            "本実行前に求人PDFを再解析しています…" if for_commit else "求人PDFを解析しています…",
            prepare,
            self._pdf_apply_ready if for_commit else self._pdf_preview_ready,
            keep_busy=for_commit,
            on_error=self._clear_pdf_csv_choice,
        )

    def _clear_pdf_csv_choice(self) -> None:
        """失敗後の再試行で、置換・別名保存を選び直せるようにする。"""
        self._pdf_csv_choice = None
        self._pdf_csv_choice_key = None

    def _reject_pdf_start(self, error: JobComparisonAppError) -> None:
        self.model = replace(
            self.model,
            pdf_plan=None,
            pdf_result=None,
            pdf_output_mode=None,
            status=error.user_message,
            tone="error",
        )
        self.view.render(self.model)
        self.view.show_error("処理を開始できません", str(error))

    def preview_report(self) -> None:
        request = self.view.current_report_request()
        self._submit(
            "Markdownと元PDFを照合しています…",
            lambda: self.application.prepare_report(request),
            self._report_preview_ready,
        )

    def build_report(self) -> None:
        request = self.view.current_report_request()

        def prepare() -> ReportBuildPlan:
            plan = self.application.prepare_report(request)
            self.application.ensure_report_runtime()
            return plan

        self._submit(
            "生成前にMarkdownと元PDFを再確認しています…",
            prepare,
            self._report_apply_ready,
            keep_busy=True,
        )

    def _pdf_preview_ready(self, plan: PdfOrganizationPlan) -> None:
        tone = "warning" if plan.level.value == "warning" else "success"
        self.model = replace(
            self.model,
            busy=False,
            status="事前確認が完了しました。ファイルは変更していません。",
            tone=tone,
            pdf_plan=plan,
            pdf_result=None,
            pdf_output_mode=self._pending_pdf_output_mode,
        )
        self.view.render(self.model)

    def _pdf_apply_ready(self, plan: PdfOrganizationPlan) -> None:
        self.model = replace(
            self.model,
            pdf_plan=plan,
            pdf_result=None,
            pdf_output_mode=self._pending_pdf_output_mode,
        )
        self.view.render(self.model)
        if not self.view.ask_pdf_commit(plan):
            self.model = replace(self.model, busy=False, status="本実行をキャンセルしました。", tone="normal")
            self.view.render(self.model)
            return
        self._submit(
            "PDF名とCSVを確定しています…",
            lambda: self.application.commit_pdf(plan),
            self._pdf_commit_finished,
            already_busy=True,
        )

    def _pdf_commit_finished(self, result: PdfOrganizationResult) -> None:
        warning = result.level.value == "warning"
        self.model = replace(
            self.model,
            busy=False,
            status="警告付きで求人PDF整理が完了しました。" if warning else "求人PDF整理が完了しました。",
            tone="warning" if warning else "success",
            pdf_result=result,
        )
        self.view.render(self.model)
        message = (
            f"保存方法: {self.model.pdf_output_mode or '新規保存'}\n"
            f"出力フォルダー: {result.plan.output_csv.parent}\n"
            f"求人情報CSV: {result.plan.output_csv.name}\n"
            f"処理レポートCSV: {result.plan.report_csv.name}\n"
            f"警告: {sum(result.plan.summary.get(name, 0) for name in ('部分成功', '改名保留', 'エラー')) + len(result.table.stale_rows)}件"
        )
        if warning:
            self.view.show_warning("求人PDF整理が完了しました", message)
        else:
            self.view.show_info("求人PDF整理が完了しました", message)

    def _report_preview_ready(self, plan: ReportBuildPlan) -> None:
        tone = "warning" if plan.level.value == "warning" else "success"
        self.model = replace(
            self.model,
            busy=False,
            status="事前確認が完了しました。成果物は生成していません。",
            tone=tone,
            report_plan=plan,
            report_result=None,
        )
        self.view.render(self.model)

    def _report_apply_ready(self, plan: ReportBuildPlan) -> None:
        self.model = replace(self.model, report_plan=plan, report_result=None)
        self.view.render(self.model)
        if not self.view.ask_report_commit(plan):
            self.model = replace(self.model, busy=False, status="PDF生成をキャンセルしました。", tone="normal")
            self.view.render(self.model)
            return
        self._submit(
            "A4・12ページの比較資料PDFを生成しています…",
            lambda: self.application.commit_report(plan),
            self._report_commit_finished,
            already_busy=True,
        )

    def _report_commit_finished(self, result: BuildResult) -> None:
        warning = result.warning_count > 0
        self.model = replace(
            self.model,
            busy=False,
            status="警告付きで比較資料PDFを生成しました。" if warning else "比較資料PDFを生成しました。",
            tone="warning" if warning else "success",
            report_result=result,
        )
        self.view.render(self.model)
        message = f"PDF: {result.output_pdf.name if result.output_pdf else ''}\nページ数: {result.page_count}\n警告: {result.warning_count}件"
        if warning:
            self.view.show_warning("比較資料PDFを生成しました", message)
        else:
            self.view.show_info("比較資料PDFを生成しました", message)

    def open_pdf_output_folder(self) -> None:
        plan = self.model.pdf_plan
        if plan:
            self._open(lambda: self.output_opener.open_folder(plan.output_csv.parent))

    def open_report_output_folder(self) -> None:
        plan = self.model.report_plan
        if plan:
            self._open(lambda: self.output_opener.open_folder(plan.request.output_pdf.parent))

    def open_report_pdf(self) -> None:
        result = self.model.report_result
        if result and result.output_pdf:
            self._open(lambda: self.output_opener.open_file(result.output_pdf))

    def _open(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except OSError as exc:
            self.view.show_error("開けませんでした", f"ファイルまたはフォルダを開けませんでした。\n{exc}")

    def request_close(self) -> None:
        if self.model.busy:
            self.view.show_warning("処理中です", "処理が完了してから終了してください。")
            return
        self.task_runner.shutdown()
        self.view.close()

    def _submit(
        self,
        status: str,
        callback: Callable[[], T],
        on_success: Callable[[T], None],
        *,
        keep_busy: bool = False,
        already_busy: bool = False,
        on_error: Callable[[], None] | None = None,
    ) -> None:
        if self.model.busy and not already_busy:
            return
        self._operation_token += 1
        token = self._operation_token
        self.model = replace(self.model, busy=True, status=status, tone="normal")
        self.view.render(self.model)
        future = self.task_runner.submit(callback)

        def completed(done: Future) -> None:
            self.view.post(lambda: self._complete(token, done, on_success, keep_busy, on_error))

        future.add_done_callback(completed)

    def _complete(
        self,
        token: int,
        future: Future,
        on_success: Callable[[T], None],
        keep_busy: bool,
        on_error: Callable[[], None] | None,
    ) -> None:
        if token != self._operation_token:
            return
        try:
            result = future.result()
        except JobComparisonAppError as exc:
            if on_error is not None:
                on_error()
            self.model = replace(self.model, busy=False, status=exc.user_message, tone="error")
            self.view.render(self.model)
            self.view.show_error("処理を完了できませんでした", str(exc))
            return
        except Exception:
            if on_error is not None:
                on_error()
            self.model = replace(
                self.model,
                busy=False,
                status="予期しないエラーが発生しました。",
                tone="error",
            )
            self.view.render(self.model)
            self.view.show_error(
                "処理を完了できませんでした",
                "予期しないエラーが発生しました（エラーコード: APP001）。",
            )
            return
        if not keep_busy:
            self.model = replace(self.model, busy=False)
        on_success(result)


__all__ = ["Controller", "ExecutorTaskRunner", "ViewModel"]
