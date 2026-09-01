from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from job_comparison_app.errors import InputChangedError
from job_comparison_app.fingerprints import (
    ensure_fingerprints_unchanged,
    fingerprint_file,
)
from job_comparison_app.models import FileFingerprint, PdfInventoryItem, RunLevel
from job_pdf_tool.transaction import ApplyError, directory_locks

from .core import ExtractionError, build_markdown, discover_pdfs, extract_documents
from .models import (
    BuiltMarkdown,
    DocumentResult,
    ExtractionWarning,
    ProgressCallback,
    ProgressEvent,
)


@dataclass(frozen=True, slots=True)
class PdfTextExportRequest:
    input_dir: Path
    output_markdown: Path


@dataclass(slots=True)
class PdfTextExportPlan:
    request: PdfTextExportRequest
    documents: tuple[DocumentResult, ...]
    built_markdown: BuiltMarkdown
    document_count: int
    page_count: int
    character_count: int
    byte_count: int
    warnings: tuple[ExtractionWarning, ...]
    level: RunLevel
    pdf_inventory: tuple[PdfInventoryItem, ...]
    output_fingerprint: FileFingerprint


@dataclass(slots=True)
class PdfTextExportResult:
    plan: PdfTextExportPlan
    output_path: Path
    document_count: int
    page_count: int
    character_count: int
    byte_count: int
    warnings: tuple[ExtractionWarning, ...]
    applied: bool
    level: RunLevel


def _inventory(paths: tuple[Path, ...]) -> tuple[PdfInventoryItem, ...]:
    return tuple(PdfInventoryItem(path.name, fingerprint_file(path)) for path in paths)


def _ensure_inventory(input_dir: Path, expected: tuple[PdfInventoryItem, ...]) -> None:
    try:
        _, paths = discover_pdfs(input_dir)
        current = _inventory(paths)
    except (ExtractionError, OSError) as exc:
        raise InputChangedError("確認後にPDFフォルダーを再確認できません。") from exc
    if current != expected:
        raise InputChangedError("確認後にPDFが追加、削除、または変更されました。")


def prepare_pdf_text_export(
    request: PdfTextExportRequest,
    progress_callback: ProgressCallback | None = None,
) -> PdfTextExportPlan:
    raw_output = request.output_markdown
    if raw_output.is_symlink() or bool(getattr(raw_output, "is_junction", lambda: False)()):
        raise ExtractionError(f"Markdown出力先にリンクは指定できません: {raw_output}")
    raw_parent = raw_output.parent
    if raw_parent.is_symlink() or bool(getattr(raw_parent, "is_junction", lambda: False)()):
        raise ExtractionError(f"Markdown出力フォルダーにリンクは指定できません: {raw_parent}")
    input_dir, paths = discover_pdfs(request.input_dir)
    output = request.output_markdown.resolve(strict=False)
    if output.exists() and not output.is_file():
        raise ExtractionError(f"Markdown出力先が通常ファイルではありません: {output}")
    if output in {path.resolve() for path in paths}:
        raise ExtractionError("PDFをMarkdown出力先には指定できません。")
    inventory = _inventory(paths)
    documents = extract_documents(paths, progress_callback)
    if progress_callback:
        progress_callback(ProgressEvent("building", len(paths), len(paths)))
    built = build_markdown(documents)
    return PdfTextExportPlan(
        request=PdfTextExportRequest(input_dir, output),
        documents=documents,
        built_markdown=built,
        document_count=len(documents),
        page_count=sum(document.page_count for document in documents),
        character_count=built.character_count,
        byte_count=built.byte_count,
        warnings=built.warnings,
        level=RunLevel.WARNING if built.warnings else RunLevel.SUCCESS,
        pdf_inventory=inventory,
        output_fingerprint=fingerprint_file(output),
    )


def _create_directories(directory: Path) -> list[Path]:
    if directory.exists():
        if not directory.is_dir():
            raise ApplyError(f"出力先の親がフォルダーではありません: {directory}")
        return []
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise ApplyError(f"出力先の親がフォルダーではありません: {current}")
    directory.mkdir(parents=True)
    return list(reversed(missing))


def _validate_staged(path: Path, plan: PdfTextExportPlan) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ApplyError("Markdownの読み戻し検証に失敗しました。") from exc
    if content != plan.built_markdown.content or "\x00" in content:
        raise ApplyError("Markdownの内容検証に失敗しました。")
    marker = plan.built_markdown.marker_prefix
    if content.count(f"<<<{marker}_DOCUMENT_START") != plan.document_count:
        raise ApplyError("Markdownの文書境界検証に失敗しました。")
    if content.count(f"<<<{marker}_PAGE_START") != plan.page_count:
        raise ApplyError("Markdownのページ境界検証に失敗しました。")


def commit_pdf_text_export(
    plan: PdfTextExportPlan,
    progress_callback: ProgressCallback | None = None,
) -> PdfTextExportResult:
    output = plan.request.output_markdown
    created = _create_directories(output.parent)
    temp: Path | None = None
    backup: Path | None = None
    published = False
    committed = False
    try:
        with directory_locks([plan.request.input_dir, output.parent]):
            _ensure_inventory(plan.request.input_dir, plan.pdf_inventory)
            ensure_fingerprints_unchanged((plan.output_fingerprint,))
            if progress_callback:
                progress_callback(ProgressEvent("committing", plan.document_count, plan.document_count))
            descriptor, raw = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
            temp = Path(raw)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(plan.built_markdown.content)
                stream.flush()
                os.fsync(stream.fileno())
            _validate_staged(temp, plan)
            _ensure_inventory(plan.request.input_dir, plan.pdf_inventory)
            ensure_fingerprints_unchanged((plan.output_fingerprint,))
            if plan.output_fingerprint.exists:
                backup = output.with_name(f".{output.name}.{uuid.uuid4().hex}.backup")
                output.rename(backup)
            elif output.exists():
                raise InputChangedError("確認後にMarkdown出力先が作成されました。")
            temp.rename(output)
            published = True
            _validate_staged(output, plan)
            if backup:
                backup.unlink()
                backup = None
            committed = True
        if progress_callback:
            progress_callback(ProgressEvent("complete", plan.document_count, plan.document_count))
    except (InputChangedError, ApplyError):
        raise
    except OSError as exc:
        raise ApplyError(f"Markdownを確定できませんでした: {exc}") from exc
    finally:
        if temp is not None:
            with suppress(OSError):
                temp.unlink(missing_ok=True)
        if backup is not None:
            try:
                if published and output.exists():
                    output.unlink()
                backup.rename(output)
            except OSError as exc:
                raise ApplyError(f"既存Markdownを復元できませんでした: {output.name}") from exc
        elif not committed and not plan.output_fingerprint.exists and published:
            with suppress(OSError):
                output.unlink(missing_ok=True)
        if not committed:
            for directory in sorted(created, key=lambda path: len(path.parts), reverse=True):
                with suppress(OSError):
                    directory.rmdir()
    return PdfTextExportResult(
        plan, output, plan.document_count, plan.page_count, plan.character_count,
        plan.byte_count, plan.warnings, True, plan.level,
    )


def extract_folder(
    input_directory: Path,
    progress_callback: ProgressCallback | None = None,
) -> PdfTextExportResult:
    output = input_directory.resolve(strict=False) / "output" / "PDF全文_統合テキスト化.md"
    plan = prepare_pdf_text_export(PdfTextExportRequest(input_directory, output), progress_callback)
    return commit_pdf_text_export(plan, progress_callback)


__all__ = [
    "PdfTextExportPlan", "PdfTextExportRequest", "PdfTextExportResult",
    "commit_pdf_text_export", "extract_folder", "prepare_pdf_text_export",
]
