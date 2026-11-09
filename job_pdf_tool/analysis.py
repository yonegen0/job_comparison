from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import load_aliases
from .extractor import Extractor
from .models import DocumentResult, EXTRACTED_FIELDS, FieldValue
from .naming import assign_proposed_names
from .providers.circus import CircusExtractor
from .pdf_reader import (
    EncryptedPdfError,
    PdfToolReadError,
    ScannedPdfError,
    read_pdf,
    sha256_file,
)


FIELD_RETRY_LIMIT = 3
RETRY_FIELD_NAMES = ("会社名", "募集ポジション", *EXTRACTED_FIELDS)


class ExtractorLike(Protocol):
    def extract(self, document): ...


class PdfChangedDuringRetry(RuntimeError):
    def __init__(self, baseline_sha256: str, observed_sha256: str):
        self.baseline_sha256 = baseline_sha256
        self.observed_sha256 = observed_sha256
        super().__init__(
            "再取得中にPDF内容が変更されました。"
            f" 初回SHA-256: {baseline_sha256} / 検出SHA-256: {observed_sha256}"
        )


def _copy_aliases(aliases: dict[str, list[str]]) -> dict[str, list[str]]:
    return {name: list(values) for name, values in aliases.items()}


def _result_field(result: DocumentResult, field_name: str) -> FieldValue:
    if field_name == "会社名":
        return result.company
    if field_name == "募集ポジション":
        return result.raw_position
    return result.fields[field_name]


def _set_result_field(result: DocumentResult, field_name: str, value: FieldValue) -> None:
    copied = FieldValue(
        value=value.value,
        classification=value.classification,
        source_heading=value.source_heading,
        page=value.page,
    )
    if field_name == "会社名":
        result.company = copied
    elif field_name == "募集ポジション":
        result.raw_position = copied
    else:
        result.fields[field_name] = copied


def _replace_field_reports(
    result: DocumentResult,
    candidate: DocumentResult,
    field_name: str,
    adopted: FieldValue,
) -> None:
    result.reports = [report for report in result.reports if report.target_field != field_name]
    candidate_reports = [
        report
        for report in candidate.reports
        if report.target_field == field_name and report.classification != "missing"
    ]
    if candidate_reports:
        for report in candidate_reports:
            result.add_report(
                report.target_field,
                report.classification,
                report.message,
                report.source_heading,
                report.page,
            )
        return
    source = adopted.source_heading or "PDF本文"
    result.add_report(
        field_name,
        adopted.classification,
        f"再取得の結果、{source}から取得しました。",
        adopted.source_heading,
        adopted.page,
    )


def _mark_retry_content_change(result: DocumentResult, exc: PdfChangedDuringRetry) -> None:
    result.error = str(exc)
    result.proposed_name = ""
    result.resolved_position = ""
    result.position_resolution = ""
    result.required_issues.clear()
    result.identity_validated = False
    result.reports.clear()
    result.add_report("ファイル", "error", result.error)


def retry_initially_missing_fields(
    path: Path,
    result: DocumentResult,
    aliases: dict[str, list[str]],
    *,
    read_pdf_func: Callable = read_pdf,
    extractor_factory: Callable[[dict[str, list[str]]], ExtractorLike] = Extractor,
    sha256_func: Callable[[Path], str] = sha256_file,
) -> DocumentResult:
    missing_fields = tuple(
        field_name for field_name in RETRY_FIELD_NAMES if _result_field(result, field_name).is_missing
    )
    if not missing_fields:
        return result
    baseline_sha256 = result.sha256
    baseline_profile = result.profile
    for field_name in missing_fields:
        for _ in range(FIELD_RETRY_LIMIT):
            try:
                document = read_pdf_func(path)
                if document.sha256 != baseline_sha256:
                    raise PdfChangedDuringRetry(baseline_sha256, document.sha256)
                candidate = extractor_factory(_copy_aliases(aliases)).extract(document)
                observed_sha256 = sha256_func(path)
                if observed_sha256 != baseline_sha256:
                    raise PdfChangedDuringRetry(baseline_sha256, observed_sha256)
            except PdfChangedDuringRetry as exc:
                _mark_retry_content_change(result, exc)
                return result
            except Exception:
                continue
            if candidate.profile != baseline_profile:
                continue
            candidate_value = _result_field(candidate, field_name)
            if candidate_value.is_missing:
                continue
            _set_result_field(result, field_name, candidate_value)
            _replace_field_reports(result, candidate, field_name, candidate_value)
            break
    return result


def _error_result(path: Path, message: str, profile: str, *, fatal: bool, sha256_func=sha256_file) -> DocumentResult:
    try:
        digest = sha256_func(path)
    except OSError:
        digest = ""
    result = DocumentResult(source_path=path, sha256=digest, profile=profile)
    if fatal:
        result.error = message
    result.add_report("ファイル", "error" if fatal else "warning", message)
    result.finalize_status()
    return result


def analyze(
    input_dir: Path,
    aliases_path: Path | None = None,
    *,
    read_pdf_func: Callable = read_pdf,
    extractor_factory: Callable[[dict[str, list[str]]], ExtractorLike] = Extractor,
    sha256_func: Callable[[Path], str] = sha256_file,
    provider: str = "jobins",
) -> list[DocumentResult]:
    if provider not in {"jobins", "circus"}:
        raise ValueError(f"未対応の媒体です: {provider}")
    if provider == "circus":
        extractor_factory = CircusExtractor
    aliases = load_aliases(aliases_path)
    paths = sorted(input_dir.glob("*.pdf"), key=lambda path: path.name.casefold())
    if not paths:
        raise ValueError(f"PDFが見つかりません: {input_dir}")
    results: list[DocumentResult] = []
    for path in paths:
        try:
            document = read_pdf_func(path)
            result = extractor_factory(_copy_aliases(aliases)).extract(document)
            if provider == "jobins":
                result = retry_initially_missing_fields(
                path,
                result,
                aliases,
                read_pdf_func=read_pdf_func,
                extractor_factory=extractor_factory,
                sha256_func=sha256_func,
                )
        except ScannedPdfError as exc:
            result = _error_result(path, str(exc), "scanned", fatal=True, sha256_func=sha256_func)
        except EncryptedPdfError as exc:
            result = _error_result(path, str(exc), "encrypted", fatal=True, sha256_func=sha256_func)
        except PdfToolReadError as exc:
            result = _error_result(path, str(exc), "unreadable", fatal=True, sha256_func=sha256_func)
        except Exception as exc:
            result = _error_result(
                path,
                f"予期しない解析エラー: {exc}",
                "error",
                fatal=True,
                sha256_func=sha256_func,
            )
        if provider == "circus":
            result.provider = "circus"
        results.append(result)
    assign_proposed_names(results, input_dir)
    for result in results:
        result.finalize_status()
    return results
