from __future__ import annotations

import csv
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .models import DocumentResult, MAIN_CSV_FIELDS, REPORT_CSV_FIELDS, ReportItem


@dataclass(slots=True)
class MainCsvTable:
    fieldnames: list[str]
    rows: list[dict[str, str]]
    stale_rows: list[dict[str, str]]


def _temporary_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    return Path(raw_path)


def _filename_key(value: str) -> str:
    return unicodedata.normalize("NFKC", Path(value.strip()).name).casefold() if value.strip() else ""


def _read_existing_main_csv(target: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not target.exists():
        return list(MAIN_CSV_FIELDS), []
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            fieldnames = next(reader)
        except StopIteration as exc:
            raise ValueError(f"既存の求人情報CSVが空です: {target}") from exc
    if not fieldnames or any(not name for name in fieldnames):
        raise ValueError(f"既存の求人情報CSVに空のヘッダーがあります: {target}")
    duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
    if duplicates:
        raise ValueError(f"既存の求人情報CSVに重複ヘッダーがあります: {', '.join(duplicates)}")
    missing = [name for name in MAIN_CSV_FIELDS if name not in fieldnames]
    if missing:
        raise ValueError(f"既存の求人情報CSVに必須ヘッダーがありません: {', '.join(missing)}")
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return fieldnames, rows


def prepare_main_csv_table(results: list[DocumentResult], target: Path) -> MainCsvTable:
    existing_fieldnames, existing_rows = _read_existing_main_csv(target)
    extra_fields = [name for name in existing_fieldnames if name not in MAIN_CSV_FIELDS]
    fieldnames = [*MAIN_CSV_FIELDS, *extra_fields]

    current_by_name: dict[str, int] = {}
    for index, result in enumerate(results):
        key = _filename_key(result.source_path.name)
        if key in current_by_name:
            raise ValueError(f"大文字小文字を無視すると同名になるPDFがあります: {result.source_path.name}")
        current_by_name[key] = index

    matched_existing: dict[int, dict[str, str]] = {}
    stale_rows: list[dict[str, str]] = []
    for existing in existing_rows:
        matches = {
            current_by_name[key]
            for key in (
                _filename_key(existing.get("元ファイル名", "")),
                _filename_key(existing.get("新ファイル名", "")),
            )
            if key and key in current_by_name
        }
        if len(matches) > 1:
            raise ValueError(
                "既存CSVの元ファイル名と新ファイル名が別々のPDFを指しています: "
                f"{existing.get('元ファイル名', '')} / {existing.get('新ファイル名', '')}"
            )
        if matches:
            index = next(iter(matches))
            if index in matched_existing:
                raise ValueError(f"既存CSVの複数行が同じPDFを指しています: {results[index].source_path.name}")
            matched_existing[index] = existing
            continue

        stale = {name: existing.get(name, "") for name in fieldnames}
        stale["処理状態"] = "PDF未検出"
        stale_rows.append(stale)

    rows: list[dict[str, str]] = []
    for index, result in enumerate(results):
        if not result.is_main_csv_eligible:
            continue
        row = result.main_csv_row()
        previous = matched_existing.get(index, {})
        for name in extra_fields:
            row[name] = previous.get(name, "")
        rows.append({name: row.get(name, "") for name in fieldnames})
    rows.extend(stale_rows)
    return MainCsvTable(fieldnames=fieldnames, rows=rows, stale_rows=stale_rows)


def stage_main_csv(table: MainCsvTable, target: Path) -> Path:
    temporary = _temporary_path(target)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=table.fieldnames, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(table.rows)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def stage_report_csv(
    results: list[DocumentResult],
    target: Path,
    stale_rows: list[dict[str, str]] | None = None,
) -> Path:
    temporary = _temporary_path(target)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_CSV_FIELDS, lineterminator="\r\n")
            writer.writeheader()
            for result in results:
                reports = result.reports or [
                    ReportItem(
                        target_field="ファイル",
                        classification="direct",
                        message="処理対象として正常に解析しました。",
                    )
                ]
                for report in reports:
                    writer.writerow(
                        {
                            "元ファイル名": result.source_path.name,
                            "新ファイル名": result.proposed_name or "記載なし",
                            "SHA-256": result.sha256,
                            "処理状態": result.status,
                            "対象項目": report.target_field,
                            "判定": report.classification,
                            "採用元見出し": report.source_heading,
                            "ページ": report.page or "",
                            "メッセージ": report.message,
                        }
                    )
            for stale in stale_rows or []:
                writer.writerow(
                    {
                        "元ファイル名": stale.get("元ファイル名", ""),
                        "新ファイル名": stale.get("新ファイル名", ""),
                        "SHA-256": "",
                        "処理状態": "PDF未検出",
                        "対象項目": "ファイル",
                        "判定": "warning",
                        "採用元見出し": "",
                        "ページ": "",
                        "メッセージ": "CSV行に対応するPDFが入力フォルダに見つかりません。",
                    }
                )
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
