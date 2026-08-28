from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .config import DEFAULT_ALIASES, all_headings, normalize_heading, normalize_match
from .models import DocumentResult, FieldValue, PdfDocument, TextLine


_BULLET_RE = re.compile(r"^[・■◆◇●○◎※★☆▶▷✓✔❶-❿]|^POINT", re.IGNORECASE)
_HEADER_RE = re.compile(r"^[【＜《].+[】＞》]$")
_OPTIONAL_RE = re.compile(r"歓迎|尚可|あれば|優遇|望ましい|歓迎します|歓迎です", re.IGNORECASE)
_INCENTIVE_RE = re.compile(r"インセンティブ|歩合|成果給|報奨金")


@dataclass(slots=True)
class ExtractedText:
    value: str
    heading: str
    page: int
    classification: str


def _display_clean(value: str) -> str:
    value = value.replace("\x00", "").replace("\ufffd", "").replace("\uffff", "")
    value = "".join(
        char
        for char in value
        if char in {"\n", "\t"} or unicodedata.category(char) not in {"Cc", "Cs", "Cn"}
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _identity_clean(value: str) -> str:
    return re.sub(r"\s+", " ", _display_clean(value)).strip()


def _join_fragments(parts: list[str]) -> str:
    return _display_clean("".join(part.strip() for part in parts if part.strip()))


def _split_embedded_bullets(value: str) -> list[str]:
    value = re.sub(r"\s+(?=[・■◆◇●○◎※★☆▶▷❶-❿])", "\n", value)
    return [part.strip() for part in value.splitlines() if part.strip()]


def _format_section(lines: list[str], duplicate_aliases: set[str]) -> str:
    blocks: list[str] = []
    normalized_duplicates = {normalize_heading(alias) for alias in duplicate_aliases}
    for raw in lines:
        raw = _display_clean(raw)
        if not raw:
            continue
        if normalize_heading(raw) in normalized_duplicates:
            continue
        for part in _split_embedded_bullets(raw):
            is_header = bool(_HEADER_RE.match(part))
            is_bullet = bool(_BULLET_RE.match(part))
            if not blocks or is_header or is_bullet or _HEADER_RE.match(blocks[-1]):
                blocks.append(part)
            else:
                blocks[-1] += part
    return "\n".join(blocks).strip()


def _dedupe_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        value = _display_clean(value)
        key = normalize_match(value)
        if value and key not in seen:
            output.append(value)
            seen.add(key)
    return output


class Extractor:
    def __init__(self, aliases: dict[str, list[str]]):
        self.aliases = aliases
        self.headings = all_headings(aliases)
        self._normalized_headings = {normalize_heading(value): value for value in self.headings}
        self._lines: list[TextLine] = []
        self._document: PdfDocument | None = None

    def extract(self, document: PdfDocument) -> DocumentResult:
        self._document = document
        self._lines = document.lines
        result = DocumentResult(
            source_path=document.path,
            sha256=document.sha256,
            page_count=document.page_count,
        )
        result.profile = self._detect_profile()

        if result.profile == "jobins":
            company, position, job_id = self._jobins_identity()
        else:
            company = self._identity_value("会社名")
            position = self._identity_value("募集ポジション")
            job_id = ""
        result.company = self._to_identity_field(company)
        result.raw_position = self._to_identity_field(position)
        result.job_id = job_id
        for field_name, field_value in (
            ("会社名", result.company),
            ("募集ポジション", result.raw_position),
        ):
            if not field_value.is_missing and field_value.classification in {"direct", "alias"}:
                result.add_report(
                    field_name,
                    field_value.classification,
                    f"{field_value.source_heading}から取得しました。",
                    field_value.source_heading,
                    field_value.page,
                )

        result.fields["雇用形態"] = self._to_field(self._first_value("雇用形態"))
        result.fields["年収"] = self._extract_annual_income(result)
        result.fields["給与"] = self._extract_salary()
        result.fields["インセンティブ"] = self._extract_incentive()
        result.fields["残業時間"] = self._extract_overtime()
        result.fields["年間休日"] = self._extract_annual_holidays(result)
        result.fields["休日"] = self._extract_holidays()
        result.fields["勤務地"] = self._extract_location()
        result.fields["転勤"] = self._extract_transfer()
        application = self._first_section("応募条件")
        if not application:
            required_as_application = self._first_section("必須資格")
            if required_as_application:
                application = ExtractedText(
                    value=required_as_application.value,
                    heading=required_as_application.heading,
                    page=required_as_application.page,
                    classification="fallback",
                )
                result.add_report(
                    "応募条件",
                    "fallback",
                    "応募条件の親見出しに本文がないため、必須条件を応募条件として採用しました。",
                    required_as_application.heading,
                    required_as_application.page,
                )
        result.fields["応募条件"] = self._to_field(application)
        result.fields["必須資格"] = self._extract_required(result.fields["応募条件"], result)
        result.fields["歓迎条件"] = self._to_field(self._first_section("歓迎条件"))
        result.fields["業務内容"] = self._extract_duties()
        result.fields["福利厚生"] = self._extract_benefits()
        result.fields["勤務時間"] = self._extract_working_hours()

        for field_name, field_value in result.fields.items():
            if field_value.is_missing:
                result.add_report(
                    field_name,
                    "missing",
                    "PDF内に該当項目の記載を確認できません。",
                )
        return result

    def _detect_profile(self) -> str:
        markers = {
            "採用企業名",
            "求人名",
            "求人ID",
            "職種分類（中分類）",
            "雇用形態",
            "仕事内容",
            "応募条件",
            "年収",
            "勤務地",
        }
        found = 0
        for marker in markers:
            if any(self._line_starts_with_alias(line.text, marker) or self._is_exact_heading(line.text, marker) for line in self._lines):
                found += 1
        if found >= 5 and any("求人ID" in line.text for line in self._lines):
            return "jobins"

        canonical_found = 0
        for canonical in self.aliases:
            if any(self._matches_any_alias(line.text, canonical) for line in self._lines):
                canonical_found += 1
        if canonical_found >= 3:
            return "generic"
        return "unsupported"

    def _jobins_identity(self) -> tuple[ExtractedText | None, ExtractedText | None, str]:
        job_id_idx = self._find_line_starting("求人ID")
        company = self._identity_value("会社名", aliases=["採用企業名"])
        if company is None:
            company = self._identity_value("会社名", aliases=["会社名"])
        position = self._identity_value("募集ポジション", aliases=["職種分類（中分類）"])
        job_id = ""

        if job_id_idx is not None:
            line = self._lines[job_id_idx]
            job_id = self._inline_after_label(line.text, "求人ID")

        return company, position, job_id

    def _identity_value(
        self,
        canonical: str,
        *,
        aliases: list[str] | None = None,
    ) -> ExtractedText | None:
        candidates: list[ExtractedText] = []
        selected_aliases = aliases if aliases is not None else self.aliases[canonical]
        for alias in selected_aliases:
            alias_normalized = unicodedata.normalize("NFKC", alias)
            for index, line in enumerate(self._lines):
                if not (
                    self._line_starts_with_alias(line.text, alias)
                    or self._is_exact_heading(line.text, alias)
                ):
                    continue

                original = line.text
                normalized = unicodedata.normalize("NFKC", original)
                alias_position = normalized.casefold().find(alias_normalized.casefold())
                value = ""
                if alias_position >= 0:
                    start = alias_position + len(alias_normalized)
                    end = len(normalized)
                    for heading in sorted(self.headings, key=len, reverse=True):
                        heading_normalized = unicodedata.normalize("NFKC", heading)
                        next_position = normalized.casefold().find(heading_normalized.casefold(), start)
                        if next_position >= 0 and next_position < end:
                            end = next_position
                    value = _display_clean(original[start:end].lstrip(" ：:"))

                if not value and self._is_exact_heading(line.text, alias):
                    content: list[str] = []
                    for following in self._lines[index + 1 :]:
                        if self._is_boundary_line(following.text):
                            break
                        content.append(following.text)
                    value = _format_section(content, set(selected_aliases))

                if value:
                    candidates.append(
                        ExtractedText(
                            value=value,
                            heading=alias,
                            page=line.page,
                            classification=self._classification(canonical, alias),
                        )
                    )

        distinct: dict[str, ExtractedText] = {}
        for candidate in candidates:
            key = normalize_match(candidate.value)
            if key and key not in distinct:
                distinct[key] = candidate
        if not distinct:
            return None
        values = list(distinct.values())
        earliest_page = min(value.page for value in values)
        values = [value for value in values if value.page == earliest_page]
        if len(values) == 1:
            return values[0]
        return ExtractedText(
            value="\n".join(value.value for value in values),
            heading=" / ".join(dict.fromkeys(value.heading for value in values)),
            page=values[0].page,
            classification="conflict",
        )

    def _to_field(self, extracted: ExtractedText | None) -> FieldValue:
        if extracted is None or not extracted.value.strip():
            return FieldValue()
        return FieldValue(
            value=_display_clean(extracted.value),
            classification=extracted.classification,
            source_heading=extracted.heading,
            page=extracted.page,
        )

    def _to_identity_field(self, extracted: ExtractedText | None) -> FieldValue:
        if extracted is None or not extracted.value.strip():
            return FieldValue()
        return FieldValue(
            value=_identity_clean(extracted.value),
            classification=extracted.classification,
            source_heading=extracted.heading,
            page=extracted.page,
        )

    def _find_line_starting(self, alias: str) -> int | None:
        for index, line in enumerate(self._lines):
            if self._line_starts_with_alias(line.text, alias):
                return index
        return None

    @staticmethod
    def _line_starts_with_alias(value: str, alias: str) -> bool:
        stripped = unicodedata.normalize("NFKC", value).lstrip()
        normalized_alias = unicodedata.normalize("NFKC", alias)
        if not stripped.casefold().startswith(normalized_alias.casefold()):
            return False
        tail = stripped[len(normalized_alias) :]
        return not tail or tail[0].isspace() or tail[0] in "：:"

    @staticmethod
    def _inline_after_label(value: str, alias: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        match = re.search(re.escape(unicodedata.normalize("NFKC", alias)), normalized, re.IGNORECASE)
        if not match:
            return ""
        tail = normalized[match.end() :]
        tail = re.sub(r"^[\s：:]+", "", tail)
        return _display_clean(tail)

    def _matches_any_alias(self, value: str, canonical: str) -> bool:
        return any(
            self._line_starts_with_alias(value, alias) or self._is_exact_heading(value, alias)
            for alias in sorted(self.aliases[canonical], key=len, reverse=True)
        )

    @staticmethod
    def _is_exact_heading(value: str, alias: str) -> bool:
        return normalize_heading(value) == normalize_heading(alias)

    def _is_any_heading(self, value: str) -> bool:
        normalized = normalize_heading(value)
        return bool(normalized) and normalized in self._normalized_headings

    def _is_boundary_line(self, value: str) -> bool:
        if self._is_any_heading(value):
            return True
        return any(self._line_starts_with_alias(value, heading) for heading in self.headings)

    def _classification(self, canonical: str, alias: str, fallback: bool = False) -> str:
        if fallback:
            return "fallback"
        primary = DEFAULT_ALIASES.get(canonical, [""])[0]
        return "direct" if normalize_match(alias) == normalize_match(primary) else "alias"

    def _inline_value(self, canonical: str, *, vertical: bool = False) -> ExtractedText | None:
        for alias in self.aliases[canonical]:
            for index, line in enumerate(self._lines):
                if not self._line_starts_with_alias(line.text, alias):
                    continue
                original_raw = line.text
                raw = unicodedata.normalize("NFKC", original_raw)
                position = raw.casefold().find(unicodedata.normalize("NFKC", alias).casefold())
                start = position + len(unicodedata.normalize("NFKC", alias))
                end = len(raw)
                for heading in sorted(self.headings, key=len, reverse=True):
                    normalized_heading_text = unicodedata.normalize("NFKC", heading)
                    next_position = raw.casefold().find(normalized_heading_text.casefold(), start)
                    if next_position >= 0 and next_position < end:
                        end = next_position
                current = _display_clean(original_raw[start:end].lstrip(" ：:"))

                fragments: list[str] = []
                if vertical:
                    before = 2 if canonical in {"転勤", "勤務地"} else 0
                    after = 4 if canonical == "勤務地" else 2
                    for nearby in self._lines[max(0, index - before) : min(len(self._lines), index + after + 1)]:
                        if nearby is not line and self._is_any_heading(nearby.text):
                            continue
                        nearby_raw = nearby.text
                        if len(nearby_raw) <= start:
                            continue
                        fragment = _display_clean(nearby_raw[start:min(end, len(nearby_raw))])
                        if not fragment:
                            continue
                        if any(normalize_match(name) in normalize_match(fragment) for name in self.headings):
                            continue
                        fragments.append(fragment)
                elif current:
                    fragments.append(current)

                value = _join_fragments(fragments)
                if value:
                    return ExtractedText(
                        value=value,
                        heading=alias,
                        page=line.page,
                        classification=self._classification(canonical, alias),
                    )
        return None

    def _section_for_alias(self, canonical: str, alias: str) -> ExtractedText | None:
        for index, line in enumerate(self._lines):
            if not self._is_exact_heading(line.text, alias):
                continue
            content: list[str] = []
            for candidate in self._lines[index + 1 :]:
                if self._is_boundary_line(candidate.text):
                    if self._is_exact_heading(candidate.text, alias):
                        continue
                    break
                content.append(candidate.text)
            value = _format_section(content, set(self.aliases[canonical]))
            if value:
                return ExtractedText(
                    value=value,
                    heading=alias,
                    page=line.page,
                    classification=self._classification(canonical, alias),
                )
        return None

    def _first_section(self, canonical: str) -> ExtractedText | None:
        for alias in self.aliases[canonical]:
            section = self._section_for_alias(canonical, alias)
            if section:
                return section
        return None

    def _first_value(self, canonical: str) -> ExtractedText | None:
        inline = self._inline_value(canonical)
        if inline:
            return inline
        return self._first_section(canonical)

    def _all_sections(self, canonical: str) -> list[ExtractedText]:
        values: list[ExtractedText] = []
        for alias in self.aliases[canonical]:
            section = self._section_for_alias(canonical, alias)
            if section:
                values.append(section)
        return values

    def _combine(self, canonical: str, values: list[ExtractedText]) -> FieldValue:
        if not values:
            return FieldValue()
        distinct = _dedupe_values([value.value for value in values])
        if not distinct:
            return FieldValue()
        return FieldValue(
            value="\n".join(distinct),
            classification=values[0].classification,
            source_heading=" / ".join(dict.fromkeys(value.heading for value in values)),
            page=values[0].page,
        )

    def _extract_annual_income(self, result: DocumentResult) -> FieldValue:
        direct = self._inline_value("年収") or self._first_section("年収")
        if direct:
            return self._to_field(direct)
        salary_text = "\n".join(value.value for value in self._all_sections("給与"))
        match = re.search(r"(?:想定年収|予定年収|年収)\s*[：:]?\s*([^\n。]{1,50}?万円(?:[^\n。]{0,15})?)", salary_text)
        if match:
            return FieldValue(
                value=_display_clean(match.group(1)),
                classification="fallback",
                source_heading="給与",
            )
        return FieldValue()

    def _extract_salary(self) -> FieldValue:
        values: list[ExtractedText] = []
        for alias in ["月給", "年俸", "時給", "日給"]:
            if alias not in self.aliases["給与"]:
                continue
            inline = self._inline_for_specific_alias("給与", alias)
            if inline:
                if alias in {"月給", "年俸", "時給", "日給"}:
                    inline.value = re.split(r"インセンティブ|歩合|成果給|報奨金", inline.value, maxsplit=1)[0]
                    inline.value = inline.value.rstrip("＋+ ")
                inline.value = f"{alias}：{inline.value}"
                values.append(inline)
                break
        for alias in ["給与詳細", "給与条件", "賃金", "給与"]:
            if alias not in self.aliases["給与"]:
                continue
            section = self._section_for_alias("給与", alias)
            if section:
                prefix = "給与詳細：\n" if alias in {"給与詳細", "給与条件"} else f"{alias}：\n"
                section.value = prefix + section.value
                values.append(section)
                break
        return self._combine("給与", values)

    def _inline_for_specific_alias(self, canonical: str, alias: str, *, vertical: bool = False) -> ExtractedText | None:
        direct = self._table_value_anywhere(canonical, alias)
        if direct:
            return direct
        original = self.aliases[canonical]
        self.aliases[canonical] = [alias]
        try:
            return self._inline_value(canonical, vertical=vertical)
        finally:
            self.aliases[canonical] = original

    def _table_value_anywhere(self, canonical: str, alias: str) -> ExtractedText | None:
        normalized_alias = unicodedata.normalize("NFKC", alias)
        for line in self._lines:
            original = line.text
            normalized = unicodedata.normalize("NFKC", original)
            position = normalized.casefold().find(normalized_alias.casefold())
            if position < 0:
                continue
            if position > 0 and not normalized[position - 1].isspace():
                continue
            start = position + len(normalized_alias)
            end = len(normalized)
            for heading in sorted(self.headings, key=len, reverse=True):
                heading_norm = unicodedata.normalize("NFKC", heading)
                next_position = normalized.casefold().find(heading_norm.casefold(), start)
                if next_position >= 0 and next_position < end:
                    end = next_position
            value = _display_clean(original[start:end].lstrip(" ：:"))
            if value:
                return ExtractedText(
                    value=value,
                    heading=alias,
                    page=line.page,
                    classification=self._classification(canonical, alias),
                )
        return None

    def _extract_incentive(self) -> FieldValue:
        direct = self._first_value("インセンティブ")
        if direct and normalize_match(direct.value) not in {normalize_match(alias) for alias in self.aliases["インセンティブ"]}:
            return self._to_field(direct)

        relevant: list[str] = []
        for canonical in ("給与", "福利厚生"):
            relevant.extend(value.value for value in self._all_sections(canonical))
        matches: list[str] = []
        for text in relevant:
            for part in re.split(r"[\n。]", text):
                if _INCENTIVE_RE.search(part):
                    matches.append(part.strip())
        matches = _dedupe_values(matches)
        if matches:
            return FieldValue(
                value="\n".join(matches),
                classification="fallback",
                source_heading="給与・福利厚生",
            )
        return FieldValue()

    def _extract_overtime(self) -> FieldValue:
        sections = self._all_sections("残業時間")
        if sections:
            return self._combine("残業時間", sections)

        pattern = re.compile(
            r"(?:月平均残業時間|平均残業時間|残業(?:時間)?(?:は)?(?:月平均|月)?)\s*[：:]?\s*(?:約|平均)?\s*\d+(?:\.\d+)?\s*(?:時間|h|H)(?:程度|以内)?"
        )
        for match in pattern.finditer(self._document.text if self._document else ""):
            prefix = (self._document.text if self._document else "")[max(0, match.start() - 12) : match.start()]
            if re.search(r"固定|みなし", prefix):
                continue
            return FieldValue(
                value=_display_clean(match.group(0)),
                classification="fallback",
                source_heading="本文",
            )
        return FieldValue()

    def _extract_annual_holidays(self, result: DocumentResult) -> FieldValue:
        direct = self._first_section("年間休日") or self._inline_value("年間休日")
        all_matches = re.findall(
            r"(?:年間休日|年休)\s*[：:]?\s*(\d+)\s*日(?:[^。\n]{0,25})?",
            unicodedata.normalize("NFKC", self._document.text if self._document else ""),
        )
        if direct:
            direct_numbers = set(re.findall(r"\d+", unicodedata.normalize("NFKC", direct.value)))
            conflicting = sorted({number for number in all_matches if number not in direct_numbers})
            field = self._to_field(direct)
            if re.fullmatch(r"[0-9０-９]+", field.value):
                field.value += "日"
            if conflicting:
                field.classification = "conflict"
                result.add_report(
                    "年間休日",
                    "conflict",
                    f"専用欄を採用しました。本文には異なる候補があります: {', '.join(conflicting)}日",
                    direct.heading,
                    direct.page,
                )
            return field

        holiday_text = "\n".join(value.value for value in self._all_sections("休日"))
        match = re.search(r"(?:年間休日|年休)\s*[：:]?\s*\d+\s*日(?:[^。\n]{0,25})?", holiday_text)
        if not match:
            match = re.search(r"(?:年間休日|年休)\s*[：:]?\s*\d+\s*日(?:[^。\n]{0,25})?", self._document.text if self._document else "")
        if match:
            return FieldValue(
                value=_display_clean(match.group(0)),
                classification="fallback",
                source_heading="休日",
            )
        return FieldValue()

    def _extract_holidays(self) -> FieldValue:
        values = self._all_sections("休日")
        inline = self._inline_value("休日")
        if inline:
            values.insert(0, inline)
        return self._combine("休日", values)

    def _extract_location(self) -> FieldValue:
        values: list[ExtractedText] = []
        plain = self._plain_between("勤務地", "勤務地詳細")
        if plain:
            values.append(ExtractedText(plain, "勤務地", self._page_for_text("勤務地"), "direct"))
        values.extend(self._all_sections("勤務地"))
        return self._combine("勤務地", values)

    def _extract_transfer(self) -> FieldValue:
        plain = self._plain_between("転勤の可能性", "勤務地")
        if plain:
            compact = re.sub(r"\s+", "", plain)
            if compact in {"なし", "あり", "当面なし", "最大限考慮"}:
                return FieldValue(
                    value=compact,
                    classification="direct",
                    source_heading="転勤の可能性",
                    page=self._page_for_text("転勤の可能性"),
                )
        inline = self._inline_value("転勤")
        if inline:
            vertical = self._inline_value("転勤", vertical=True)
            candidate = vertical or inline
            compact = re.sub(r"\s+", "", candidate.value)
            if compact in {"なし", "あり", "当面なし", "最大限考慮"}:
                candidate.value = compact
            return self._to_field(candidate)
        return self._to_field(self._first_section("転勤"))

    def _plain_between(self, start_heading: str, end_heading: str) -> str:
        if not self._document:
            return ""
        pattern = re.compile(
            re.escape(start_heading) + r"\s*(.*?)\s*" + re.escape(end_heading),
            re.DOTALL,
        )
        match = pattern.search(self._document.plain_text)
        return _display_clean(match.group(1)) if match else ""

    def _page_for_text(self, value: str) -> int:
        for line in self._lines:
            if value in line.text:
                return line.page
        return 1

    def _extract_required(self, application: FieldValue, result: DocumentResult) -> FieldValue:
        direct = self._first_section("必須資格") or self._inline_value("必須資格")
        if direct:
            return self._to_field(direct)
        if application.is_missing:
            return FieldValue()

        parts = _split_embedded_bullets(application.value)
        required = [part for part in parts if not _OPTIONAL_RE.search(part)]
        if not required:
            return FieldValue()
        value = "\n".join(required)
        result.add_report(
            "必須資格",
            "fallback",
            "独立した必須欄がないため、応募条件から任意条件を除いて抽出しました。",
            application.source_heading,
            application.page,
        )
        return FieldValue(
            value=value,
            classification="fallback",
            source_heading=application.source_heading,
            page=application.page,
        )

    def _extract_duties(self) -> FieldValue:
        values = self._all_sections("業務内容")
        return self._combine("業務内容", values)

    def _extract_benefits(self) -> FieldValue:
        values = self._all_sections("福利厚生")
        return self._combine("福利厚生", values)

    def _extract_working_hours(self) -> FieldValue:
        value = self._first_section("勤務時間") or self._inline_value("勤務時間")
        if not value:
            return FieldValue()
        text = value.value
        text = re.split(r"[＜<]休日[・･]休暇[＞>]", text, maxsplit=1)[0].strip()
        value.value = text
        return self._to_field(value) if text else FieldValue()
