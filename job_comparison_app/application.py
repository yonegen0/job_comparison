from __future__ import annotations

from job_comparison_report.markdown_parser import MarkdownFormatError
from job_comparison_report.models import BuildResult
from job_comparison_report.pdf_renderer import PdfRenderError, find_browser
from job_comparison_report.service import (
    ReportBuildPlan,
    ReportBuildRequest,
    commit_report_build,
    prepare_report_build,
)
from job_comparison_report.source_verifier import SourceVerificationError
from job_comparison_report.transaction import OutputTransactionError
from job_pdf_tool.service import (
    PdfOrganizationPlan,
    PdfOrganizationRequest,
    PdfOrganizationResult,
    commit_pdf_organization,
    prepare_pdf_organization,
)
from job_pdf_tool.transaction import ApplyError

from .errors import (
    BrowserUnavailableError,
    InputChangedError,
    InputValidationError,
    JobComparisonAppError,
    OutputOperationError,
    ResourceLockError,
)


class JobComparisonApplication:
    def prepare_pdf(self, request: PdfOrganizationRequest) -> PdfOrganizationPlan:
        try:
            return prepare_pdf_organization(request)
        except JobComparisonAppError:
            raise
        except (ValueError, OSError) as exc:
            raise InputValidationError(str(exc)) from exc

    def commit_pdf(self, plan: PdfOrganizationPlan) -> PdfOrganizationResult:
        try:
            return commit_pdf_organization(plan)
        except JobComparisonAppError:
            raise
        except ApplyError as exc:
            if "別の処理" in str(exc) or "ロック" in str(exc):
                raise ResourceLockError(str(exc)) from exc
            raise OutputOperationError(str(exc)) from exc
        except OSError as exc:
            raise OutputOperationError(str(exc)) from exc

    def prepare_report(self, request: ReportBuildRequest) -> ReportBuildPlan:
        try:
            return prepare_report_build(request)
        except JobComparisonAppError:
            raise
        except (MarkdownFormatError, SourceVerificationError, ValueError, OSError) as exc:
            raise InputValidationError(str(exc)) from exc

    def ensure_report_runtime(self) -> None:
        try:
            find_browser()
        except PdfRenderError as exc:
            raise BrowserUnavailableError(str(exc)) from exc

    def commit_report(self, plan: ReportBuildPlan) -> BuildResult:
        try:
            return commit_report_build(plan)
        except JobComparisonAppError:
            raise
        except PdfRenderError as exc:
            message = str(exc)
            if any(token in message for token in ("Edge", "Chrome", "ブラウザPDF生成", "タイムアウト")):
                raise BrowserUnavailableError(message) from exc
            raise OutputOperationError(message) from exc
        except OutputTransactionError as exc:
            if "実行中" in str(exc) or "ロック" in str(exc):
                raise ResourceLockError(str(exc)) from exc
            raise OutputOperationError(str(exc)) from exc
        except OSError as exc:
            raise OutputOperationError(str(exc)) from exc


__all__ = [
    "JobComparisonApplication",
    "PdfOrganizationPlan",
    "PdfOrganizationRequest",
    "PdfOrganizationResult",
    "ReportBuildPlan",
    "ReportBuildRequest",
    "BuildResult",
    "InputChangedError",
]
