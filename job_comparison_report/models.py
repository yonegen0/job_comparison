from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


HEADER_FIELDS = [
    "雇用形態",
    "評価",
    "年収",
    "インセンティブ",
    "平均残業",
    "年間休日",
    "休日",
    "勤務地",
    "転勤",
    "応募条件",
    "歓迎条件",
    "難易度",
]

SECTION_FIELDS = ["業務概要", "おすすめポイント", "メリット", "デメリット", "総合判断"]


@dataclass(slots=True)
class JobEntry:
    rank: int
    company: str
    position: str
    fields: dict[str, str] = field(default_factory=dict)
    sections: dict[str, list[str]] = field(default_factory=dict)
    priority_group: str = ""
    source_pdf: Path | None = None
    source_sha256: str = ""
    source_page_count: int = 0
    source_fields: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    candidate_checks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReportDocument:
    title: str
    jobs: list[JobEntry]
    comparison_rows: list[dict[str, str]]
    priority_groups: dict[str, list[int]]
    subtitle: str = ""
    created_date: str = ""
    candidate_summary: str = ""
    candidate_unknowns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerificationFinding:
    rank: int
    company: str
    position: str
    source_pdf: str
    field: str
    markdown_value: str
    pdf_value: str
    final_value: str
    verdict: str
    source_heading: str = ""
    page: int | None = None
    message: str = ""

    def as_row(self) -> dict[str, str | int]:
        return {
            "順位": self.rank,
            "会社名": self.company,
            "募集ポジション": self.position,
            "元PDF": self.source_pdf,
            "対象項目": self.field,
            "Markdown記載値": self.markdown_value,
            "PDF確認値": self.pdf_value,
            "最終採用値": self.final_value,
            "判定": self.verdict,
            "採用元見出し": self.source_heading,
            "ページ": self.page or "",
            "メッセージ": self.message,
        }


@dataclass(slots=True)
class BuildResult:
    output_pdf: Path | None = None
    verified_markdown: Path | None = None
    verification_csv: Path | None = None
    page_count: int = 0
    warning_count: int = 0
    findings: list[VerificationFinding] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    applied: bool = False
    level: str = "success"
