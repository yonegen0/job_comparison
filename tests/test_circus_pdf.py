from __future__ import annotations

from pathlib import Path

from job_pdf_tool.models import DocumentResult, FieldValue, JOB_INFO_FIELDS, PdfDocument, TextLine
from job_pdf_tool.naming import assign_proposed_names
from job_pdf_tool.output import JobInfoDocument, prepare_job_info_document, serialize_job_info
from job_pdf_tool.providers.circus import CIRCUS_FIELDS, CircusExtractor, extract_job_id


def document(filename: str, text: str) -> PdfDocument:
    path = Path(filename)
    return PdfDocument(path, "a" * 64, 1,
                       [TextLine(1, line) for line in text.splitlines()], text, text)


SAMPLE = """
募集企業名：架空株式会社
募集ポジション：法人営業
どのような人を求めているか
必要条件：未経験歓迎
求人概要
想定年収：400〜500万円
月給：30万円
月間平均残業時間：20時間以下
固定残業時間：30時間分
年間休日：120日
休日：完全週休2日制（土日）
勤務地：愛知県名古屋市
仕事内容：法人向け提案
福利厚生：社会保険完備
勤務時間：9:00〜18:00
会社情報
"""


def extracted(filename: str = "架空株式会社_No417803_営業.pdf") -> DocumentResult:
    result = CircusExtractor().extract(document(filename, SAMPLE))
    assign_proposed_names([result], Path("."))
    result.finalize_status()
    return result


def test_circus_extracts_fields_and_does_not_confuse_overtime() -> None:
    result = extracted()
    assert result.is_job_info_eligible
    assert result.company.value == "架空株式会社"
    assert result.raw_position.value == "法人営業"
    assert result.job_id == "417803"
    assert result.fields["年収"].value == "400〜500万円"
    assert result.fields["残業時間"].value == "20時間以下"
    assert result.extra_fields["固定残業時間"] == "30時間分"
    assert result.proposed_name == "架空株式会社（法人営業）-No417803.pdf"


def test_job_id_parsing_original_and_renamed() -> None:
    for name in (
        "株式会社テスト_No530148_営業 (1).pdf",
        "株式会社テスト（営業）-No530148.pdf",
        "株式会社テスト_No530148_営業.pdf",
    ):
        assert extract_job_id(Path(name)) == "530148"
    assert extract_job_id(Path("株式会社テスト（営業）-a1b2c3d4.pdf")) == ""


def test_format_mismatch_never_eligible() -> None:
    result = CircusExtractor().extract(document("generic.pdf", "求人名: テスト\n会社名: 架空株式会社"))
    assign_proposed_names([result], Path("."))
    assert not result.is_job_info_eligible
    assert result.proposed_name == ""


def existing_job(name: str, *, provider: str = "circus", job_id: str = "417803") -> dict[str, str]:
    row = {name: "" for name in [*JOB_INFO_FIELDS, *CIRCUS_FIELDS, "自由入力"]}
    row.update({"会社名": "架空株式会社", "募集ポジション": "法人営業",
                "元ファイル名": name, "新ファイル名": name, "処理状態": "成功",
                "媒体": provider, "求人ID": job_id, "自由入力": "保持"})
    return row


def save_existing(path: Path, jobs: list[dict[str, str]]) -> None:
    fields = [*JOB_INFO_FIELDS, *CIRCUS_FIELDS, "自由入力"]
    path.write_text(serialize_job_info(JobInfoDocument(fields, jobs, [])), encoding="utf-8")


def test_existing_job_preserved_when_pdf_fails(tmp_path: Path) -> None:
    target = tmp_path / "求人情報.json"
    previous = existing_job("架空株式会社_No417803_営業.pdf")
    save_existing(target, [previous])
    invalid = DocumentResult(source_path=Path(previous["元ファイル名"]), provider="circus",
                             error="読み取り失敗", job_id="417803")
    invalid.finalize_status()
    output = prepare_job_info_document([invalid], target, provider="circus")
    assert len(output.jobs) == 1
    assert output.jobs[0] == previous
    assert not output.stale_jobs


def test_circus_fields_are_saved_and_unknown_fields_preserved(tmp_path: Path) -> None:
    target = tmp_path / "求人情報.json"
    previous = existing_job("架空株式会社_No417803_営業.pdf")
    previous["採用区分"] = "以前の値"
    save_existing(target, [previous])
    result = extracted()
    result.extra_fields["採用区分"] = "中途採用"
    updated = prepare_job_info_document([result], target, provider="circus")
    assert updated.jobs[0]["採用区分"] == "中途採用"
    assert updated.jobs[0]["自由入力"] == "保持"
    assert updated.field_order[:20] == JOB_INFO_FIELDS
    assert updated.field_order[20:] == [*CIRCUS_FIELDS, "自由入力"]


def test_different_provider_record_is_untouched(tmp_path: Path) -> None:
    target = tmp_path / "求人情報.json"
    previous = existing_job("架空株式会社_No417803_営業.pdf", provider="jobins")
    save_existing(target, [previous])
    updated = prepare_job_info_document([extracted()], target, provider="circus")
    assert len(updated.jobs) == 2
    assert updated.jobs[1] == previous


def test_duplicate_same_id_different_content_holds_all(tmp_path: Path) -> None:
    left = CircusExtractor().extract(document("A_No417803.pdf", SAMPLE))
    right = CircusExtractor().extract(document("B_No417803.pdf", SAMPLE.replace("法人営業", "技術営業")))
    right.sha256 = "b" * 64
    assign_proposed_names([left, right], tmp_path)
    assert not left.is_job_info_eligible
    assert not right.is_job_info_eligible
    assert not left.proposed_name and not right.proposed_name


def test_duplicate_identical_content_keeps_one(tmp_path: Path) -> None:
    left = CircusExtractor().extract(document("A_No417803.pdf", SAMPLE))
    right = CircusExtractor().extract(document("B_No417803.pdf", SAMPLE))
    assign_proposed_names([left, right], tmp_path)
    assert sum(item.is_job_info_eligible for item in [left, right]) == 1
