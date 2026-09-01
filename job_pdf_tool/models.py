from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


MISSING_VALUE = "記載なし"

_INVALID_IDENTITY_VALUES = {
    "",
    "記載なし",
    "なし",
    "不明",
    "未定",
    "n/a",
    "na",
}
_IDENTITY_HEADINGS = {
    "会社名": {"採用企業名", "会社名", "企業名", "勤務先企業"},
    "募集ポジション": {
        "職種分類（中分類）",
        "求人名",
        "職種名",
        "募集職種",
        "募集ポジション",
        "ポジション",
    },
}

EXTRACTED_FIELDS = [
    "雇用形態",
    "年収",
    "給与",
    "インセンティブ",
    "残業時間",
    "年間休日",
    "休日",
    "勤務地",
    "転勤",
    "応募条件",
    "必須資格",
    "歓迎条件",
    "業務内容",
    "福利厚生",
    "勤務時間",
]

JOB_INFO_FIELDS = [
    "会社名",
    "募集ポジション",
    "元ファイル名",
    "新ファイル名",
    "処理状態",
    *EXTRACTED_FIELDS,
]

PROCESS_REPORT_FIELDS = [
    "元ファイル名",
    "新ファイル名",
    "SHA-256",
    "処理状態",
    "対象項目",
    "判定",
    "採用元見出し",
    "ページ",
    "メッセージ",
]


@dataclass(slots=True)
class TextLine:
    page: int
    text: str


@dataclass(slots=True)
class PdfDocument:
    path: Path
    sha256: str
    page_count: int
    lines: list[TextLine]
    text: str
    plain_text: str = ""


@dataclass(slots=True)
class FieldValue:
    value: str = MISSING_VALUE
    classification: str = "missing"
    source_heading: str = ""
    page: int | None = None

    @property
    def is_missing(self) -> bool:
        return self.classification == "missing" or self.value == MISSING_VALUE


@dataclass(slots=True)
class ReportItem:
    target_field: str
    classification: str
    message: str
    source_heading: str = ""
    page: int | None = None


@dataclass(slots=True)
class DocumentResult:
    source_path: Path
    sha256: str = ""
    page_count: int = 0
    profile: str = "unknown"
    company: FieldValue = field(default_factory=FieldValue)
    raw_position: FieldValue = field(default_factory=FieldValue)
    short_position: str = ""
    resolved_position: str = ""
    position_resolution: str = ""
    position_qualifiers: list[str] = field(default_factory=list)
    job_id: str = ""
    fields: dict[str, FieldValue] = field(
        default_factory=lambda: {name: FieldValue() for name in EXTRACTED_FIELDS}
    )
    reports: list[ReportItem] = field(default_factory=list)
    required_issues: list[ReportItem] = field(default_factory=list)
    identity_validated: bool = False
    proposed_name: str = ""
    status: str = "改名保留"
    error: str = ""

    def add_report(
        self,
        target_field: str,
        classification: str,
        message: str,
        source_heading: str = "",
        page: int | None = None,
    ) -> None:
        self.reports.append(
            ReportItem(
                target_field=target_field,
                classification=classification,
                message=message,
                source_heading=source_heading,
                page=page,
            )
        )

    @staticmethod
    def _identity_issue(field_name: str, value: FieldValue) -> ReportItem | None:
        if value.classification == "conflict":
            return ReportItem(
                target_field=field_name,
                classification="conflict",
                message=f"{field_name}に異なる候補が複数あるため、求人情報・リネーム対象から除外します。",
                source_heading=value.source_heading,
                page=value.page,
            )
        if value.classification not in {"direct", "alias"}:
            classification = "missing" if value.is_missing else "invalid"
            return ReportItem(
                target_field=field_name,
                classification=classification,
                message=f"{field_name}を認識済み見出しから確定できないため、求人情報・リネーム対象から除外します。",
                source_heading=value.source_heading,
                page=value.page,
            )

        raw = value.value
        if "\ufffd" in raw or "\uffff" in raw:
            return ReportItem(
                target_field=field_name,
                classification="invalid",
                message=f"{field_name}に文字化け記号があるため、求人情報・リネーム対象から除外します。",
                source_heading=value.source_heading,
                page=value.page,
            )

        normalized = unicodedata.normalize("NFKC", raw)
        compact = "".join(normalized.split()).strip("-‐‑‒–—―ー_・.。:").casefold()
        invalid_values = {unicodedata.normalize("NFKC", item).casefold() for item in _INVALID_IDENTITY_VALUES}
        headings = {
            "".join(unicodedata.normalize("NFKC", item).split()).casefold()
            for item in _IDENTITY_HEADINGS[field_name]
        }
        source_heading = "".join(unicodedata.normalize("NFKC", value.source_heading).split()).casefold()
        if compact in invalid_values or compact in headings or (source_heading and compact == source_heading):
            return ReportItem(
                target_field=field_name,
                classification="missing",
                message=f"{field_name}に有効な値がないため、求人情報・リネーム対象から除外します。",
                source_heading=value.source_heading,
                page=value.page,
            )
        if not re.search(r"[^\W_]", normalized, re.UNICODE):
            return ReportItem(
                target_field=field_name,
                classification="invalid",
                message=f"{field_name}が記号だけのため、求人情報・リネーム対象から除外します。",
                source_heading=value.source_heading,
                page=value.page,
            )
        return None

    def validate_required_identity(self) -> None:
        previous_ids = {id(item) for item in self.required_issues}
        if previous_ids:
            self.reports = [item for item in self.reports if id(item) not in previous_ids]
        self.required_issues.clear()
        self.identity_validated = True
        if self.error:
            return

        for field_name, field_value in (
            ("会社名", self.company),
            ("募集ポジション", self.raw_position),
        ):
            issue = self._identity_issue(field_name, field_value)
            if issue is not None:
                self.required_issues.append(issue)
                self.reports.append(issue)

        if not any(issue.target_field == "募集ポジション" for issue in self.required_issues):
            if not self.resolved_position.strip():
                issue = ReportItem(
                    target_field="募集ポジション",
                    classification="invalid",
                    message="募集ポジションの出力値を生成できないため、求人情報・リネーム対象から除外します。",
                    source_heading=self.raw_position.source_heading,
                    page=self.raw_position.page,
                )
                self.required_issues.append(issue)
                self.reports.append(issue)

    @property
    def has_required_identity(self) -> bool:
        return self.identity_validated and not self.required_issues

    @property
    def is_job_info_eligible(self) -> bool:
        return not self.error and self.has_required_identity

    @property
    def is_rename_eligible(self) -> bool:
        return self.is_job_info_eligible and bool(self.proposed_name)

    def finalize_status(self) -> None:
        if self.error:
            self.status = "エラー"
            return
        if self.identity_validated and self.required_issues:
            self.status = "エラー"
            return
        if not self.proposed_name:
            self.status = "改名保留"
            return
        if any(value.is_missing or value.classification == "conflict" for value in self.fields.values()):
            self.status = "部分成功"
            return
        if any(item.classification in {"conflict", "warning"} for item in self.reports):
            self.status = "部分成功"
            return
        self.status = "成功"

    def job_info_record(self) -> dict[str, str]:
        if not self.is_job_info_eligible:
            raise ValueError(f"必須項目が不正なPDFは求人情報へ出力できません: {self.source_path.name}")
        row = {
            "会社名": self.company.value,
            "募集ポジション": self.resolved_position,
            "元ファイル名": self.source_path.name,
            "新ファイル名": self.proposed_name or MISSING_VALUE,
            "処理状態": self.status,
        }
        row.update({name: self.fields[name].value for name in EXTRACTED_FIELDS})
        return row

    # Python API compatibility for callers that have not migrated their terminology yet.
    @property
    def is_main_csv_eligible(self) -> bool:
        return self.is_job_info_eligible

    def main_csv_row(self) -> dict[str, str]:
        return self.job_info_record()


# Import compatibility only. New code must use the format-neutral names above.
MAIN_CSV_FIELDS = JOB_INFO_FIELDS
REPORT_CSV_FIELDS = PROCESS_REPORT_FIELDS
