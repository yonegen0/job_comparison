from pathlib import Path

from job_pdf_tool.models import DocumentResult, FieldValue
from job_pdf_tool.naming import MAX_ABSOLUTE_PATH, assign_proposed_names, sanitize_component


def make_result(
    tmp_path: Path,
    name: str,
    company: str,
    position: str,
    digest: str,
    job_id: str = "",
) -> DocumentResult:
    result = DocumentResult(source_path=tmp_path / name, sha256=digest)
    result.company = FieldValue(company, "direct", "採用企業名", 1)
    result.raw_position = FieldValue(position, "direct", "職種分類（中分類）", 1)
    result.job_id = job_id
    return result


def test_middle_category_is_used_verbatim_for_csv_and_name(tmp_path: Path) -> None:
    result = make_result(
        tmp_path,
        "source.pdf",
        "Example株式会社",
        "営業事務、営業アシスタント",
        "c" * 64,
    )

    assign_proposed_names([result], tmp_path)

    assert result.resolved_position == "営業事務、営業アシスタント"
    assert result.position_resolution == "middle_category"
    assert result.main_csv_row()["募集ポジション"] == "営業事務、営業アシスタント"
    assert result.proposed_name == "Example株式会社（営業事務、営業アシスタント）.pdf"
    assert not any(item.classification == "fallback" for item in result.reports)


def test_duplicate_company_and_category_identify_every_file(tmp_path: Path) -> None:
    first = make_result(
        tmp_path,
        "a.pdf",
        "株式会社partners",
        "キャリアカウンセラー、キャリアコンサルタント",
        "a" * 64,
        "00364540-f3291",
    )
    second = make_result(
        tmp_path,
        "b.pdf",
        "株式会社partners",
        "キャリアカウンセラー、キャリアコンサルタント",
        "b" * 64,
        "00364540-fffff",
    )

    assign_proposed_names([first, second], tmp_path)

    assert first.proposed_name == "株式会社partners（キャリアカウンセラー、キャリアコンサルタント-540f3291）.pdf"
    assert second.proposed_name == "株式会社partners（キャリアカウンセラー、キャリアコンサルタント-540fffff）.pdf"
    assert all(any(item.classification == "warning" for item in result.reports) for result in (first, second))


def test_windows_filename_sanitizing_preserves_valid_symbols() -> None:
    assert sanitize_component('営業/企画:東日本?★😀') == "営業／企画：東日本？★😀"
    assert sanitize_component("CON") == "_CON"


def test_long_path_is_shortened_with_identifier(tmp_path: Path) -> None:
    result = make_result(
        tmp_path,
        "source.pdf",
        "長い会社名株式会社",
        "営業職" * 100,
        "d" * 64,
    )

    assign_proposed_names([result], tmp_path)

    assert result.proposed_name
    assert len(str(tmp_path / result.proposed_name)) <= MAX_ABSOLUTE_PATH
    assert "-dddddddd）.pdf" in result.proposed_name
    assert any("パス" in item.message for item in result.reports)
