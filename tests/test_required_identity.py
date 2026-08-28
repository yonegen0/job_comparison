from pathlib import Path

import pytest

from job_pdf_tool.cli import exit_code
from job_pdf_tool.models import DocumentResult, FieldValue
from job_pdf_tool.naming import resolve_positions


def make_identity(company: FieldValue, position: FieldValue) -> DocumentResult:
    result = DocumentResult(source_path=Path("sample.pdf"))
    result.company = company
    result.raw_position = position
    resolve_positions([result])
    result.validate_required_identity()
    return result


@pytest.mark.parametrize("classification", ["direct", "alias"])
def test_direct_and_alias_identity_are_eligible(classification: str) -> None:
    result = make_identity(
        FieldValue("例示株式会社", classification, "会社名", 1),
        FieldValue("事務スタッフ", classification, "求人名", 1),
    )

    assert result.has_required_identity
    assert result.is_main_csv_eligible


@pytest.mark.parametrize("invalid", ["", " ", "記載なし", "なし", "不明", "未定", "N/A", "---"])
def test_placeholder_company_is_rejected(invalid: str) -> None:
    result = make_identity(
        FieldValue(invalid, "direct", "採用企業名", 1),
        FieldValue("事務スタッフ", "direct", "求人名", 1),
    )

    assert not result.is_main_csv_eligible
    assert any(issue.target_field == "会社名" for issue in result.required_issues)


def test_fallback_extraction_source_is_not_accepted() -> None:
    result = make_identity(
        FieldValue("例示株式会社", "fallback", "会社概要", 1),
        FieldValue("事務スタッフ", "direct", "求人名", 1),
    )

    assert not result.is_main_csv_eligible


def test_main_csv_row_rejects_invalid_identity() -> None:
    result = DocumentResult(source_path=Path("sample.pdf"))
    result.validate_required_identity()

    with pytest.raises(ValueError):
        result.main_csv_row()


def test_optional_fields_do_not_exclude_valid_identity() -> None:
    result = make_identity(
        FieldValue("例示株式会社", "direct", "採用企業名", 1),
        FieldValue("Account Executive", "direct", "求人名", 1),
    )
    result.proposed_name = "例示株式会社（Account Executive）.pdf"
    result.finalize_status()

    assert result.is_main_csv_eligible
    assert result.status == "部分成功"
    assert exit_code([result]) == 2
