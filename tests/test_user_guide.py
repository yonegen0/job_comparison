from __future__ import annotations

import binascii
import importlib.util
import os
from pathlib import Path, PurePosixPath
import re
import struct
import zlib

import pytest


ROOT = Path(__file__).parents[1]
GUIDE_ROOT = ROOT / "docs" / "user-guide"
GUIDE = GUIDE_ROOT / "求人比較ツール_使い方.md"
IMAGE_ROOT = GUIDE_ROOT / "images"
EXPECTED_IMAGES = [
    "11-pdf-copy-preview.png",
    "00-workflow-overview.png",
    "01-start-candidate.png",
    "02-candidate-example.png",
    "03-recruit-initial-setup.png",
    "04-recruit-ready.png",
    "05-replace-confirmation.png",
    "06-general-comparison-confirmation.png",
    "07-candidate-analysis-complete.png",
    "08-pdf-organize-preview.png",
    "09-pdf-text-preview.png",
    "10-report-preview.png",
]
EXPECTED_HEADINGS = [
    "## 1. このガイドについて",
    "## 2. 利用前の準備",
    "## 3. 全体ワークフロー",
    "## 4. 起動・終了と共通画面",
    "## 5. 転職者条件を入力する",
    "## 6. 求人取得を初期設定する",
    "## 7. 求人検索結果HTMLを保存する",
    "## 8. 求人を取り込む",
    "## 9. 求人データを管理する",
    "## 10. AI比較ファイルを作る",
    "## 11. 求人PDFを整理する",
    "## 12. PDF全文をMarkdownへまとめる",
    "## 13. 外部AIで比較Markdownを準備する",
    "## 14. 元PDF対応JSONを準備する",
    "## 15. 比較資料PDFを生成する",
    "## 16. 出力ファイル早見表",
    "## 17. ショートカット",
    "## 18. ボタンが押せない場合",
    "## 19. エラーと復旧",
    "## 20. 個人情報とデータ保存",
]


def _guide_text() -> str:
    text = GUIDE.read_text(encoding="utf-8")
    assert not text.startswith("\ufeff")
    return text


def _image_links(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]+\]\((images/[^)]+\.png)\)", text)


def _png_chunks(path: Path) -> tuple[tuple[int, int], list[str]]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    dimensions: tuple[int, int] | None = None
    types: list[str] = []
    while offset < len(data):
        assert offset + 12 <= len(data)
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        assert binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == expected_crc
        decoded = chunk_type.decode("ascii")
        types.append(decoded)
        if decoded == "IHDR":
            dimensions = struct.unpack(">II", chunk_data[:8])
        offset += 12 + length
        if decoded == "IEND":
            break
    assert offset == len(data)
    assert dimensions is not None
    return dimensions, types


def test_user_guide_encoding_structure_and_required_contracts() -> None:
    text = _guide_text()
    assert text.startswith("# 求人比較ツール 使い方ガイド\n")
    assert "対象バージョン: 0.4.3" in text
    positions = [text.index(heading) for heading in EXPECTED_HEADINGS]
    assert positions == sorted(positions)
    assert all(text.count(heading) == 1 for heading in EXPECTED_HEADINGS)
    for tab in (
        "転職者条件",
        "求人取得",
        "求人PDF整理",
        "求人PDF全文統合テキスト化",
        "比較資料PDF生成",
    ):
        assert tab in text
    for required in (
        "HTML・AI比較ワークフロー",
        "PDF・比較資料ワークフロー",
        "求人取得用JSONとPDF工程用JSONは別形式",
        "媒体ごとの上限は100件",
        "上位最大20件",
        "順位1～10",
        "A4縦12ページ",
        "GUIでは作成・更新できません",
        "求人JSONが必須で、v10ガイドは任意",
        "求人JSONは必須です",
        "条件入りv11ガイドも必須です",
    ):
        assert required in text


def test_all_shortcuts_and_recovery_codes_are_documented() -> None:
    text = _guide_text()
    for shortcut in (
        "Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "F1", "F5",
        "Alt+J", "Alt+C", "Ctrl+O", "Ctrl+I", "Ctrl+E", "Ctrl+R", "Ctrl+G",
        "Ctrl+Shift+O",
    ):
        assert f"`{shortcut}`" in text
    for code in (
        "CHANGED001", "INPUT001", "BROWSER001", "OUTPUT001", "LOCK001",
        "APP003", "IMPORT_FORMAT_UNSUPPORTED", "IMPORT_VALIDATION_FAILED", "CSV001",
    ):
        assert f"`{code}`" in text


def test_image_links_are_exact_ordered_relative_and_complete() -> None:
    text = _guide_text()
    links = _image_links(text)
    assert links == [f"images/{name}" for name in EXPECTED_IMAGES]
    assert sorted(path.name for path in IMAGE_ROOT.iterdir()) == sorted(EXPECTED_IMAGES)
    for link in links:
        pure = PurePosixPath(link)
        assert not pure.is_absolute()
        assert ".." not in pure.parts
        target = (GUIDE_ROOT / Path(*pure.parts)).resolve(strict=True)
        assert target.parent == IMAGE_ROOT.resolve(strict=True)
        assert target.is_file()
        assert not target.is_symlink()
        assert not bool(getattr(target, "is_junction", lambda: False)())


def test_png_files_are_valid_bounded_and_have_no_metadata_chunks() -> None:
    total = 0
    forbidden = {"tEXt", "zTXt", "iTXt", "eXIf"}
    for name in EXPECTED_IMAGES:
        path = IMAGE_ROOT / name
        size = path.stat().st_size
        assert 0 < size <= 1024 * 1024
        total += size
        (width, height), chunks = _png_chunks(path)
        assert 760 <= width <= 1600
        assert 560 <= height <= 900
        assert chunks[0] == "IHDR"
        assert chunks[-1] == "IEND"
        assert forbidden.isdisjoint(chunks)
    assert total <= 8 * 1024 * 1024


def test_guide_contains_only_synthetic_paths_and_sample_markers() -> None:
    text = _guide_text()
    forbidden = (
        "C:\\Users\\",
        "Downloads\\",
        "Documents\\",
        "転職自動化ツール",
    )
    assert not any(value.casefold() in text.casefold() for value in forbidden)
    assert "M:\\manual-sample" in text
    assert "サンプル株式会社" in text
    assert "サンプル画面" in text
    assert "すべて架空のサンプル" in text


def test_technical_document_links_resolve() -> None:
    text = _guide_text()
    links = re.findall(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]+)?\)", text)
    assert "../../README_job_comparison_report.md" in links
    for link in links:
        target = (GUIDE_ROOT / Path(*PurePosixPath(link).parts)).resolve(strict=True)
        assert target.is_file()
        assert target.is_relative_to(ROOT.resolve())


def test_existing_documents_and_gui_help_link_to_the_detailed_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bundled = (ROOT / "はじめにお読みください.txt").read_text(encoding="utf-8")
    help_source = (ROOT / "job_comparison_app" / "gui" / "dialogs.py").read_text(encoding="utf-8")
    assert "[画像付き使い方ガイド](docs/user-guide/求人比較ツール_使い方.md)" in readme
    assert "使い方ガイド\\求人比較ツール_使い方.md" in bundled
    assert "Markdown対応ビューアー" in bundled
    assert "アプリと同じ配布フォルダーの「使い方ガイド」" in help_source
    for text in (readme, bundled, _guide_text()):
        assert "0.4.3" in text


def test_capture_tool_has_fixed_scenes_and_rejects_other_output_roots() -> None:
    module_path = ROOT / "tools" / "user_guide_capture" / "capture_scenes.py"
    spec = importlib.util.spec_from_file_location("user_guide_capture_scenes", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert list(module.SCENES) == [Path(name).stem for name in EXPECTED_IMAGES]
    assert set(module.SCENE_RUNNERS) == set(module.SCENES)
    expected = "only be written" if os.name == "nt" else "supported only on Windows"
    with pytest.raises(RuntimeError, match=expected):
        module._prepare_environment(ROOT / ".venv-build" / "outside-user-guide-test")


def test_capture_tool_uses_ui_rendering_without_application_services() -> None:
    python_source = (ROOT / "tools" / "user_guide_capture" / "capture_scenes.py").read_text(encoding="utf-8")
    powershell_source = (ROOT / "tools" / "user_guide_capture" / "capture_window.ps1").read_text(encoding="utf-8")
    for forbidden in (
        "RecruitJobApplication(",
        "JobComparisonApplication(",
        "prepare_pdf_organization(",
        "prepare_pdf_text_export(",
        "prepare_report_build(",
    ):
        assert forbidden not in python_source
    assert "TkView(root)" in python_source
    assert "M:\\manual-sample" in python_source
    assert "GetClientRect" in powershell_source
    assert "ClientToScreen" in powershell_source
    assert "CopyFromScreen" in powershell_source
    assert "サンプル画面" not in powershell_source  # constructed from Unicode code points
