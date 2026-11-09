from __future__ import annotations

import csv
import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DocumentResult, JOB_INFO_FIELDS, PROCESS_REPORT_FIELDS, ReportItem, MISSING_VALUE
from .providers.circus import CIRCUS_FIELDS


JOB_INFO_FORMAT = "job-comparison/job-info"
PROCESS_REPORT_FORMAT = "job-comparison/processing-report"
FORMAT_VERSION = 1


@dataclass(slots=True)
class JobInfoDocument:
    field_order: list[str]
    jobs: list[dict[str, str]]
    stale_jobs: list[dict[str, str]]


def _temporary_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def _filename_key(value: str) -> str:
    return (
        unicodedata.normalize("NFKC", Path(value.strip()).name).casefold()
        if value.strip()
        else ""
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONオブジェクトに重複キーがあります: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JSONがUTF-8ではありません: {path}") from exc
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"JSONに使用できない数値があります: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"JSON形式が不正です: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSONの最上位はオブジェクトである必要があります: {path}")
    return data


def _validate_envelope(
    data: dict[str, Any],
    *,
    path: Path,
    expected_format: str,
    collection_key: str,
    standard_fields: list[str],
    allow_extra_fields: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    expected_top = {"format", "format_version", "field_order", collection_key}
    actual_top = set(data)
    if actual_top != expected_top:
        missing = sorted(expected_top - actual_top)
        extra = sorted(actual_top - expected_top)
        detail = []
        if missing:
            detail.append(f"不足: {', '.join(missing)}")
        if extra:
            detail.append(f"未対応: {', '.join(extra)}")
        raise ValueError(f"JSONの最上位キーが不正です: {path}: {' / '.join(detail)}")
    if data["format"] != expected_format:
        raise ValueError(f"JSONのformatが不正です: {path}")
    if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
        raise ValueError(f"未対応のJSON format_versionです: {path}")
    field_order = data["field_order"]
    if not isinstance(field_order, list) or not all(isinstance(item, str) for item in field_order):
        raise ValueError(f"JSONのfield_orderが不正です: {path}")
    if any(not item for item in field_order) or len(field_order) != len(set(field_order)):
        raise ValueError(f"JSONのfield_orderに空または重複項目があります: {path}")
    if allow_extra_fields:
        if field_order[: len(standard_fields)] != standard_fields:
            raise ValueError(f"JSONの標準項目または順序が不正です: {path}")
    elif field_order != standard_fields:
        raise ValueError(f"JSONの項目または順序が不正です: {path}")
    records = data[collection_key]
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"JSONの{collection_key}はオブジェクト配列である必要があります: {path}")
    return list(field_order), records


def load_job_info_json(path: Path) -> JobInfoDocument:
    data = _load_json(path)
    field_order, raw_jobs = _validate_envelope(
        data,
        path=path,
        expected_format=JOB_INFO_FORMAT,
        collection_key="jobs",
        standard_fields=JOB_INFO_FIELDS,
        allow_extra_fields=True,
    )
    expected_keys = set(field_order)
    jobs: list[dict[str, str]] = []
    for index, raw in enumerate(raw_jobs, 1):
        unknown = set(raw) - expected_keys
        missing_standard = set(JOB_INFO_FIELDS) - set(raw)
        if unknown or missing_standard:
            raise ValueError(
                f"求人情報JSONのjob {index}の項目が不正です: "
                f"不足={', '.join(sorted(missing_standard)) or 'なし'} / "
                f"未登録={', '.join(sorted(unknown)) or 'なし'}"
            )
        normalized: dict[str, str] = {}
        for field in field_order:
            value = raw.get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"求人情報JSONのjob {index}の値は文字列である必要があります: {field}")
            normalized[field] = value
        jobs.append(normalized)
    stale = [job for job in jobs if job.get("処理状態") == "PDF未検出"]
    return JobInfoDocument(field_order=field_order, jobs=jobs, stale_jobs=stale)


def load_processing_report_json(path: Path) -> list[dict[str, str | int | None]]:
    data = _load_json(path)
    field_order, raw_reports = _validate_envelope(
        data,
        path=path,
        expected_format=PROCESS_REPORT_FORMAT,
        collection_key="reports",
        standard_fields=PROCESS_REPORT_FIELDS,
        allow_extra_fields=False,
    )
    expected_keys = set(field_order)
    reports: list[dict[str, str | int | None]] = []
    for index, raw in enumerate(raw_reports, 1):
        if set(raw) != expected_keys:
            raise ValueError(f"処理レポートJSONのreport {index}の項目が不正です")
        normalized: dict[str, str | int | None] = {}
        for field in field_order:
            value = raw[field]
            if field == "ページ":
                if value is not None and (type(value) is not int or value <= 0):
                    raise ValueError(f"処理レポートJSONのreport {index}のページが不正です")
            elif not isinstance(value, str):
                raise ValueError(
                    f"処理レポートJSONのreport {index}の値は文字列である必要があります: {field}"
                )
            normalized[field] = value
        reports.append(normalized)
    return reports


def _read_legacy_main_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise ValueError(f"移行元の求人情報CSVが存在しません: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            fieldnames = next(reader)
        except StopIteration as exc:
            raise ValueError(f"移行元の求人情報CSVが空です: {path}") from exc
    if not fieldnames or any(not name for name in fieldnames):
        raise ValueError(f"移行元の求人情報CSVに空のヘッダーがあります: {path}")
    duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
    if duplicates:
        raise ValueError(f"移行元の求人情報CSVに重複ヘッダーがあります: {', '.join(duplicates)}")
    missing = [name for name in JOB_INFO_FIELDS if name not in fieldnames]
    if missing:
        raise ValueError(f"移行元の求人情報CSVに必須ヘッダーがありません: {', '.join(missing)}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return list(fieldnames), [{key: value or "" for key, value in row.items()} for row in rows]


def _existing_job_info(
    target: Path,
    import_main_csv: Path | None,
) -> tuple[list[str], list[dict[str, str]]]:
    if target.exists():
        if import_main_csv is not None:
            raise ValueError("求人情報JSONが既に存在するため、旧CSVを移行できません。")
        document = load_job_info_json(target)
        return document.field_order, document.jobs
    if import_main_csv is not None:
        return _read_legacy_main_csv(import_main_csv)
    return list(JOB_INFO_FIELDS), []


def prepare_job_info_document(
    results: list[DocumentResult],
    target: Path,
    import_main_csv: Path | None = None,
    provider: str = "jobins",
) -> JobInfoDocument:
    existing_fields, existing_jobs = _existing_job_info(target, import_main_csv)
    extras = [name for name in existing_fields if name not in JOB_INFO_FIELDS]
    if provider == "circus":
        extras.extend(name for name in CIRCUS_FIELDS if name not in extras)
    field_order = [*JOB_INFO_FIELDS, *extras]
    current_by_name: dict[str, int] = {}
    for index, result in enumerate(results):
        key = _filename_key(result.source_path.name)
        if key in current_by_name:
            raise ValueError(f"大文字小文字を無視すると同名になるPDFがあります: {result.source_path.name}")
        current_by_name[key] = index

    matches_by_existing: dict[int, int] = {}
    matches_by_result: dict[int, int] = {}
    protected: set[int] = set()
    for existing_index, previous in enumerate(existing_jobs):
        previous_provider = previous.get("媒体", "").strip()
        previous_id = previous.get("求人ID", "").strip()
        if previous_id == MISSING_VALUE:
            previous_id = ""
        candidates: set[int] = set()
        for key in (
            _filename_key(previous.get("元ファイル名", "")),
            _filename_key(previous.get("新ファイル名", "")),
        ):
            if key and key in current_by_name:
                candidates.add(current_by_name[key])
        if provider == "circus" and previous_provider == "circus" and previous_id:
            candidates.update(
                i for i, result in enumerate(results)
                if result.provider == "circus" and result.job_id == previous_id
            )
        if provider == "circus" and previous_provider not in {"", "circus"}:
            candidates.clear()
        if provider == "jobins" and previous_provider == "circus":
            candidates.clear()
        if len(candidates) != 1:
            if candidates:
                protected.add(existing_index)
                for i in candidates:
                    results[i].error = "既存JSONとの照合が一意ではないため更新・登録を保留しました。"
                    results[i].add_report("求人照合", "warning", results[i].error)
            continue
        i = next(iter(candidates))
        current = results[i]
        if provider == "circus":
            if current.provider != "circus":
                continue
            if previous_provider == "":
                # Legacy data is only safe to associate by an exact file name plus company.
                previous_names = {
                    _filename_key(previous.get("元ファイル名", "")),
                    _filename_key(previous.get("新ファイル名", "")),
                }
                if (_filename_key(current.source_path.name) not in previous_names
                        or previous.get("会社名") != current.company.value):
                    protected.add(existing_index)
                    current.error = "媒体不明の既存求人と照合できないため、更新・登録を保留しました。"
                    current.add_report("求人照合", "warning", current.error)
                    continue
            if (previous.get("会社名") and current.company.value != previous["会社名"]
                    and current.is_job_info_eligible):
                protected.add(existing_index)
                current.error = "同一求人IDで企業名が異なるため、更新・登録を保留しました。"
                current.add_report("会社名", "warning", current.error)
                continue
        if i in matches_by_result:
            protected.add(existing_index)
            protected.add(matches_by_result[i])
            current.error = "複数の既存求人が同じPDFを指すため更新・登録を保留しました。"
            current.add_report("求人照合", "warning", current.error)
            continue
        matches_by_existing[existing_index] = i
        matches_by_result[i] = existing_index

    jobs: list[dict[str, str]] = []
    stale_jobs: list[dict[str, str]] = []
    for i, result in enumerate(results):
        if not result.is_job_info_eligible:
            continue
        previous = existing_jobs[matches_by_result[i]] if i in matches_by_result else {}
        record = result.job_info_record()
        for field in extras:
            incoming = result.extra_fields.get(field, "")
            old = previous.get(field, "")
            if provider == "circus" and result.provider == "circus" and incoming not in {"", MISSING_VALUE}:
                record[field] = incoming
            else:
                record[field] = old or (incoming if incoming else "")
        jobs.append({name: record.get(name, "") for name in field_order})

    for index, previous in enumerate(existing_jobs):
        matched_result = results[matches_by_existing[index]] if index in matches_by_existing else None
        if matched_result is not None and matched_result.is_job_info_eligible:
            continue
        preserved = {field: previous.get(field, "") for field in field_order}
        should_mark_missing = (
            matched_result is None
            and index not in protected
            and (provider == "jobins" and previous.get("媒体", "") != "circus"
                 or provider == "circus" and previous.get("媒体", "") == "circus")
        )
        if should_mark_missing:
            preserved["処理状態"] = "PDF未検出"
            stale_jobs.append(preserved)
        elif matched_result is not None:
            matched_result.add_report("求人照合", "warning", "解析失敗等のため既存JSONレコードを変更せず保持しました。")
        jobs.append(preserved)

    return JobInfoDocument(field_order=field_order, jobs=jobs, stale_jobs=stale_jobs)


def build_processing_reports(
    results: list[DocumentResult],
    stale_jobs: list[dict[str, str]] | None = None,
) -> list[dict[str, str | int | None]]:
    rows: list[dict[str, str | int | None]] = []
    for result in results:
        reports = result.reports or [
            ReportItem(
                target_field="ファイル",
                classification="direct",
                message="処理対象として正常に解析しました。",
            )
        ]
        for report in reports:
            rows.append(
                {
                    "元ファイル名": result.source_path.name,
                    "新ファイル名": result.proposed_name or "記載なし",
                    "SHA-256": result.sha256,
                    "処理状態": result.status,
                    "対象項目": report.target_field,
                    "判定": report.classification,
                    "採用元見出し": report.source_heading,
                    "ページ": report.page,
                    "メッセージ": report.message,
                }
            )
    for stale in stale_jobs or []:
        rows.append(
            {
                "元ファイル名": stale.get("元ファイル名", ""),
                "新ファイル名": stale.get("新ファイル名", ""),
                "SHA-256": "",
                "処理状態": "PDF未検出",
                "対象項目": "ファイル",
                "判定": "warning",
                "採用元見出し": "",
                "ページ": None,
                "メッセージ": "求人情報jobに対応するPDFが入力フォルダーに見つかりません。",
            }
        )
    return rows


def _serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def serialize_job_info(document: JobInfoDocument) -> str:
    return _serialize(
        {
            "format": JOB_INFO_FORMAT,
            "format_version": FORMAT_VERSION,
            "field_order": document.field_order,
            "jobs": document.jobs,
        }
    )


def serialize_processing_report(
    reports: list[dict[str, str | int | None]],
) -> str:
    return _serialize(
        {
            "format": PROCESS_REPORT_FORMAT,
            "format_version": FORMAT_VERSION,
            "field_order": PROCESS_REPORT_FIELDS,
            "reports": reports,
        }
    )


def stage_job_info_json(document: JobInfoDocument, target: Path) -> Path:
    temporary = _temporary_path(target)
    try:
        temporary.write_text(serialize_job_info(document), encoding="utf-8", newline="")
        load_job_info_json(temporary)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def stage_processing_report_json(
    results: list[DocumentResult],
    target: Path,
    stale_jobs: list[dict[str, str]] | None = None,
) -> Path:
    temporary = _temporary_path(target)
    try:
        reports = build_processing_reports(results, stale_jobs)
        temporary.write_text(serialize_processing_report(reports), encoding="utf-8", newline="")
        load_processing_report_json(temporary)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "FORMAT_VERSION",
    "JOB_INFO_FORMAT",
    "PROCESS_REPORT_FORMAT",
    "JobInfoDocument",
    "build_processing_reports",
    "load_job_info_json",
    "load_processing_report_json",
    "prepare_job_info_document",
    "serialize_job_info",
    "serialize_processing_report",
    "stage_job_info_json",
    "stage_processing_report_json",
]
