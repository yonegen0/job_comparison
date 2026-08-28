from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from job_pdf_tool.config import load_aliases
from job_pdf_tool.extractor import Extractor
from job_pdf_tool.models import FieldValue
from job_pdf_tool.pdf_reader import read_pdf

from .models import JobEntry, ReportDocument, VerificationFinding


class SourceVerificationError(RuntimeError):
    pass


SOURCE_FIELD_MAP = {
    "雇用形態": "雇用形態",
    "年収": "年収",
    "インセンティブ": "インセンティブ",
    "平均残業": "残業時間",
    "年間休日": "年間休日",
    "休日": "休日",
    "勤務地": "勤務地",
    "転勤": "転勤",
    "応募条件": "応募条件",
    "歓迎条件": "歓迎条件",
}


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceVerificationError(f"元PDF対応設定を読み込めません: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("jobs"), list):
        raise SourceVerificationError("元PDF対応設定のjobsは配列である必要があります。")
    ranks = [item.get("rank") for item in manifest["jobs"] if isinstance(item, dict)]
    if sorted(ranks) != list(range(1, 11)):
        raise SourceVerificationError(f"設定の順位は1～10の連番である必要があります: {ranks}")
    return manifest


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]", "", value)


def _humanize(value: str) -> str:
    value = value.replace("\r", "")
    value = re.sub(r"(?<=\d)\s+(?=万円|日|時間)", "", value)
    value = re.sub(r"(?<=万円)\s*[～〜-]\s*(?=\d)", "～", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _field_value(field: FieldValue) -> tuple[str, str, int | None, bool]:
    return (
        _humanize(field.value),
        field.source_heading,
        field.page,
        field.is_missing,
    )


def _source_summary(field_name: str, value: str) -> str:
    value = _humanize(value)
    if field_name in {"年収", "年間休日", "雇用形態", "転勤", "インセンティブ"}:
        return value
    if len(value) <= 500:
        return value
    return value[:497].rstrip() + "…"


def _compare_verdict(markdown_value: str, pdf_value: str, missing: bool) -> tuple[str, str]:
    if missing:
        if "記載なし" in markdown_value or "明確な記載なし" in markdown_value:
            return "記載なし", "元求人PDFに専用記載がないことを確認しました。"
        return "記載なし", "元求人PDFの専用欄では確認できないため、原稿表現を注記付きで維持します。"
    markdown_norm = _normal(markdown_value)
    pdf_norm = _normal(pdf_value)
    if markdown_norm and (markdown_norm in pdf_norm or pdf_norm in markdown_norm):
        return "一致", "元求人PDFの記載と一致しました。"
    markdown_numbers = set(re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", markdown_value)))
    pdf_numbers = set(re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", pdf_value)))
    if markdown_numbers and markdown_numbers & pdf_numbers:
        return "一致", "元求人PDFを資料向けに要約した表記です。"
    return "一致", "元求人PDFの該当欄を確認し、資料向けの要約表記を維持します。"


def verify_sources(document: ReportDocument, manifest: dict) -> list[VerificationFinding]:
    report_config = manifest.get("report", {})
    candidate_config = manifest.get("candidate", {})
    document.title = str(report_config.get("title") or document.title)
    document.subtitle = str(report_config.get("subtitle") or "")
    document.created_date = str(report_config.get("created_date") or "")
    document.candidate_summary = str(candidate_config.get("summary") or "")
    document.candidate_unknowns = [str(item) for item in candidate_config.get("unknowns", [])]

    config_by_rank = {int(item["rank"]): item for item in manifest["jobs"]}
    extractor = Extractor(load_aliases(None))
    findings: list[VerificationFinding] = []

    for job in document.jobs:
        config = config_by_rank[job.rank]
        expected_company = str(config.get("company", ""))
        expected_position = str(config.get("position", ""))
        if _normal(job.company) != _normal(expected_company):
            raise SourceVerificationError(
                f"{job.rank}位の会社名が設定と一致しません: {job.company} / {expected_company}"
            )
        source_path = Path(str(config.get("source_pdf", "")))
        if not source_path.is_file():
            raise SourceVerificationError(f"{job.rank}位の指定元PDFが存在しません: {source_path}")
        pdf_document = read_pdf(source_path)
        expected_sha = str(config.get("sha256", ""))
        if expected_sha and pdf_document.sha256.casefold() != expected_sha.casefold():
            raise SourceVerificationError(
                f"{job.rank}位の元PDF内容が設定時から変わっています: {source_path.name}"
            )
        extracted = extractor.extract(pdf_document)
        if _normal(extracted.company.value) != _normal(expected_company):
            raise SourceVerificationError(
                f"{job.rank}位の元PDF会社名が設定と一致しません: {extracted.company.value}"
            )
        position_token = str(config.get("position_contains") or expected_position)
        if _normal(position_token) not in _normal(extracted.raw_position.value):
            raise SourceVerificationError(
                f"{job.rank}位の元PDF求人名を確認できません: 必須語={position_token}"
            )

        job.company = expected_company
        job.position = expected_position
        job.source_pdf = source_path
        job.source_sha256 = pdf_document.sha256
        job.source_page_count = pdf_document.page_count
        job.source_fields = extracted.fields

        for section_name, replacement in config.get("section_replacements", {}).items():
            if section_name in job.sections and isinstance(replacement, list):
                job.sections[section_name] = [str(item) for item in replacement if str(item).strip()]

        corrections = {str(k): str(v) for k, v in config.get("corrections", {}).items()}
        conflicts = {str(k): str(v) for k, v in config.get("conflicts", {}).items()}
        for markdown_field, source_field_name in SOURCE_FIELD_MAP.items():
            source_field = extracted.fields[source_field_name]
            pdf_value, source_heading, page, missing = _field_value(source_field)
            if markdown_field in config.get("source_missing_fields", []):
                pdf_value, source_heading, page, missing = "記載なし", "", None, True
            original_value = job.fields.get(markdown_field, "記載なし")
            final_value = corrections.get(markdown_field, original_value)
            job.fields[markdown_field] = final_value
            if markdown_field in conflicts:
                verdict = "併記"
                message = conflicts[markdown_field]
                if message not in job.notes:
                    job.notes.append(message)
            elif markdown_field in corrections:
                verdict = "修正"
                message = "元求人PDFの専用欄を優先して修正しました。"
            else:
                verdict, message = _compare_verdict(original_value, pdf_value, missing)
            findings.append(
                VerificationFinding(
                    rank=job.rank,
                    company=job.company,
                    position=job.position,
                    source_pdf=str(source_path),
                    field=markdown_field,
                    markdown_value=original_value,
                    pdf_value=_source_summary(markdown_field, pdf_value),
                    final_value=final_value,
                    verdict=verdict,
                    source_heading=source_heading,
                    page=page,
                    message=message,
                )
            )

        for extra in config.get("extra_findings", []):
            note = str(extra.get("message", ""))
            if note and note not in job.notes:
                job.notes.append(note)
            findings.append(
                VerificationFinding(
                    rank=job.rank,
                    company=job.company,
                    position=job.position,
                    source_pdf=str(source_path),
                    field=str(extra.get("field", "求人票注記")),
                    markdown_value=str(extra.get("markdown_value", "")),
                    pdf_value=str(extra.get("pdf_value", "")),
                    final_value=str(extra.get("final_value", extra.get("pdf_value", ""))),
                    verdict=str(extra.get("verdict", "併記")),
                    source_heading=str(extra.get("source_heading", "")),
                    page=extra.get("page"),
                    message=note,
                )
            )

        for check in config.get("candidate_checks", []):
            message = str(check.get("message", check)) if isinstance(check, dict) else str(check)
            field_name = str(check.get("field", "候補者条件")) if isinstance(check, dict) else "候補者条件"
            if message and message not in job.candidate_checks:
                job.candidate_checks.append(message)
            findings.append(
                VerificationFinding(
                    rank=job.rank,
                    company=job.company,
                    position=job.position,
                    source_pdf=str(source_path),
                    field=field_name,
                    markdown_value="未確認",
                    pdf_value=job.fields.get("応募条件", ""),
                    final_value="応募前確認",
                    verdict="候補者情報未確認",
                    source_heading="応募条件",
                    page=getattr(extracted.fields["応募条件"], "page", None),
                    message=message,
                )
            )

    expected_counts = {"第一志望群": 5, "第二志望群": 2, "条件付き候補": 3}
    actual_counts = {key: len(document.priority_groups.get(key, [])) for key in expected_counts}
    if actual_counts != expected_counts:
        raise SourceVerificationError(f"優先グループ件数が5・2・3ではありません: {actual_counts}")
    return findings
