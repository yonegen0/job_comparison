from pathlib import Path

from job_pdf_tool.config import load_aliases
from job_pdf_tool.extractor import Extractor
from job_pdf_tool.models import PdfDocument, TextLine
from job_pdf_tool.naming import resolve_positions


def document_from_text(text: str) -> PdfDocument:
    return PdfDocument(
        path=Path("sample.pdf"),
        sha256="0" * 64,
        page_count=1,
        lines=[TextLine(1, line) for line in text.splitlines()],
        text=text,
        plain_text=text,
    )


def test_generic_alias_layout_and_missing_value() -> None:
    document = document_from_text(
        """企業名：テスト株式会社
募集職種：データエンジニア
契約形態：正社員
想定年収：500万円～650万円
仕事内容
データ基盤の設計と開発を担当します。
応募資格
・Pythonの実務経験
・クラウド経験があれば歓迎
歓迎要件
・AWS認定資格
就業時間
9:00～18:00
"""
    )

    result = Extractor(load_aliases(None)).extract(document)

    assert result.profile == "generic"
    assert result.company.value == "テスト株式会社"
    assert result.raw_position.value == "データエンジニア"
    assert result.fields["雇用形態"].value == "正社員"
    assert "500万円" in result.fields["年収"].value
    assert result.fields["業務内容"].value == "データ基盤の設計と開発を担当します。"
    assert result.fields["必須資格"].value == "・Pythonの実務経験"
    assert result.fields["勤務時間"].value == "9:00～18:00"
    assert result.fields["インセンティブ"].value == "記載なし"


def test_fixed_overtime_is_not_actual_overtime() -> None:
    text = """採用企業名 例示株式会社
求人名 事務スタッフ
求人ID x
雇用形態 正社員
仕事内容
事務作業
応募条件
未経験可
年収 400万円
勤務地 東京
給与詳細
固定残業時間30時間分を含む
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(text))
    assert result.fields["残業時間"].value == "記載なし"


def test_annual_holiday_dedicated_value_wins_conflict() -> None:
    text = """採用企業名 例示株式会社
求人名 事務スタッフ
求人ID x
雇用形態 正社員
仕事内容
事務作業
応募条件
未経験可
年収 400万円
勤務地 東京
勤務時間
9:00～18:00 年間休日：133日
年間休日
123日＋有給休暇5日
休日・休暇
週休二日制
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(text))
    assert result.fields["年間休日"].value == "123日＋有給休暇5日"
    assert result.fields["年間休日"].classification == "conflict"


def test_jobins_identity_accepts_values_on_following_lines() -> None:
    text = """採用企業名
例示株式会社
求人名
とても長い求人タイトル
求人ID x
職種分類（中分類）
内勤営業、カウンターセールス
雇用形態 正社員
仕事内容
法人営業
応募条件
経験不問
年収 400万円
勤務地 東京
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(text))
    resolve_positions([result])
    result.validate_required_identity()

    assert result.profile == "jobins"
    assert result.company.value == "例示株式会社"
    assert result.raw_position.value == "内勤営業、カウンターセールス"
    assert result.raw_position.source_heading == "職種分類（中分類）"
    assert result.resolved_position == "内勤営業、カウンターセールス"
    assert result.position_resolution == "middle_category"
    assert result.has_required_identity


def test_jobins_company_prefers_recruiting_company_and_falls_back_to_company() -> None:
    preferred = """採用企業名 第一株式会社
会社名 第二株式会社
求人名 長い求人名
求人ID x
職種分類（中分類） 営業事務、営業アシスタント
雇用形態 正社員
仕事内容 事務
応募条件 不問
年収 400万円
勤務地 東京
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(preferred))
    assert result.company.value == "第一株式会社"
    assert result.company.source_heading == "採用企業名"
    assert result.raw_position.value == "営業事務、営業アシスタント"

    fallback = preferred.replace("採用企業名 第一株式会社\n", "")
    result = Extractor(load_aliases(None)).extract(document_from_text(fallback))
    assert result.company.value == "第二株式会社"
    assert result.company.source_heading == "会社名"


def test_jobins_missing_middle_category_is_not_replaced_by_job_title() -> None:
    text = """採用企業名 例示株式会社
求人名 キャリアアドバイザー
求人ID x
雇用形態 正社員
仕事内容 法人営業
応募条件 経験不問
年収 400万円
勤務地 東京
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(text))
    resolve_positions([result])
    result.validate_required_identity()

    assert result.raw_position.is_missing
    assert not result.has_required_identity
    assert any(issue.target_field == "募集ポジション" for issue in result.required_issues)


def test_conflicting_company_values_are_not_valid_identity() -> None:
    text = """企業名：例示株式会社
会社名：別会社株式会社
募集職種：事務スタッフ
雇用形態：正社員
"""
    result = Extractor(load_aliases(None)).extract(document_from_text(text))
    resolve_positions([result])
    result.validate_required_identity()

    assert result.company.classification == "conflict"
    assert not result.has_required_identity
    assert result.required_issues[0].classification == "conflict"
