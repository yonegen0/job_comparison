from __future__ import annotations

import html
import re
import unicodedata
from pathlib import Path

from .models import HEADER_FIELDS, JobEntry, ReportDocument, SECTION_FIELDS


class MarkdownFormatError(ValueError):
    pass


_JOB_HEADING_RE = re.compile(r"^#\s*(\d+)位[｜|](.*?)[｜|](.+?)\s*$")
_SECTION_ALIASES = {
    "業務概要": "業務概要",
    "求人概要": "業務概要",
    "おすすめポイント": "おすすめポイント",
    "メリット": "メリット",
    "デメリット": "デメリット",
    "デメリット・注意点": "デメリット",
    "総合判断": "総合判断",
}
_GROUP_ALIASES = {
    "第一優先グループ": "第一志望群",
    "第一志望群": "第一志望群",
    "第二優先グループ": "第二志望群",
    "第二志望群": "第二志望群",
    "条件付きグループ": "条件付き候補",
    "条件付き候補": "条件付き候補",
}


def clean_inline(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " / ", value, flags=re.IGNORECASE)
    value = re.sub(r"</?[^>]+>", "", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = unicodedata.normalize("NFC", value)
    return re.sub(r"[ \t]+", " ", value).strip()


def _split_pipe_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        raise MarkdownFormatError(f"Markdown表の行形式が不正です: {line}")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in raw[1:-1]:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append(clean_inline("".join(current)))
            current = []
        else:
            current.append(char)
    cells.append(clean_inline("".join(current)))
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _table_at(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    index = start
    while index < len(lines) and lines[index].strip().startswith("|"):
        cells = _split_pipe_row(lines[index])
        if rows and len(cells) != len(rows[0]):
            raise MarkdownFormatError(f"Markdown表の列数が一致しません（{index + 1}行目）")
        rows.append(cells)
        index += 1
    if len(rows) < 2 or not _is_separator_row(rows[1]):
        raise MarkdownFormatError(f"Markdown表の区切り行がありません（{start + 1}行目）")
    return [rows[0], *rows[2:]], index


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean_inline(value)).casefold()
    value = re.sub(r"株式会社|医療法人社団|相互会社|税理士法人", "", value)
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]", "", value)


def _parse_blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(clean_inline(" ".join(paragraph)))
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            flush()
            continue
        if stripped.startswith(("- ", "* ")):
            flush()
            value = clean_inline(stripped[2:])
            if value:
                blocks.append(f"• {value}")
        elif re.match(r"^\d+[.)]\s+", stripped):
            flush()
            blocks.append(clean_inline(stripped))
        else:
            paragraph.append(stripped)
    flush()
    output: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        key = _normal(block)
        if key and key not in seen:
            output.append(block)
            seen.add(key)
    return output


def parse_markdown(path: Path) -> ReportDocument:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = next((clean_inline(line[2:]) for line in lines if line.startswith("# ")), "")
    if not title:
        raise MarkdownFormatError("文書タイトルがありません。")

    comparison_rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if re.match(r"^#{1,2}\s*比較一覧\s*$", line.strip()):
            table_start = next((i for i in range(index + 1, len(lines)) if lines[i].strip().startswith("|")), -1)
            if table_start < 0:
                raise MarkdownFormatError("比較一覧の表がありません。")
            table, _ = _table_at(lines, table_start)
            headers = table[0]
            comparison_rows = [dict(zip(headers, row, strict=True)) for row in table[1:]]
            break
    if not comparison_rows:
        raise MarkdownFormatError("比較一覧を解析できません。")

    jobs: list[JobEntry] = []
    job_starts = [(i, _JOB_HEADING_RE.match(line.strip())) for i, line in enumerate(lines)]
    job_starts = [(i, match) for i, match in job_starts if match]
    for number, (start, match) in enumerate(job_starts):
        assert match is not None
        end = job_starts[number + 1][0] if number + 1 < len(job_starts) else len(lines)
        for probe in range(start + 1, end):
            if lines[probe].startswith("# ") and not _JOB_HEADING_RE.match(lines[probe].strip()):
                end = probe
                break
        rank = int(match.group(1))
        job = JobEntry(rank=rank, company=clean_inline(match.group(2)), position=clean_inline(match.group(3)))
        section_positions: list[tuple[int, str]] = []
        header_table_start: int | None = None
        for index in range(start + 1, end):
            stripped = lines[index].strip()
            if re.match(r"^##\s*ヘッダ\s*$", stripped):
                header_table_start = next(
                    (i for i in range(index + 1, end) if lines[i].strip().startswith("|")), None
                )
            heading_match = re.match(r"^###\s*(.+?)\s*$", stripped)
            if heading_match:
                heading = _SECTION_ALIASES.get(clean_inline(heading_match.group(1)))
                if heading:
                    section_positions.append((index, heading))
        if header_table_start is None:
            raise MarkdownFormatError(f"{rank}位にヘッダ表がありません。")
        table, _ = _table_at(lines, header_table_start)
        if table[0][:2] != ["項目", "内容"]:
            raise MarkdownFormatError(f"{rank}位のヘッダ表は「項目／内容」の2列である必要があります。")
        job.fields = {row[0]: row[1] for row in table[1:] if len(row) >= 2}
        for field_name in ("雇用形態", "評価", "年収", "年間休日", "勤務地", "応募条件", "難易度"):
            if not job.fields.get(field_name, "").strip():
                raise MarkdownFormatError(f"{rank}位の必須項目「{field_name}」がありません。")
        for section_index, (position, heading) in enumerate(section_positions):
            section_end = section_positions[section_index + 1][0] if section_index + 1 < len(section_positions) else end
            job.sections[heading] = _parse_blocks(lines[position + 1 : section_end])
        for section_name in SECTION_FIELDS:
            if not job.sections.get(section_name):
                raise MarkdownFormatError(f"{rank}位の必須セクション「{section_name}」がありません。")
        jobs.append(job)

    ranks = sorted(job.rank for job in jobs)
    if ranks != list(range(1, 11)):
        raise MarkdownFormatError(f"求人順位は1～10の連番である必要があります: {ranks}")
    if len(comparison_rows) != 10:
        raise MarkdownFormatError(f"比較一覧は10件である必要があります: {len(comparison_rows)}件")

    comparison_by_rank: dict[int, dict[str, str]] = {}
    for row in comparison_rows:
        raw_rank = row.get("順位", "")
        rank_match = re.search(r"\d+", raw_rank)
        if not rank_match:
            raise MarkdownFormatError(f"比較一覧の順位が不正です: {raw_rank}")
        rank = int(rank_match.group())
        if rank in comparison_by_rank:
            raise MarkdownFormatError(f"比較一覧の順位が重複しています: {rank}")
        comparison_by_rank[rank] = row
    for job in jobs:
        summary = comparison_by_rank.get(job.rank)
        if summary is None:
            raise MarkdownFormatError(f"比較一覧に{job.rank}位がありません。")
        summary_name = summary.get("会社・職種", "")
        company_key = _normal(job.company)
        if company_key and company_key[: max(4, len(company_key) // 2)] not in _normal(summary_name):
            raise MarkdownFormatError(f"比較一覧と詳細の会社が一致しません: {job.rank}位")

    priority_groups: dict[str, list[int]] = {name: [] for name in _GROUP_ALIASES.values()}
    group_heading_positions: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^#\s*(.+?)\s*$", line.strip())
        if match and clean_inline(match.group(1)) in _GROUP_ALIASES:
            group_heading_positions.append((index, _GROUP_ALIASES[clean_inline(match.group(1))]))
    for group_index, (start, group_name) in enumerate(group_heading_positions):
        end = group_heading_positions[group_index + 1][0] if group_index + 1 < len(group_heading_positions) else len(lines)
        ranks_found = sorted({int(value) for value in re.findall(r"(?:\*\*)?(\d+)位", "\n".join(lines[start + 1 : end]))})
        priority_groups[group_name] = ranks_found
    assigned = [rank for ranks_in_group in priority_groups.values() for rank in ranks_in_group]
    if sorted(assigned) != list(range(1, 11)) or len(assigned) != len(set(assigned)):
        raise MarkdownFormatError(f"優先グループの割当が不正です: {priority_groups}")
    for job in jobs:
        job.priority_group = next(name for name, group_ranks in priority_groups.items() if job.rank in group_ranks)

    return ReportDocument(
        title=title,
        jobs=sorted(jobs, key=lambda item: item.rank),
        comparison_rows=[comparison_by_rank[rank] for rank in range(1, 11)],
        priority_groups=priority_groups,
    )
