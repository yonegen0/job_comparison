from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from job_pdf_tool.pdf_reader import sha256_file

from .html_renderer import render_html
from .markdown_parser import MarkdownFormatError, parse_markdown
from .pdf_renderer import PdfRenderError, render_pdf
from .report_builder import build_verified_markdown, write_verification_csv
from .source_verifier import SourceVerificationError, load_manifest, verify_sources
from .transaction import (
    OutputTransactionError,
    commit_outputs,
    output_lock,
    preflight_targets,
    staged_path,
)


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
    return sum(item.verdict in {"修正", "併記", "記載なし", "候補者情報未確認"} for item in findings)


def _print_summary(document, findings, output, *, applied: bool) -> None:
    counts: dict[str, int] = {}
    for item in findings:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    print("\n=== " + ("本実行結果" if applied else "事前確認（ファイル変更なし）") + " ===")
    print(f"入力Markdown: 解析成功 / 求人: {len(document.jobs)}件 / 元PDF: {len(document.jobs)}件")
    print(" / ".join(f"{name}: {counts.get(name, 0)}" for name in ("一致", "修正", "併記", "記載なし", "候補者情報未確認", "要判断", "エラー")))
    print("ページ構成: A4縦 12ページ（比較一覧1＋求人詳細10＋優先順位1）")
    print(f"最終PDF: {output}")
    if not applied:
        print("PDF・Markdown・CSVは変更していません。")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    staged_files: list[Path] = []
    try:
        input_path = args.input.resolve()
        manifest_path = args.source_manifest.resolve()
        output_pdf = args.output.resolve()
        verified_markdown = (args.verified_markdown or ROOT / "reports" / "求人比較資料_オフィスワーカー_検証済み.md").resolve()
        verification_csv = (args.verification_csv or ROOT / "reports" / "求人比較資料_オフィスワーカー_検証結果.csv").resolve()
        if not input_path.is_file():
            raise MarkdownFormatError(f"入力Markdownが存在しません: {input_path}")
        if not manifest_path.is_file():
            raise SourceVerificationError(f"元PDF対応設定が存在しません: {manifest_path}")
        manifest = load_manifest(manifest_path)
        if args.date:
            dt.date.fromisoformat(args.date)
            manifest.setdefault("report", {})["created_date"] = args.date

        input_sha = sha256_file(input_path)
        document = parse_markdown(input_path)
        findings = verify_sources(document, manifest)
        source_hashes = {job.source_pdf: job.source_sha256 for job in document.jobs if job.source_pdf}
        _print_summary(document, findings, output_pdf, applied=False)
        if any(item.verdict in {"要判断", "エラー"} for item in findings):
            raise SourceVerificationError("要判断またはエラーがあるため、最終PDFを確定しません。")
        if not args.apply:
            return 2 if _warning_count(findings) else 0

        targets = [output_pdf, verified_markdown, verification_csv]
        html_target = output_pdf.with_suffix(".html") if args.keep_html else None
        if html_target:
            targets.append(html_target)
        preflight_targets(targets)
        html_text = render_html(document)
        with output_lock(output_pdf):
            staged_pdf = staged_path(output_pdf)
            staged_markdown = staged_path(verified_markdown)
            staged_csv = staged_path(verification_csv)
            staged_files.extend([staged_pdf, staged_markdown, staged_csv])
            staged_markdown.write_text(build_verified_markdown(document), encoding="utf-8-sig", newline="\n")
            write_verification_csv(staged_csv, findings)
            page_count = render_pdf(
                html_text,
                staged_pdf,
                document.title,
                [(job.company, job.position) for job in document.jobs],
            )
            staged: dict[Path, Path] = {
                output_pdf: staged_pdf,
                verified_markdown: staged_markdown,
                verification_csv: staged_csv,
            }
            if html_target:
                staged_html = staged_path(html_target)
                staged_files.append(staged_html)
                staged_html.write_text(html_text, encoding="utf-8", newline="\n")
                staged[html_target] = staged_html
            if sha256_file(input_path) != input_sha:
                raise OutputTransactionError("処理中に入力Markdownが変更されました。")
            for source_path, digest in source_hashes.items():
                if source_path is None or sha256_file(source_path) != digest:
                    raise OutputTransactionError(f"処理中に元PDFが変更されました: {source_path}")
            commit_outputs(staged)
        _print_summary(document, findings, output_pdf, applied=True)
        print(f"検証済みMarkdown: {verified_markdown}")
        print(f"検証結果CSV: {verification_csv}")
        print(f"PDFページ数: {page_count}")
        return 2 if _warning_count(findings) else 0
    except (MarkdownFormatError, SourceVerificationError, PdfRenderError, OutputTransactionError, OSError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        for path in staged_files:
            path.unlink(missing_ok=True)
