from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from .models import (
    BuiltMarkdown,
    DocumentResult,
    ExtractionWarning,
    PageResult,
    ProgressCallback,
    ProgressEvent,
)

FORMAT_VERSION = 1
OUTPUT_DIRECTORY_NAME = "output"
OUTPUT_FILENAME = "PDF全文_統合テキスト化.md"
BASE_MARKER_PREFIX = "PDFTEXT_V1"
NUL_REPLACEMENT = "␀"
CHARACTER_WARNING_LIMIT = 150_000
BYTE_WARNING_LIMIT = 500_000


class ExtractionError(RuntimeError):
    def __init__(self, message: str, details: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return self.message if not self.details else f"{self.message} {'; '.join(self.details)}"


def discover_pdfs(input_directory: Path) -> tuple[Path, tuple[Path, ...]]:
    try:
        resolved = input_directory.resolve(strict=True)
    except OSError as exc:
        raise ExtractionError("指定されたフォルダーが見つかりません。") from exc
    if not resolved.is_dir():
        raise ExtractionError("指定されたパスはフォルダーではありません。")
    try:
        paths = tuple(sorted(
            (path for path in resolved.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf"),
            key=lambda path: (path.name.casefold(), path.name),
        ))
    except OSError as exc:
        raise ExtractionError("指定されたフォルダーを読み取れません。") from exc
    if not paths:
        raise ExtractionError("指定されたフォルダー直下にPDFがありません。")
    return resolved, paths


def extract_documents(
    paths: tuple[Path, ...], progress_callback: ProgressCallback | None = None
) -> tuple[DocumentResult, ...]:
    _emit(progress_callback, ProgressEvent("discovered", 0, len(paths)))
    documents: list[DocumentResult] = []
    errors: list[str] = []
    for index, path in enumerate(paths, 1):
        _emit(progress_callback, ProgressEvent("document", index, len(paths), path.name))
        try:
            with path.open("rb") as stream:
                reader = PdfReader(stream, strict=False)
                if reader.is_encrypted and not reader.decrypt(""):
                    raise ExtractionError("パスワードが必要なPDFです。")
                if not reader.pages:
                    raise ExtractionError("ページがありません。")
                pages: list[PageResult] = []
                page_count = len(reader.pages)
                for page_number, page in enumerate(reader.pages, 1):
                    text = "" if "/Contents" not in page else page.extract_text(
                        extraction_mode="layout", layout_mode_strip_rotated=False
                    ) or ""
                    pages.append(PageResult(page_number, text, text.count("\x00"), not text.strip()))
                    _emit(progress_callback, ProgressEvent(
                        "page", index, len(paths), path.name, page_number, page_count
                    ))
                documents.append(DocumentResult(index, path.name, page_count, tuple(pages)))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        raise ExtractionError("読み取れないPDFがあるため、出力を中止しました。", tuple(errors))
    return tuple(documents)


def build_markdown(documents: tuple[DocumentResult, ...]) -> BuiltMarkdown:
    marker = _choose_marker_prefix(documents)
    warnings = list(_content_warnings(documents))
    for _ in range(3):
        content = _render(documents, tuple(warnings), marker)
        added = False
        if len(content) >= CHARACTER_WARNING_LIMIT and not any(w.code == "large_chars" for w in warnings):
            warnings.append(ExtractionWarning("large_chars", f"出力が{CHARACTER_WARNING_LIMIT:,}文字を超えています。", count=len(content)))
            added = True
        size = len(content.encode("utf-8"))
        if size >= BYTE_WARNING_LIMIT and not any(w.code == "large_bytes" for w in warnings):
            warnings.append(ExtractionWarning("large_bytes", f"出力が{BYTE_WARNING_LIMIT:,}バイトを超えています。", count=size))
            added = True
        if not added:
            break
    content = _render(documents, tuple(warnings), marker)
    _validate_markdown(content, documents, marker)
    return BuiltMarkdown(
        content, tuple(warnings), marker,
        sum(document.character_count for document in documents),
        len(content.encode("utf-8")),
    )


def _content_warnings(documents: tuple[DocumentResult, ...]) -> tuple[ExtractionWarning, ...]:
    result: list[ExtractionWarning] = []
    for document in documents:
        for page in document.pages:
            if page.nul_count:
                result.append(ExtractionWarning("nul_replaced", f"NUL {page.nul_count}文字を{NUL_REPLACEMENT}へ置換しました。", document.filename, page.page_number, page.nul_count))
            if page.is_empty:
                result.append(ExtractionWarning("no_text", "テキストを抽出できませんでした。", document.filename, page.page_number))
    return tuple(result)


def _render(documents: tuple[DocumentResult, ...], warnings: tuple[ExtractionWarning, ...], marker: str) -> str:
    from job_comparison_app import __version__
    chunks = [
        "# PDF全文統合テキスト化\n\n", f"- format_version: {FORMAT_VERSION}\n", f"- tool_version: {__version__}\n",
        f"- document_count: {len(documents)}\n", f"- page_count: {sum(d.page_count for d in documents)}\n",
        "- extraction_mode: pypdf-layout\n", "- source_scope: selected-folder-only\n",
        "- nul_replacement: U+0000 -> U+2400\n", f"- boundary_prefix: {marker}\n\n", "## 抽出警告\n\n",
    ]
    if warnings:
        for warning in warnings:
            prefix = ""
            if warning.filename is not None and warning.page_number is not None:
                prefix = f"{json.dumps(warning.filename, ensure_ascii=False)} / Page {warning.page_number}: "
            chunks.append(f"- {prefix}{warning.message}\n")
    else:
        chunks.append("- なし\n")
    for document in documents:
        number = f"{document.index:03d}"
        chunks += ["\n", f"<<<{marker}_DOCUMENT_START index={number}>>>\n\n", f"## PDF {number}\n\n",
                   f"source_file: {json.dumps(document.filename, ensure_ascii=False)}\n", f"page_count: {document.page_count}\n\n"]
        for page in document.pages:
            page_no = f"{page.page_number:03d}"
            status = "no_text" if page.is_empty else "ok"
            chunks.append(f"<<<{marker}_PAGE_START document={number} page={page_no} total={document.page_count:03d} status={status}>>>\n")
            chunks.append(page.text.replace("\x00", NUL_REPLACEMENT))
            chunks.append(f"\n<<<{marker}_PAGE_END document={number} page={page_no}>>>\n\n")
        chunks.append(f"<<<{marker}_DOCUMENT_END index={number}>>>\n")
    return "".join(chunks)


def _choose_marker_prefix(documents: tuple[DocumentResult, ...]) -> str:
    texts = tuple(page.text for document in documents for page in document.pages)
    marker = BASE_MARKER_PREFIX
    if not any(f"<<<{marker}_" in text for text in texts):
        return marker
    digest = hashlib.sha256("\n".join(d.filename for d in documents).encode()).hexdigest()[:12].upper()
    marker = f"{BASE_MARKER_PREFIX}_{digest}"
    suffix = 2
    while any(f"<<<{marker}_" in text for text in texts):
        marker = f"{BASE_MARKER_PREFIX}_{digest}_{suffix}"
        suffix += 1
    return marker


def _validate_markdown(content: str, documents: tuple[DocumentResult, ...], marker: str) -> None:
    expected = {
        f"<<<{marker}_DOCUMENT_START": len(documents), f"<<<{marker}_DOCUMENT_END": len(documents),
        f"<<<{marker}_PAGE_START": sum(d.page_count for d in documents),
        f"<<<{marker}_PAGE_END": sum(d.page_count for d in documents),
    }
    if any(content.count(token) != count for token, count in expected.items()):
        raise ExtractionError("Markdownの境界検証に失敗しました。")
    if "\x00" in content:
        raise ExtractionError("Markdown内に未処理のNUL文字が残っています。")
    content.encode("utf-8").decode("utf-8")


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback:
        callback(event)


__all__ = ["BYTE_WARNING_LIMIT", "CHARACTER_WARNING_LIMIT", "ExtractionError", "build_markdown", "discover_pdfs", "extract_documents"]
