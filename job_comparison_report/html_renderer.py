from __future__ import annotations

import html
import re
from importlib.resources import files
from pathlib import Path

from .models import JobEntry, ReportDocument


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _grade_class(value: str) -> str:
    value = value.upper()
    if value.startswith("A"):
        return "grade-a"
    if value.startswith("B"):
        return "grade-b"
    return "grade-c"


def _short(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _blocks(values: list[str]) -> str:
    parts: list[str] = []
    bullets: list[str] = []

    def flush_bullets() -> None:
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{_h(item)}</li>" for item in bullets) + "</ul>")
            bullets.clear()

    for value in values:
        if value.startswith("• "):
            bullets.append(value[2:])
        elif re.match(r"^\d+[.)]\s+", value):
            flush_bullets()
            parts.append(f'<p class="numbered">{_h(value)}</p>')
        else:
            flush_bullets()
            parts.append(f"<p>{_h(value)}</p>")
    flush_bullets()
    return "".join(parts)


def _footer(page_number: int) -> str:
    return (
        '<footer class="footer"><span>求人比較資料｜オフィスワーカー</span>'
        f'<span class="page-number">{page_number:02d} / 12</span></footer>'
    )


def _cover_page(document: ReportDocument) -> str:
    rows: list[str] = []
    for job in document.jobs:
        rows.append(
            "<tr>"
            f'<td class="rank-cell">{job.rank}</td>'
            f'<td><strong>{_h(job.company)}</strong><small>{_h(job.position)}</small></td>'
            f'<td><span class="grade-pill {_grade_class(job.fields.get("評価", ""))}">{_h(job.fields.get("評価", ""))}</span></td>'
            f'<td>{_h(job.fields.get("年収", ""))}</td>'
            f'<td>{_h(job.fields.get("平均残業", ""))}</td>'
            f'<td>{_h(job.fields.get("年間休日", ""))}</td>'
            f'<td>{_h(_short(job.fields.get("勤務地", ""), 28))}</td>'
            "</tr>"
        )
    return f"""
    <section class="page cover-page" data-page="1">
      <main class="page-main">
        <div class="eyebrow">CAREER OPPORTUNITY REPORT</div>
        <div class="cover-heading">
          <div>
            <h1>{_h(document.title)}</h1>
            <p>{_h(document.subtitle)}</p>
          </div>
          <div class="job-count"><strong>10</strong><span>求人を比較</span></div>
        </div>
        <div class="cover-meta"><span>作成日 {_h(document.created_date)}</span><span>匿名資料</span><span>愛知県・WLB重視</span></div>
        <h2 class="section-title"><span>01</span> 比較一覧</h2>
        <table class="comparison-table">
          <thead><tr><th>順位</th><th>会社・募集ポジション</th><th>評価</th><th>年収</th><th>残業</th><th>年間休日</th><th>勤務地</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        <aside class="notice-box">
          <strong>応募条件に関する注意</strong>
          <p>最終学歴、雇用形態を含む職歴、運転免許、PC・Officeスキル、プログラミング学習歴、保護者対応経験の評価は未確認です。満たしていると推測せず、各求人の「応募前確認」を確認してください。</p>
        </aside>
      </main>
      {_footer(1)}
    </section>"""


def _metric(label: str, value: str) -> str:
    return f'<div class="metric"><span>{_h(label)}</span><strong>{_h(value)}</strong></div>'


def _detail_page(job: JobEntry, page_number: int) -> str:
    badges = [f'<span class="badge group">{_h(job.priority_group)}</span>']
    if job.candidate_checks:
        badges.append('<span class="badge warning">応募前確認</span>')
    if job.notes:
        badges.append('<span class="badge note">複数記載あり</span>')
    metrics = "".join(
        [
            _metric("雇用形態", job.fields.get("雇用形態", "記載なし")),
            _metric("年収", job.fields.get("年収", "記載なし")),
            _metric("平均残業", job.fields.get("平均残業", "記載なし")),
            _metric("年間休日", job.fields.get("年間休日", "記載なし")),
            _metric("勤務地", job.fields.get("勤務地", "記載なし")),
            _metric("転勤", job.fields.get("転勤", "記載なし")),
        ]
    )
    cards: list[str] = []
    for number, section_name in enumerate(("業務概要", "おすすめポイント", "メリット", "デメリット", "総合判断"), 1):
        cards.append(
            f'<section class="content-card card-{number}"><h3><span>{number:02d}</span>{_h(section_name)}</h3>'
            f'{_blocks(job.sections[section_name])}</section>'
        )
    checks = ""
    if job.candidate_checks or job.notes:
        rows = "".join(f"<li>{_h(item)}</li>" for item in [*job.candidate_checks, *job.notes])
        checks = f'<section class="check-card"><h3>応募条件・求人票の確認事項</h3><ul>{rows}</ul></section>'
    grade = job.fields.get("評価", "")
    return f"""
    <section class="page detail-page" data-page="{page_number}" data-rank="{job.rank}">
      <main class="page-main">
        <header class="job-header">
          <div class="rank-badge"><small>RANK</small><strong>{job.rank:02d}</strong></div>
          <div class="job-title"><div class="eyebrow">OFFICE WORKER OPPORTUNITY</div><h1>{_h(job.company)}</h1><h2>{_h(job.position)}</h2><div class="badges">{''.join(badges)}</div></div>
          <div class="score {_grade_class(grade)}"><small>評価</small><strong>{_h(grade)}</strong><span>{_h(job.fields.get('難易度', ''))}</span></div>
        </header>
        <div class="metrics">{metrics}</div>
        <div class="content-grid">{''.join(cards)}</div>
        {checks}
        <div class="layout-error">LAYOUT_ERROR：{job.rank}位の文章がページ内に収まりません</div>
      </main>
      {_footer(page_number)}
    </section>"""


def _priority_page(document: ReportDocument) -> str:
    group_descriptions = {
        "第一志望群": "条件バランスと転職目的への適合を優先",
        "第二志望群": "勤務地・転勤条件を確認して検討",
        "条件付き候補": "応募資格または求人票の整合確認後に判断",
    }
    groups: list[str] = []
    for group_number, group_name in enumerate(("第一志望群", "第二志望群", "条件付き候補"), 1):
        cards: list[str] = []
        for rank in document.priority_groups[group_name]:
            job = document.jobs[rank - 1]
            reason = _short(job.sections["総合判断"][0], 58)
            cards.append(
                f'<div class="priority-job"><span>{rank:02d}</span><div><strong>{_h(job.company)}</strong>'
                f'<small>{_h(job.position)}</small><p>{_h(reason)}</p></div></div>'
            )
        groups.append(
            f'<section class="priority-group priority-{group_number}"><header><div><small>GROUP {group_number:02d}</small>'
            f'<h2>{_h(group_name)}</h2><p>{_h(group_descriptions[group_name])}</p></div>'
            f'<strong>{len(cards)}</strong></header><div class="priority-jobs">{"".join(cards)}</div></section>'
        )
    unknowns = "".join(f"<li>{_h(item)}</li>" for item in document.candidate_unknowns)
    return f"""
    <section class="page priority-page" data-page="12">
      <main class="page-main">
        <div class="eyebrow">PRIORITY MAP</div>
        <div class="priority-title"><div><h1>応募優先順位</h1><p>確定情報と未確認条件を分け、安全に応募順を組み立てる</p></div><div class="priority-counts"><span>5</span><span>2</span><span>3</span></div></div>
        <div class="priority-layout">{''.join(groups)}</div>
        <aside class="final-check"><div><h3>応募前の共通確認</h3><ul>{unknowns}</ul></div><div><h3>応募開始時の考え方</h3><p>第一志望群から応募を進め、条件確認の結果に応じて第二志望群・条件付き候補へ広げます。未確認項目は企業またはエージェントに確認し、充足を推測しません。</p></div></aside>
        <div class="layout-error">LAYOUT_ERROR：優先順位ページが収まりません</div>
      </main>
      {_footer(12)}
    </section>"""


def render_html(document: ReportDocument, template_dir: Path | None = None) -> str:
    if template_dir is None:
        resources = files("job_comparison_report").joinpath("templates")
        template = resources.joinpath("report.html").read_text(encoding="utf-8")
        css = resources.joinpath("report.css").read_text(encoding="utf-8")
    else:
        template = (template_dir / "report.html").read_text(encoding="utf-8")
        css = (template_dir / "report.css").read_text(encoding="utf-8")
    pages = [_cover_page(document)]
    pages.extend(_detail_page(job, job.rank + 1) for job in document.jobs)
    pages.append(_priority_page(document))
    return (
        template.replace("{{TITLE}}", _h(document.title))
        .replace("{{STYLE}}", css)
        .replace("{{PAGES}}", "\n".join(pages))
    )
