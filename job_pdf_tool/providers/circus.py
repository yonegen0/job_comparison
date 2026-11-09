from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from ..models import DocumentResult, EXTRACTED_FIELDS, FieldValue, MISSING_VALUE, PdfDocument

CIRCUS_FIELDS = (
    "媒体", "求人ID", "求人タイトル", "職種カテゴリ", "採用区分", "最終学歴",
    "職種経験", "業種経験", "選考で評価されやすい人物像", "選考フロー", "固定残業時間",
)

# These headings are intentionally scoped to circus; the JoBins alias registry is untouched.
LABELS = {
    "会社名": ("採用企業名", "募集企業名", "企業名", "会社名"),
    "募集ポジション": ("募集ポジション", "募集職種名", "募集職種", "職種名", "ポジション", "職種"),
    "求人タイトル": ("求人タイトル", "求人名", "タイトル"),
    "職種カテゴリ": ("職種カテゴリ", "職種カテゴリー", "職種分類", "職種（中分類）"),
    "雇用形態": ("雇用形態", "契約形態"),
    "年収": ("想定年収", "予定年収", "年収"),
    "給与": ("月給", "給与詳細", "給与条件", "基本給", "給与"),
    "インセンティブ": ("インセンティブ", "歩合給", "成果給", "歩合"),
    "残業時間": ("月間平均残業時間", "月平均残業時間", "平均残業時間"),
    "年間休日": ("年間休日", "年間休日数"),
    "休日": ("休日・休暇", "休日休暇", "休日", "休暇"),
    "勤務地": ("勤務地詳細", "勤務先住所", "勤務地", "就業場所"),
    "転勤": ("転勤の可能性", "転勤"),
    "応募条件": ("必要条件", "応募条件", "応募資格", "応募要件"),
    "必須資格": ("必須資格", "必須条件", "必要資格"),
    "歓迎条件": ("歓迎条件", "歓迎要件", "歓迎する経験"),
    "業務内容": ("仕事内容", "業務内容", "職務内容"),
    "福利厚生": ("福利厚生", "待遇・福利厚生", "福利厚生・待遇"),
    "勤務時間": ("勤務時間", "就業時間", "勤務体系"),
    "採用区分": ("採用区分", "応募区分"),
    "最終学歴": ("最終学歴", "学歴"),
    "職種経験": ("職種経験", "職種経験年数"),
    "業種経験": ("業種経験", "業種経験年数"),
    "選考で評価されやすい人物像": ("内定の可能性が高い人", "選考で評価されやすい人物像", "求める人物像"),
    "選考フロー": ("選考フロー", "選考プロセス"),
    "固定残業時間": ("固定残業時間", "みなし残業時間", "固定残業代の対象時間"),
}
SECTION_HEADINGS = {
    "どのような人を求めているか", "求人概要", "給与・年収例", "会社情報",
    "求人詳細", "募集要項", "選考について", "企業情報", "PRポイント",
}
_HEADINGS = {alias: key for key, aliases in LABELS.items() for alias in aliases}
_SORTED_LABELS = sorted(_HEADINGS, key=len, reverse=True)
_NO_ID = re.compile(r"(?:^|[_\s（(\-])No[\s_\-]*(\d+)(?=$|[_\s）)\-\.])", re.I)


def extract_job_id(path: Path) -> str:
    match = _NO_ID.search(unicodedata.normalize("NFKC", path.stem))
    return match.group(1) if match else ""


def _clean(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.strip(" \t:：|｜")).strip()


def _read_rows(document: PdfDocument) -> list[tuple[int, str]]:
    source = [(line.page, line.text) for line in document.lines]
    if not any("必要条件" in value or "求人概要" in value for _, value in source):
        source = [(1, line) for line in document.plain_text.splitlines()]
    return [(page, value) for page, value in source if value.strip()]


def _heading_piece(piece: str) -> tuple[str, str] | None:
    normalized = unicodedata.normalize("NFKC", piece.strip()).strip("■●◆【】<> ")
    for label in _SORTED_LABELS:
        if not normalized.startswith(unicodedata.normalize("NFKC", label)):
            continue
        tail = normalized[len(unicodedata.normalize("NFKC", label)):]
        if not tail:
            return label, ""
        if tail[0] in ":：|｜" or (len(tail) > 1 and tail[0].isspace()):
            return label, _clean(tail)
    return None


def _is_section(piece: str) -> bool:
    text = unicodedata.normalize("NFKC", piece.strip()).strip("■●◆【】<> ")
    return text in SECTION_HEADINGS


def _collect(document: PdfDocument) -> dict[str, list[FieldValue]]:
    rows = _read_rows(document)
    found: dict[str, list[FieldValue]] = defaultdict(list)
    active: tuple[str, int, int] | None = None
    for page, raw in rows:
        # pypdf layout mode separates table cells with multiple spaces.
        pieces = [piece.strip() for piece in re.split(r"\s{3,}", raw) if piece.strip()]
        for index, piece in enumerate(pieces):
            if _is_section(piece):
                active = None
                continue
            heading = _heading_piece(piece)
            if heading:
                label, inline = heading
                key = _HEADINGS[label]
                if not inline and index + 1 < len(pieces):
                    next_piece = pieces[index + 1]
                    if not _heading_piece(next_piece) and not _is_section(next_piece):
                        inline = _clean(next_piece)
                if inline:
                    found[key].append(FieldValue(inline, "direct", label, page))
                    active = (key, page, len(found[key]) - 1)
                else:
                    found[key].append(FieldValue("", "missing", label, page))
                    active = (key, page, len(found[key]) - 1)
                continue
            if active is not None:
                key, previous_page, item_index = active
                if page == previous_page and (
                    not _heading_piece(piece) and not _is_section(piece)
                ):
                    previous = found[key][item_index]
                    # Don't pull unrelated table cells into a scalar identity or money value.
                    if key in {"会社名", "募集ポジション", "職種カテゴリ", "年収", "残業時間", "年間休日", "転勤"}:
                        if not previous.value:
                            previous.value = _clean(piece)
                            previous.classification = "direct"
                    else:
                        previous.value = (previous.value + "\n" + _clean(piece)).strip()
                        previous.classification = "direct"
    return found


def _pick(rows: dict[str, list[FieldValue]], key: str, *, identity: bool = False) -> FieldValue:
    values = [field for field in rows.get(key, ()) if field.value and field.value != MISSING_VALUE]
    if not values:
        return FieldValue()
    first = values[0]
    if identity:
        distinct = {"".join(unicodedata.normalize("NFKC", f.value).split()).casefold() for f in values}
        if len(distinct) > 1:
            return FieldValue("\n".join(f.value for f in values), "conflict", first.source_heading, first.page)
    return first


def _company_from_header(document: PdfDocument) -> FieldValue:
    for line in document.lines[:15]:
        raw = _clean(line.text)
        # Only use a standalone company name in the PDF header, not an agency footer.
        if re.fullmatch(r"(?:株式会社|有限会社|合同会社|.+株式会社|.+有限会社|.+合同会社)[^\s：:]{0,45}", raw):
            return FieldValue(raw, "direct", "PDF冒頭の企業名", line.page)
    return FieldValue()


def _signature(document: PdfDocument) -> bool:
    text = document.text or document.plain_text
    section_hits = sum(heading in text for heading in SECTION_HEADINGS)
    return section_hits >= 2 and (
        "必要条件" in text or "どのような人を求めているか" in text
    ) and ("会社情報" in text or "求人概要" in text or "給与・年収例" in text)


class CircusExtractor:
    """Read circus text and layout tables without modifying the JoBins extractor."""

    def __init__(self, aliases: dict[str, list[str]] | None = None):
        self.aliases = aliases or {}

    def extract(self, document: PdfDocument) -> DocumentResult:
        result = DocumentResult(
            source_path=document.path, sha256=document.sha256, page_count=document.page_count,
            profile="circus" if _signature(document) else "unsupported",
            provider="circus",
        )
        if result.profile != "circus":
            result.error = "選択媒体circusとPDF形式が一致しません。"
            result.add_report("ファイル", "warning", result.error)
            return result

        rows = _collect(document)
        result.company = _pick(rows, "会社名", identity=True)
        if result.company.is_missing:
            result.company = _company_from_header(document)
        result.raw_position = _pick(rows, "募集ポジション", identity=True)
        if result.raw_position.is_missing:
            category = _pick(rows, "職種カテゴリ")
            if not category.is_missing:
                result.raw_position = FieldValue(
                    category.value, "alias", "職種カテゴリ", category.page,
                )
                result.add_report("募集ポジション", "warning", "具体的な募集職種がなく職種カテゴリを代用しました。",
                                  category.source_heading, category.page)
        result.job_id = extract_job_id(document.path)
        for name in EXTRACTED_FIELDS:
            result.fields[name] = _pick(rows, name)
        # 必要条件は応募条件であり、歓迎条件や人物像は混入させない。
        if result.fields["必須資格"].is_missing:
            required = _pick(rows, "応募条件")
            if not required.is_missing:
                result.fields["必須資格"] = FieldValue(
                    required.value, "alias", required.source_heading, required.page,
                )
        result.extra_fields = {"媒体": "circus", "求人ID": result.job_id or MISSING_VALUE}
        for name in CIRCUS_FIELDS[2:]:
            value = _pick(rows, name)
            result.extra_fields[name] = value.value
            if not value.is_missing:
                result.add_report(name, value.classification, f"{value.source_heading}から取得しました。",
                                  value.source_heading, value.page)
        for name, value in (("会社名", result.company), ("募集ポジション", result.raw_position)):
            if not value.is_missing:
                result.add_report(name, value.classification, f"{value.source_heading}から取得しました。",
                                  value.source_heading, value.page)
        for name, value in result.fields.items():
            if value.is_missing:
                result.add_report(name, "missing", "PDF内に該当項目の記載を確認できません。")
        return result
