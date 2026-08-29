from __future__ import annotations

import argparse
import sys
from pathlib import Path

from job_comparison_app.errors import JobComparisonAppError

from .markdown_parser import MarkdownFormatError
from .pdf_renderer import PdfRenderError
from .service import (
    WARNING_VERDICTS,
    ReportBuildRequest,
    commit_report_build,
    prepare_report_build,
)
from .source_verifier import SourceVerificationError
from .transaction import OutputTransactionError


ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="求人比較Markdownと指定元PDFを照合し、A4・12ページの比較資料を完全ローカルで生成します。"
    )
    parser.add_argument("--input", required=True, type=Path, help="入力Markdown")
    parser.add_argument("--source-manifest", required=True, type=Path, help="10求人と元PDFの固定対応JSON")
    parser.add_argument("--output", required=True, type=Path, help="最終PDF")
    parser.add_argument("--verified-markdown", type=Path, help="検証済みMarkdown出力先")
    parser.add_argument("--verification-csv", type=Path, help="検証結果CSV出力先")
    parser.add_argument("--date", help="表紙日付の上書き（YYYY-MM-DD）")
    parser.add_argument("--keep-html", action="store_true", help="PDFと同じ場所へ中間HTMLも保存する")
    parser.add_argument("--apply", action="store_true", help="成果物を実際に生成する")
    return parser


def _warning_count(findings) -> int:
    return sum(item.verdict in WARNING_VERDICTS for item in findings)


def _print_summary(document, findings, output, *, applied: bool) -> None:
    counts: dict[str, int] = {}
    for item in findings:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    print("\n=== " + ("本実行結果" if applied else "事前確認（ファイル変更なし）") + " ===")
    print(f"入力Markdown: 解析成功 / 求人: {len(document.jobs)}件 / 元PDF: {len(document.jobs)}件")
    print(
        " / ".join(
            f"{name}: {counts.get(name, 0)}"
            for name in ("一致", "修正", "併記", "記載なし", "候補者情報未確認", "要判断", "エラー")
        )
    )
    print("ページ構成: A4縦 12ページ（比較一覧1＋求人詳細10＋優先順位1）")
    print(f"最終PDF: {output}")
    if not applied:
        print("PDF・Markdown・CSVは変更していません。")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = ReportBuildRequest(
            input_markdown=args.input,
            source_manifest=args.source_manifest,
            output_pdf=args.output,
            verified_markdown=args.verified_markdown
            or ROOT / "reports" / "求人比較資料_オフィスワーカー_検証済み.md",
            verification_csv=args.verification_csv
            or ROOT / "reports" / "求人比較資料_オフィスワーカー_検証結果.csv",
            report_date=args.date,
            keep_html=args.keep_html,
        )
        plan = prepare_report_build(request)
        _print_summary(plan.document, plan.findings, plan.request.output_pdf, applied=False)
        if not args.apply:
            return 2 if plan.warning_count else 0
        result = commit_report_build(plan)
        _print_summary(plan.document, plan.findings, plan.request.output_pdf, applied=True)
        print(f"検証済みMarkdown: {result.verified_markdown}")
        print(f"検証結果CSV: {result.verification_csv}")
        print(f"PDFページ数: {result.page_count}")
        return 2 if result.warning_count else 0
    except (
        MarkdownFormatError,
        SourceVerificationError,
        PdfRenderError,
        OutputTransactionError,
        JobComparisonAppError,
        OSError,
        ValueError,
    ) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
