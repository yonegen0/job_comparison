from pathlib import Path

import pytest
from pypdf import PdfWriter

from job_comparison_app.errors import InputChangedError
from job_comparison_app.gui.export_outputs import (
    inspect_export_output,
    resolve_export_output,
    resolve_numbered_export_output,
)
from pdf_job_exporter.core import ExtractionError, NUL_REPLACEMENT, build_markdown, discover_pdfs
from pdf_job_exporter.models import DocumentResult, PageResult
from pdf_job_exporter.service import (
    PdfTextExportRequest,
    commit_pdf_text_export,
    prepare_pdf_text_export,
)


def _blank_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages): writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream: writer.write(stream)


def test_discovery_is_direct_case_insensitive_and_deterministic(tmp_path: Path) -> None:
    _blank_pdf(tmp_path / "B.PDF"); _blank_pdf(tmp_path / "a.pdf")
    nested = tmp_path / "nested"; nested.mkdir(); _blank_pdf(nested / "nested.pdf")
    _, paths = discover_pdfs(tmp_path)
    assert [path.name for path in paths] == ["a.pdf", "B.PDF"]


def test_build_markdown_preserves_text_and_only_replaces_nul() -> None:
    raw = "  本文\x00末尾  "
    document = DocumentResult(1, "求人.pdf", 1, (PageResult(1, raw, 1, False),))
    built = build_markdown((document,))
    assert raw.replace("\x00", NUL_REPLACEMENT) in built.content
    assert "\x00" not in built.content
    assert 'source_file: "求人.pdf"' in built.content


def test_prepare_is_write_free_and_commit_creates_markdown(tmp_path: Path) -> None:
    _blank_pdf(tmp_path / "求人.pdf", 2)
    output = resolve_export_output(tmp_path)
    events = []
    plan = prepare_pdf_text_export(PdfTextExportRequest(tmp_path, output), events.append)
    assert not output.parent.exists()
    assert plan.document_count == 1 and plan.page_count == 2
    assert any(warning.code == "no_text" for warning in plan.warnings)
    result = commit_pdf_text_export(plan, events.append)
    assert result.output_path.is_file()
    assert result.output_path.read_text(encoding="utf-8") == plan.built_markdown.content
    assert events[-1].stage == "complete"


def test_commit_detects_pdf_change_after_prepare(tmp_path: Path) -> None:
    source = tmp_path / "求人.pdf"; _blank_pdf(source)
    plan = prepare_pdf_text_export(PdfTextExportRequest(tmp_path, resolve_export_output(tmp_path)))
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(InputChangedError): commit_pdf_text_export(plan)
    assert not plan.request.output_markdown.exists()


def test_existing_output_can_be_numbered_without_touching_original(tmp_path: Path) -> None:
    base = resolve_export_output(tmp_path); base.parent.mkdir(); base.write_text("original", encoding="utf-8")
    (base.parent / "PDF全文_統合テキスト化(1).md").write_text("used", encoding="utf-8")
    conflict = inspect_export_output(base)
    assert conflict.existing_path == base
    assert conflict.suggested_path.name == "PDF全文_統合テキスト化(2).md"
    assert resolve_numbered_export_output(base).name == "PDF全文_統合テキスト化(2).md"


def test_unreadable_pdf_aborts_whole_prepare(tmp_path: Path) -> None:
    (tmp_path / "broken.pdf").write_bytes(b"not pdf")
    with pytest.raises(ExtractionError, match="読み取れないPDF"):
        prepare_pdf_text_export(PdfTextExportRequest(tmp_path, resolve_export_output(tmp_path)))
