from __future__ import annotations

import csv
from pathlib import Path

from .models import ReportDocument, VerificationFinding


VERIFICATION_FIELDS = [
    "順位",
    "会社名",
    "募集ポジション",
    "元PDF",
    "対象項目",
    "Markdown記載値",
    "PDF確認値",
    "最終採用値",
    "判定",
    "採用元見出し",
    "ページ",
    "メッセージ",
]


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _block_lines(blocks: list[str]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        if block.startswith("• "):
            lines.append(f"- {block[2:]}")
        else:
            if lines:
                lines.append("")
            lines.append(block)
    return lines


def build_verified_markdown(document: ReportDocument) -> str:
    lines = [
        f"# {document.title}",
        "",
        f"{document.subtitle}  ",
        f"作成日：{document.created_date}",
        "",
        "---",
        "",
        "## 比較一覧",
        "",
        "| 順位 | 会社・職種 | 雇用形態 | 評価 | 年収 | 平均残業 | 年間休日 | 主な勤務地 | 難易度 |",
        "|---:|---|---|---|---|---|---:|---|---|",
    ]
    for job in document.jobs:
        lines.append(
            "| {rank} | {name} | {employment} | {grade} | {income} | {overtime} | {holidays} | {location} | {difficulty} |".format(
                rank=job.rank,
                name=_cell(f"{job.company}／{job.position}"),
                employment=_cell(job.fields.get("雇用形態", "記載なし")),
                grade=_cell(job.fields.get("評価", "記載なし")),
                income=_cell(job.fields.get("年収", "記載なし")),
                overtime=_cell(job.fields.get("平均残業", "記載なし")),
                holidays=_cell(job.fields.get("年間休日", "記載なし")),
                location=_cell(job.fields.get("勤務地", "記載なし")),
                difficulty=_cell(job.fields.get("難易度", "記載なし")),
            )
        )
    for job in document.jobs:
        lines.extend(["", "---", "", f"# {job.rank}位｜{job.company}｜{job.position}", "", "## ヘッダ", ""])
        lines.extend(["| 項目 | 内容 |", "|---|---|"])
        for field_name, value in job.fields.items():
            lines.append(f"| {field_name} | {_cell(value)} |")
        for section_name in ("業務概要", "おすすめポイント", "メリット", "デメリット", "総合判断"):
            lines.extend(["", f"### {section_name}", ""])
            lines.extend(_block_lines(job.sections[section_name]))
        if job.notes or job.candidate_checks:
            lines.extend(["", "### 元求人票との確認メモ", ""])
            lines.extend(f"- {note}" for note in job.notes)
            lines.extend(f"- 応募前確認：{check}" for check in job.candidate_checks)

    group_labels = [
        ("第一志望群", "第一優先グループ"),
        ("第二志望群", "第二優先グループ"),
        ("条件付き候補", "条件付きグループ"),
    ]
    for group_name, heading in group_labels:
        lines.extend(["", "---", "", f"# {heading}", ""])
        for rank in document.priority_groups[group_name]:
            job = document.jobs[rank - 1]
            judgment = job.sections["総合判断"][0]
            lines.append(f"- **{rank}位 {job.company}／{job.position}** — {judgment}")
    lines.extend(
        [
            "",
            "## 応募前に確認する情報",
            "",
            *[f"- {item}" for item in document.candidate_unknowns],
            "",
            "## 応募順の考え方",
            "",
            "第一志望群から応募を進め、応募条件の確認結果に応じて第二志望群・条件付き候補へ広げます。未確認条件は満たしていると推測せず、応募前に企業またはエージェントへ確認します。",
            "",
        ]
    )
    return "\n".join(lines)


def write_verification_csv(path: Path, findings: list[VerificationFinding]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=VERIFICATION_FIELDS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(item.as_row() for item in findings)
