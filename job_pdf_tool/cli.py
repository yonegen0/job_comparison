from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_comparison_app.errors import JobComparisonAppError

from .analysis import (
    FIELD_RETRY_LIMIT,
    RETRY_FIELD_NAMES,
    PdfChangedDuringRetry,
    analyze as _analyze_core,
    retry_initially_missing_fields,
)
from .extractor import Extractor
from .models import DocumentResult
from .pdf_reader import read_pdf, sha256_file
from .service import (
    PdfOrganizationRequest,
    commit_pdf_organization,
    prepare_pdf_organization,
    resolve_output_csv as _resolve_output_csv,
    summarize,
)
from .transaction import ApplyError


# Compatibility names retained for existing callers and tests.
_FIELD_RETRY_LIMIT = FIELD_RETRY_LIMIT
_RETRY_FIELD_NAMES = RETRY_FIELD_NAMES
_PdfChangedDuringRetry = PdfChangedDuringRetry


def _retry_initially_missing_fields(
    path: Path,
    result: DocumentResult,
    aliases: dict[str, list[str]],
) -> DocumentResult:
    return retry_initially_missing_fields(
        path,
        result,
        aliases,
        read_pdf_func=read_pdf,
        extractor_factory=Extractor,
        sha256_func=sha256_file,
    )


def analyze(input_dir: Path, aliases_path: Path | None = None) -> list[DocumentResult]:
    return _analyze_core(
        input_dir,
        aliases_path,
        read_pdf_func=read_pdf,
        extractor_factory=Extractor,
        sha256_func=sha256_file,
    )


def resolve_output_csv(input_dir: Path, explicit: Path | None) -> Path:
    return _resolve_output_csv(input_dir, explicit)


def _summary(results: list[DocumentResult]) -> dict[str, int]:
    return summarize(results)


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
        if not args.input.resolve().is_dir():
            parser.error(f"入力フォルダが存在しません: {args.input.resolve()}")
        if args.aliases and not args.aliases.resolve().is_file():
            parser.error(f"見出し別名JSONが存在しません: {args.aliases.resolve()}")
        plan = prepare_pdf_organization(
            PdfOrganizationRequest(
                input_dir=args.input,
                aliases_path=args.aliases,
                output_csv=args.output_csv,
                report_csv=args.report_csv,
            )
        )
        print_preview(plan.results, applied=False, stale_count=len(plan.table.stale_rows))
        if not args.apply:
            return exit_code(plan.results, len(plan.table.stale_rows))
        result = commit_pdf_organization(plan)
        print_preview(plan.results, applied=True, stale_count=len(result.table.stale_rows))
        print(f"\n求人情報CSV: {plan.output_csv}")
        print(f"処理レポートCSV: {plan.report_csv}")
        return exit_code(plan.results, len(result.table.stale_rows))
    except (ValueError, OSError, ApplyError, JobComparisonAppError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
