from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from pypdf import PdfReader, PdfWriter


class PdfRenderError(RuntimeError):
    pass


BROWSER_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def find_browser() -> Path:
    for candidate in BROWSER_CANDIDATES:
        if candidate.is_file():
            return candidate
    for command in ("msedge", "chrome", "google-chrome"):
        found = shutil.which(command)
        if found:
            return Path(found)
    raise PdfRenderError("Microsoft EdgeまたはGoogle Chromeが見つかりません。")


def _apply_metadata(pdf_path: Path, title: str) -> None:
    reader = PdfReader(str(pdf_path), strict=False)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.add_metadata({"/Title": title, "/Author": "", "/Subject": "求人比較資料"})
    metadata_path = pdf_path.with_name(f".{pdf_path.name}.metadata.tmp")
    try:
        with metadata_path.open("wb") as handle:
            writer.write(handle)
        os.replace(metadata_path, pdf_path)
    finally:
        metadata_path.unlink(missing_ok=True)


def validate_pdf(pdf_path: Path, jobs: list[tuple[str, str]], expected_pages: int = 12) -> int:
    if not pdf_path.is_file() or pdf_path.stat().st_size < 10_000:
        raise PdfRenderError("生成PDFが存在しないか、ファイルサイズが小さすぎます。")
    try:
        reader = PdfReader(str(pdf_path), strict=False)
    except Exception as exc:
        raise PdfRenderError(f"生成PDFを読み取れません: {exc}") from exc
    if len(reader.pages) != expected_pages:
        raise PdfRenderError(f"PDFページ数が{expected_pages}ではありません: {len(reader.pages)}")
    all_text: list[str] = []
    for page_number, page in enumerate(reader.pages, 1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if abs(width - 595.28) > 2.0 or abs(height - 841.89) > 2.0:
            raise PdfRenderError(
                f"{page_number}ページ目がA4縦ではありません: {width:.2f} x {height:.2f} pt"
            )
        text = unicodedata.normalize("NFKC", page.extract_text() or "")
        all_text.append(text)
        if "LAYOUT_ERROR" in text:
            raise PdfRenderError(f"{page_number}ページ目で文章がページ内に収まりません。")
    if "比較一覧" not in all_text[0] or "CAREER OPPORTUNITY REPORT" not in all_text[0]:
        raise PdfRenderError("1ページ目の表紙・比較一覧を確認できません。")
    for rank, (company, position) in enumerate(jobs, start=1):
        compact = lambda value: re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠]", "", value)
        page_text = compact(all_text[rank])
        company_token = compact(company)[:6]
        position_token = compact(position)[:6]
        if company_token not in page_text:
            raise PdfRenderError(f"{rank}位の会社名を{rank + 1}ページ目で確認できません: {company}")
        if position_token and position_token not in page_text:
            raise PdfRenderError(f"{rank}位の募集ポジションを{rank + 1}ページ目で確認できません: {position}")
    if "PRIORITY MAP" not in all_text[-1] or "第一志望群" not in all_text[-1]:
        raise PdfRenderError("12ページ目の優先順位マップを確認できません。")
    return len(reader.pages)


def render_pdf(html_text: str, target_pdf: Path, title: str, jobs: list[tuple[str, str]]) -> int:
    browser = find_browser()
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    target_pdf.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="job-comparison-") as raw_temp:
        temp_dir = Path(raw_temp)
        html_path = temp_dir / "report.html"
        profile_dir = temp_dir / "browser-profile"
        html_path.write_text(html_text, encoding="utf-8")
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-proxy-server",
            "--safebrowsing-disable-auto-update",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3000",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={target_pdf}",
            html_path.as_uri(),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise PdfRenderError(f"ブラウザPDF生成に失敗しました（{completed.returncode}）: {detail}")
    _apply_metadata(target_pdf, title)
    return validate_pdf(target_pdf, jobs)
