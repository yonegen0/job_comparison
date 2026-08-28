from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_aliases
from .extractor import Extractor
from .models import DocumentResult, EXTRACTED_FIELDS, FieldValue
from .naming import assign_proposed_names
from .output import MainCsvTable, prepare_main_csv_table
from .pdf_reader import (
    EncryptedPdfError,
    PdfToolReadError,
    ScannedPdfError,
    read_pdf,
    sha256_file,
)
from .transaction import ApplyError, apply_transaction, directory_lock


_FIELD_RETRY_LIMIT = 3
_RETRY_FIELD_NAMES = ("会社名", "募集ポジション", *EXTRACTED_FIELDS)


class _PdfChangedDuringRetry(RuntimeError):
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


def _mark_retry_content_change(result: DocumentResult, exc: _PdfChangedDuringRetry) -> None:
    result.error = str(exc)
    result.proposed_name = ""
    result.resolved_position = ""
    result.position_resolution = ""
    result.required_issues.clear()
    result.identity_validated = False
    result.reports.clear()
    result.add_report("ファイル", "error", result.error)


def _retry_initially_missing_fields(
    path: Path,
    result: DocumentResult,
    aliases: dict[str, list[str]],
) -> DocumentResult:
    missing_fields = tuple(
        field_name for field_name in _RETRY_FIELD_NAMES if _result_field(result, field_name).is_missing
    )
    if not missing_fields:
        return result

    baseline_sha256 = result.sha256
    baseline_profile = result.profile
    for field_name in missing_fields:
        for _ in range(_FIELD_RETRY_LIMIT):
            try:
                document = read_pdf(path)
                if document.sha256 != baseline_sha256:
                    raise _PdfChangedDuringRetry(baseline_sha256, document.sha256)
                candidate = Extractor(_copy_aliases(aliases)).extract(document)
                observed_sha256 = sha256_file(path)
                if observed_sha256 != baseline_sha256:
                    raise _PdfChangedDuringRetry(baseline_sha256, observed_sha256)
            except _PdfChangedDuringRetry as exc:
                _mark_retry_content_change(result, exc)
                return result
            except Exception:
                # A failed retry is intentionally silent. Only the final field result
                # is kept in the processing report.
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="求人PDFをローカルでCSV化し、会社名と職種中分類でリネームします。",
    )
    parser.add_argument("--input", required=True, type=Path, help="PDFが直下にある入力フォルダ")
    parser.add_argument("--apply", action="store_true", help="CSV出力とPDFリネームを実行する")
    parser.add_argument("--output-csv", type=Path, help="求人情報CSVの出力先")
    parser.add_argument("--report-csv", type=Path, help="処理レポートCSVの出力先")
    parser.add_argument("--aliases", type=Path, help="追加の見出し別名を定義したUTF-8 JSON")
    return parser


def _error_result(path: Path, message: str, profile: str, *, fatal: bool) -> DocumentResult:
    try:
        digest = sha256_file(path)
    except OSError:
        digest = ""
    result = DocumentResult(source_path=path, sha256=digest, profile=profile)
    if fatal:
        result.error = message
    result.add_report("ファイル", "error" if fatal else "warning", message)
    result.finalize_status()
    return result


def analyze(input_dir: Path, aliases_path: Path | None = None) -> list[DocumentResult]:
    aliases = load_aliases(aliases_path)
    paths = sorted(input_dir.glob("*.pdf"), key=lambda path: path.name.casefold())
    if not paths:
        raise ValueError(f"PDFが見つかりません: {input_dir}")

    results: list[DocumentResult] = []
    for path in paths:
        try:
            document = read_pdf(path)
            result = Extractor(_copy_aliases(aliases)).extract(document)
            result = _retry_initially_missing_fields(path, result, aliases)
        except ScannedPdfError as exc:
            result = _error_result(path, str(exc), "scanned", fatal=True)
        except EncryptedPdfError as exc:
            result = _error_result(path, str(exc), "encrypted", fatal=True)
        except PdfToolReadError as exc:
            result = _error_result(path, str(exc), "unreadable", fatal=True)
        except Exception as exc:  # Keep one bad PDF from hiding the other results.
            result = _error_result(path, f"予期しない解析エラー: {exc}", "error", fatal=True)
        results.append(result)

    assign_proposed_names(results, input_dir)
    for result in results:
        result.finalize_status()
    return results


def resolve_output_csv(input_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted(input_dir.glob("求人情報*.csv"), key=lambda path: path.name.casefold())
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"求人情報CSVが複数あります。--output-csv で1件を指定してください: {names}")
    return (candidates[0] if candidates else input_dir / "求人情報.csv").resolve()


def _summary(results: list[DocumentResult]) -> dict[str, int]:
    summary = {
        "成功": 0,
        "部分成功": 0,
        "改名保留": 0,
        "エラー": 0,
        "CSV出力対象": 0,
        "CSV除外": 0,
        "必須項目除外": 0,
        "リネーム対象": 0,
        "変更なし": 0,
    }
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
        if result.is_main_csv_eligible:
            summary["CSV出力対象"] += 1
        else:
            summary["CSV除外"] += 1
            if result.required_issues:
                summary["必須項目除外"] += 1
        if result.is_rename_eligible:
            if result.source_path.name == result.proposed_name:
                summary["変更なし"] += 1
            else:
                summary["リネーム対象"] += 1
    return summary


def print_preview(results: list[DocumentResult], *, applied: bool, stale_count: int = 0) -> None:
    summary = _summary(results)
    mode = "本実行結果" if applied else "事前確認（ファイル変更なし）"
    print(f"\n=== {mode} ===")
    print(f"対象PDF: {len(results)}件")
    if stale_count:
        print(f"PDF未検出の既存CSV行: {stale_count}件（行を保持）")
    print(
        " / ".join(
            [
                f"CSV出力対象: {summary['CSV出力対象']}",
                f"CSV除外: {summary['CSV除外']}（必須項目: {summary['必須項目除外']}）",
                f"リネーム対象: {summary['リネーム対象']}",
                f"変更なし: {summary['変更なし']}",
            ]
        )
    )
    print(
        " / ".join(
            [
                f"成功: {summary['成功']}",
                f"部分成功: {summary['部分成功']}",
                f"改名保留: {summary['改名保留']}",
                f"エラー: {summary['エラー']}",
            ]
        )
    )
    print("\n--- 旧名 → 新名 ---")
    for result in results:
        if not result.is_main_csv_eligible:
            target = "（CSV除外・改名なし）"
        else:
            target = result.proposed_name or "（改名保留）"
        no_change = " [変更なし]" if target == result.source_path.name else ""
        print(f"[{result.status}] {result.source_path.name}")
        print(f"  -> {target}{no_change}")
        if result.error:
            print(f"     エラー: {result.error}")
        for issue in result.required_issues:
            print(f"     {issue.target_field}: {issue.message}")
    warning_count = sum(len(result.reports) for result in results)
    print(f"\nレポート項目数: {warning_count}")


def exit_code(results: list[DocumentResult], stale_count: int = 0) -> int:
    return 0 if not stale_count and all(result.status == "成功" for result in results) else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        input_dir = args.input.resolve()
        if not input_dir.is_dir():
            parser.error(f"入力フォルダが存在しません: {input_dir}")
        aliases_path = args.aliases.resolve() if args.aliases else None
        if aliases_path and not aliases_path.is_file():
            parser.error(f"見出し別名JSONが存在しません: {aliases_path}")

        output_csv = resolve_output_csv(input_dir, args.output_csv)
        report_csv = (args.report_csv or input_dir / "処理レポート.csv").resolve()
        results = analyze(input_dir, aliases_path)
        table: MainCsvTable = prepare_main_csv_table(results, output_csv)
        print_preview(results, applied=False, stale_count=len(table.stale_rows))
        if not args.apply:
            return exit_code(results, len(table.stale_rows))

        with directory_lock(input_dir):
            table = apply_transaction(results, input_dir, output_csv, report_csv)
        print_preview(results, applied=True, stale_count=len(table.stale_rows))
        print(f"\n求人情報CSV: {output_csv}")
        print(f"処理レポートCSV: {report_csv}")
        return exit_code(results, len(table.stale_rows))
    except (ValueError, OSError, ApplyError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
