from __future__ import annotations

from pathlib import Path

import pytest

from job_comparison_report.html_renderer import render_html
from job_comparison_report.markdown_parser import MarkdownFormatError, parse_markdown
from job_comparison_report.pdf_renderer import find_browser, render_pdf
from job_comparison_report.source_verifier import load_manifest, verify_sources


ROOT = Path(__file__).resolve().parent.parent
REAL_INPUT = Path(r"C:\Users\user\Downloads\# 求人比較資料_オフィスワーカー.md")
MANIFEST = ROOT / "reports" / "オフィスワーカー_sources.json"


def _sample_markdown(*, ranks=range(1, 11), unsafe_text="") -> str:
    lines = [
        "# 求人比較資料",
        "",
        "## 比較一覧",
        "",
        "| 順位 | 会社・職種 | 雇用形態 | 評価 | 年収 | 平均残業 | 年間休日 | 主な勤務地 | 難易度 |",
        "|---:|---|---|---|---|---|---:|---|---|",
    ]
    for rank in ranks:
        lines.append(f"| {rank} | 会社{rank}／職種{rank} | 正社員 | A | 400万円 | 10時間 | 120日 | 愛知県 | 中 |")
    for rank in ranks:
        lines.extend(
            [
                "",
                f"# {rank}位｜会社{rank}｜職種{rank}",
                "",
                "## ヘッダ",
                "",
                "| 項目 | 内容 |",
                "|---|---|",
                "| 雇用形態 | 正社員 |",
                "| 評価 | A |",
                "| 年収 | 400万円 |",
                "| インセンティブ | 記載なし |",
                "| 平均残業 | 10時間 |",
                "| 年間休日 | 120日 |",
                "| 休日 | 土日祝 |",
                "| 勤務地 | 愛知県 |",
                "| 転勤 | なし |",
                "| 応募条件 | 高卒以上 |",
                "| 歓迎条件 | 記載なし |",
                "| 難易度 | 中 |",
                "",
                "### 業務概要",
                f"業務概要{rank}{unsafe_text}",
                "### おすすめポイント",
                "- おすすめ",
                "### メリット",
                "- メリット",
                "### デメリット",
                "- 注意点",
                "### 総合判断",
                "応募候補です。",
            ]
        )
    lines.extend(["# 第一優先グループ", *[f"**{rank}位 会社{rank}**" for rank in range(1, 6)]])
    lines.extend(["# 第二優先グループ", *[f"**{rank}位 会社{rank}**" for rank in range(6, 8)]])
    lines.extend(["# 条件付きグループ", *[f"**{rank}位 会社{rank}**" for rank in range(8, 11)]])
    return "\n".join(lines)


def test_parser_accepts_ten_ranked_jobs(tmp_path: Path) -> None:
    source = tmp_path / "# 日本語 入力.md"
    source.write_text(_sample_markdown(), encoding="utf-8")
    document = parse_markdown(source)
    assert [job.rank for job in document.jobs] == list(range(1, 11))
    assert document.priority_groups == {
        "第一志望群": [1, 2, 3, 4, 5],
        "第二志望群": [6, 7],
        "条件付き候補": [8, 9, 10],
    }


def test_parser_rejects_rank_gap(tmp_path: Path) -> None:
    source = tmp_path / "bad.md"
    source.write_text(_sample_markdown(ranks=range(1, 10)), encoding="utf-8")
    with pytest.raises(MarkdownFormatError):
        parse_markdown(source)


def test_html_escapes_attached_document_content(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.md"
    source.write_text(_sample_markdown(unsafe_text='<script src="https://example.invalid/x.js"></script>'), encoding="utf-8")
    document = parse_markdown(source)
    document.title = "求人比較資料｜テスト"
    document.subtitle = "匿名"
    document.created_date = "2026-08-23"
    document.candidate_unknowns = ["学歴"]
    html_text = render_html(document)
    assert html_text.count('class="page ') == 12
    assert "https://example.invalid" not in html_text
    assert '<script src="https://example.invalid' not in html_text


@pytest.mark.skipif(not REAL_INPUT.is_file() or not MANIFEST.is_file(), reason="実データがない環境")
def test_real_sources_are_fixed_and_verified() -> None:
    document = parse_markdown(REAL_INPUT)
    findings = verify_sources(document, load_manifest(MANIFEST))
    assert len(document.jobs) == 10
    assert all(job.source_sha256 for job in document.jobs)
    assert document.jobs[5].fields["年間休日"] == "120日"
    assert not [item for item in findings if item.verdict in {"要判断", "エラー"}]
    assert document.jobs[7].source_pdf is not None
    assert "未経験" in document.jobs[7].source_pdf.name
    assert "(CA)" in document.jobs[9].source_pdf.name


@pytest.mark.skipif(not REAL_INPUT.is_file() or not MANIFEST.is_file(), reason="実データがない環境")
def test_real_report_renders_as_a4_12_pages(tmp_path: Path) -> None:
    try:
        find_browser()
    except Exception:
        pytest.skip("Edge/Chromeがない環境")
    document = parse_markdown(REAL_INPUT)
    verify_sources(document, load_manifest(MANIFEST))
    output = tmp_path / "求人比較資料.pdf"
    pages = render_pdf(
        render_html(document),
        output,
        document.title,
        [(job.company, job.position) for job in document.jobs],
    )
    assert pages == 12
    assert output.stat().st_size > 10_000
