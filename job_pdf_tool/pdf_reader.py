from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError as PyPdfReadError

from .models import PdfDocument, TextLine


class PdfToolReadError(RuntimeError):
    """The PDF could not be read safely."""


class EncryptedPdfError(PdfToolReadError):
    pass


class ScannedPdfError(PdfToolReadError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_extracted_text(value: str) -> str:
    cleaned: list[str] = []
    for char in value.replace("\r\n", "\n").replace("\r", "\n"):
        if char in {"\n", "\t"}:
            cleaned.append(char)
            continue
        category = unicodedata.category(char)
        if category in {"Cc", "Cs", "Cn"}:
            continue
        if char in {"\ufffd", "\uffff"}:
            continue
        cleaned.append(char)
    return "".join(cleaned)


def read_pdf(path: Path) -> PdfDocument:
    try:
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise PdfToolReadError("PDFヘッダーを確認できません。")
        digest = sha256_file(path)
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:  # pragma: no cover - pypdf backend detail
                raise EncryptedPdfError("パスワード付きPDFです。") from exc
            if not decrypted:
                raise EncryptedPdfError("パスワード付きPDFです。")

        lines: list[TextLine] = []
        page_texts: list[str] = []
        plain_page_texts: list[str] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:  # Older compatible pypdf fallback.
                text = page.extract_text() or ""
            text = _clean_extracted_text(text)
            plain_text = _clean_extracted_text(page.extract_text() or "")
            page_texts.append(text)
            plain_page_texts.append(plain_text)
            lines.extend(TextLine(page=page_number, text=line.rstrip()) for line in text.splitlines())

        all_text = "\n".join(page_texts)
        visible_count = sum(1 for char in all_text if not char.isspace())
        if visible_count < 80:
            raise ScannedPdfError("抽出可能な文字が少ないため、スキャンPDFとして扱います。")
        return PdfDocument(
            path=path,
            sha256=digest,
            page_count=len(reader.pages),
            lines=lines,
            text=all_text,
            plain_text="\n".join(plain_page_texts),
        )
    except (EncryptedPdfError, ScannedPdfError, PdfToolReadError):
        raise
    except (OSError, PyPdfReadError, ValueError, KeyError) as exc:
        raise PdfToolReadError(f"PDFを読み取れません: {exc}") from exc
