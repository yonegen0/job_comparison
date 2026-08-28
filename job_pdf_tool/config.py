from __future__ import annotations

import json
import unicodedata
from pathlib import Path


DEFAULT_ALIASES: dict[str, list[str]] = {
    "会社名": ["採用企業名", "会社名", "企業名", "勤務先企業"],
    "募集ポジション": ["職種分類（中分類）", "求人名", "職種名", "募集職種", "募集ポジション", "ポジション"],
    "雇用形態": ["雇用形態", "契約形態"],
    "年収": ["年収", "想定年収", "予定年収"],
    "給与": ["月給", "給与", "給与詳細", "給与条件", "賃金", "年俸", "時給", "日給"],
    "インセンティブ": ["インセンティブ", "歩合", "成果給", "報奨金"],
    "残業時間": ["月平均残業時間", "月平均残業時間詳細", "平均残業時間", "残業時間"],
    "年間休日": ["年間休日", "年休"],
    "休日": ["休日", "休日詳細", "休日・休暇", "休暇"],
    "勤務地": ["勤務地", "勤務地詳細", "就業場所", "配属先", "勤務地（変更の範囲）"],
    "転勤": ["転勤", "転勤の可能性", "異動"],
    "応募条件": ["応募条件", "応募資格", "応募要件"],
    "必須資格": ["必須条件", "必須要件", "MUST", "必須"],
    "歓迎条件": ["歓迎条件", "歓迎要件", "尚可", "WANT"],
    "業務内容": ["仕事内容", "業務内容", "職務内容", "仕事内容（変更の範囲）", "業務内容（変更の範囲）"],
    "福利厚生": ["福利厚生", "福利厚生詳細", "待遇", "加入保険"],
    "勤務時間": ["勤務時間", "就業時間", "労働時間", "勤務体系"],
}


EXTRA_HEADINGS = {
    "求人票",
    "求人ID",
    "応募区分",
    "職種（大分類）",
    "職種分類（中分類）",
    "必要な経験年数（職種）",
    "必要な経験年数（業種）",
    "その他求める経験",
    "採用人数",
    "特徴",
    "募集背景",
    "求める人物像",
    "給与範囲",
    "賞与",
    "賞与等詳細",
    "ストックオプション",
    "諸手当",
    "給与・報酬",
    "試用期間",
    "試用期間（詳細）",
    "選考フロー",
    "受動喫煙対策について",
    "受動喫煙対策詳細",
    "会社概要",
    "株式公開",
    "業種分類",
    "売上高",
    "資本金",
    "従業員数",
    "設立年月",
    "会社概要（採用企業）",
}


def normalize_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\x00", "")
    return "".join(value.split()).casefold()


def normalize_heading(value: str) -> str:
    value = value.strip().strip("【】[]＜＞<>：:")
    return normalize_match(value)


def load_aliases(path: Path | None) -> dict[str, list[str]]:
    aliases = {key: list(values) for key, values in DEFAULT_ALIASES.items()}
    if path is None:
        return aliases
    with path.open("r", encoding="utf-8") as handle:
        custom = json.load(handle)
    if not isinstance(custom, dict):
        raise ValueError("見出し別名JSONの最上位はオブジェクトである必要があります。")
    for canonical, values in custom.items():
        if canonical not in aliases:
            raise ValueError(f"未対応の正規項目です: {canonical}")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{canonical} の値は文字列配列である必要があります。")
        for value in values:
            if value and value not in aliases[canonical]:
                aliases[canonical].append(value)
    return aliases


def all_headings(aliases: dict[str, list[str]]) -> set[str]:
    headings = set(EXTRA_HEADINGS)
    for values in aliases.values():
        headings.update(values)
    return headings
