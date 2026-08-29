from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from job_comparison_app.fingerprints import (
    ensure_fingerprints_unchanged,
    fingerprint_file,
    fingerprint_files,
)
from job_comparison_app.models import FileFingerprint, RunLevel

from .html_renderer import render_html
from .markdown_parser import MarkdownFormatError, parse_markdown
from .models import BuildResult, ReportDocument, VerificationFinding
from .pdf_renderer import render_pdf
from .report_builder import build_verified_markdown, write_verification_csv
from .source_verifier import SourceVerificationError, load_manifest, verify_sources
from .transaction import commit_outputs, output_lock, preflight_targets, staged_path


WARNING_VERDICTS = {"修正", "併記", "記載なし", "候補者情報未確認"}
FATAL_VERDICTS = {"要判断", "エラー"}


@dataclass(frozen=True, slots=True)
class ReportBuildRequest:
    input_markdown: Path
    source_manifest: Path
    output_pdf: Path
    verified_markdown: Path
    verification_csv: Path
    report_date: str | None = None
    keep_html: bool = False


@dataclass(slots=True)
class ReportBuildPlan:
    request: ReportBuildRequest
    document: ReportDocument
    findings: list[VerificationFinding]
    verdict_counts: dict[str, int]
    warning_count: int
    level: RunLevel
    target_paths: tuple[Path, ...]
    html_target: Path | None
    input_fingerprints: tuple[FileFingerprint, ...]
    output_fingerprints: tuple[FileFingerprint, ...]


def _counts(findings: list[VerificationFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
    return counts


def _validate_distinct_paths(inputs: list[Path], targets: list[Path]) -> None:
    resolved_targets = [path.resolve(strict=False) for path in targets]
    if len(resolved_targets) != len(set(resolved_targets)):
        raise ValueError("出力先が重複しています。")
    resolved_inputs = {path.resolve(strict=False) for path in inputs}
    overlap = [path for path in resolved_targets if path in resolved_inputs]
    if overlap:
        raise ValueError(f"入力ファイルを出力先には指定できません: {overlap[0]}")


def prepare_report_build(request: ReportBuildRequest) -> ReportBuildPlan:
    input_path = request.input_markdown.resolve()
    manifest_path = request.source_manifest.resolve()
    output_pdf = request.output_pdf.resolve()
    verified_markdown = request.verified_markdown.resolve()
    verification_csv = request.verification_csv.resolve()
    if not input_path.is_file():
        raise MarkdownFormatError(f"入力Markdownが存在しません: {input_path}")
    if not manifest_path.is_file():
        raise SourceVerificationError(f"元PDF対応設定が存在しません: {manifest_path}")
    before_input = fingerprint_file(input_path)
    before_manifest = fingerprint_file(manifest_path)
    manifest = load_manifest(manifest_path)
    if request.report_date:
        dt.date.fromisoformat(request.report_date)
        manifest.setdefault("report", {})["created_date"] = request.report_date
    document = parse_markdown(input_path)
    findings = verify_sources(document, manifest)
    if fingerprint_file(input_path) != before_input:
        raise SourceVerificationError("解析中に入力Markdownが変更されました。")
    if fingerprint_file(manifest_path) != before_manifest:
        raise SourceVerificationError("解析中に元PDF対応設定が変更されました。")
    source_paths = [job.source_pdf for job in document.jobs if job.source_pdf is not None]
    source_fingerprints = fingerprint_files(source_paths)
    expected_source_hashes = {
        job.source_pdf.resolve(): job.source_sha256
        for job in document.jobs
        if job.source_pdf is not None
    }
    for fingerprint in source_fingerprints:
        if not fingerprint.exists or fingerprint.sha256 != expected_source_hashes.get(fingerprint.path, ""):
            raise SourceVerificationError(f"解析中に元PDFが変更されました: {fingerprint.path.name}")
    html_target = output_pdf.with_suffix(".html") if request.keep_html else None
    targets = [output_pdf, verified_markdown, verification_csv]
    if html_target:
        targets.append(html_target)
    _validate_distinct_paths([input_path, manifest_path, *source_paths], targets)
    counts = _counts(findings)
    if any(counts.get(verdict, 0) for verdict in FATAL_VERDICTS):
        raise SourceVerificationError("要判断またはエラーがあるため、最終PDFを確定しません。")
    warning_count = sum(counts.get(verdict, 0) for verdict in WARNING_VERDICTS)
    normalized = ReportBuildRequest(
        input_path,
        manifest_path,
        output_pdf,
        verified_markdown,
        verification_csv,
        request.report_date,
        request.keep_html,
    )
    return ReportBuildPlan(
        request=normalized,
        document=document,
        findings=findings,
        verdict_counts=counts,
        warning_count=warning_count,
        level=RunLevel.WARNING if warning_count else RunLevel.SUCCESS,
        target_paths=tuple(targets),
        html_target=html_target,
        input_fingerprints=(before_input, before_manifest, *source_fingerprints),
        output_fingerprints=fingerprint_files(targets),
    )


def commit_report_build(plan: ReportBuildPlan) -> BuildResult:
    request = plan.request
    staged_files: list[Path] = []
    try:
        with output_lock(request.output_pdf):
            ensure_fingerprints_unchanged(plan.input_fingerprints)
            ensure_fingerprints_unchanged(plan.output_fingerprints)
            preflight_targets(list(plan.target_paths))
            html_text = render_html(plan.document)
            staged_pdf = staged_path(request.output_pdf)
            staged_markdown = staged_path(request.verified_markdown)
            staged_csv = staged_path(request.verification_csv)
            staged_files.extend([staged_pdf, staged_markdown, staged_csv])
            staged_markdown.write_text(
                build_verified_markdown(plan.document),
                encoding="utf-8-sig",
                newline="\n",
            )
            write_verification_csv(staged_csv, plan.findings)
            page_count = render_pdf(
                html_text,
                staged_pdf,
                plan.document.title,
                [(job.company, job.position) for job in plan.document.jobs],
            )
            staged: dict[Path, Path] = {
                request.output_pdf: staged_pdf,
                request.verified_markdown: staged_markdown,
                request.verification_csv: staged_csv,
            }
            if plan.html_target:
                staged_html = staged_path(plan.html_target)
                staged_files.append(staged_html)
                staged_html.write_text(html_text, encoding="utf-8", newline="\n")
                staged[plan.html_target] = staged_html
            ensure_fingerprints_unchanged(plan.input_fingerprints)
            ensure_fingerprints_unchanged(plan.output_fingerprints)
            commit_outputs(staged)
        return BuildResult(
            output_pdf=request.output_pdf,
            verified_markdown=request.verified_markdown,
            verification_csv=request.verification_csv,
            page_count=page_count,
            warning_count=plan.warning_count,
            findings=plan.findings,
            verdict_counts=dict(plan.verdict_counts),
            applied=True,
            level=plan.level.value,
        )
    finally:
        for path in staged_files:
            path.unlink(missing_ok=True)
