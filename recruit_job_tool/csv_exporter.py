"""媒体別・社内専用CSVの安全な完全置換出力。"""

from __future__ import annotations

from collections.abc import Iterable
import csv
import io
import os
from pathlib import Path
import tempfile

from .errors import CsvExportError
from .models import Job, JobSource, format_rfc3339
from .repository import _coerce_source
from .validator import Validator


CSV_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "internal_job_id",
    "source",
    "source_job_id",
    "source_url",
    "batch_id",
    "job_status",
    "job_title",
    "company_name",
    "industry",
    "occupation",
    "employment_type",
    "location",
    "salary_text",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_parse_status",
    "job_description",
    "required_conditions",
    "preferred_conditions",
    "required_skills",
    "preferred_skills",
    "experience",
    "qualification",
    "education",
    "age_requirement",
    "working_hours",
    "holidays",
    "benefits",
    "selection_process",
    "recruitment_reason",
    "number_of_positions",
    "commission",
    "success_fee_conditions",
    "referral_reward_your_company",
    "referral_reward_total",
    "refund_policy",
    "remarks",
    "acquired_at",
    "last_verified_at",
    "extractor_version",
)

_NUMERIC_COLUMNS = frozenset({"schema_version", "salary_min", "salary_max"})
_TEXT_COLUMNS = frozenset(CSV_COLUMNS) - _NUMERIC_COLUMNS


def serialize_jobs_csv(
    provider: JobSource | str, jobs: Iterable[Job], *, validator: Validator | None = None,
) -> bytes:
    """求人を検証し、ファイルを変更せず正式CSVのバイト列へ変換する。"""
    source = _coerce_source(provider)
    materialized = list(jobs)
    if not materialized:
        raise CsvExportError("0件の求人はCSV出力できません")
    validator = validator or Validator()
    seen_ids: set[str] = set()
    for job in materialized:
        result = validator.validate(job)
        if not result.is_valid:
            raise CsvExportError(f"不正な求人が含まれています: {','.join(result.errors)}")
        if job.source is not source:
            raise CsvExportError("出力先媒体と求人の媒体が一致しません")
        if job.internal_job_id in seen_ids:
            raise CsvExportError("重複する求人IDが含まれています")
        seen_ids.add(job.internal_job_id)
    try:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, extrasaction="raise", lineterminator="\r\n")
        writer.writeheader()
        for job in materialized:
            writer.writerow(_job_to_csv_row(job))
        return stream.getvalue().encode("utf-8-sig")
    except (UnicodeError, csv.Error, TypeError, ValueError) as exc:
        raise CsvExportError("求人をCSVへ変換できませんでした") from exc


class CsvExporter:
    """全テキストセルをapostropheで無害化し、媒体別CSVを公開する。"""

    def __init__(
        self,
        output_dir: Path | str = Path("data/output"),
        validator: Validator | None = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._validator = validator or Validator()

    def export(self, provider: JobSource | str, jobs: Iterable[Job]) -> Path:
        source = _coerce_source(provider)
        materialized = list(jobs)
        content = serialize_jobs_csv(source, materialized, validator=self._validator)

        destination = self._output_dir / f"{source.value.lower()}_jobs.csv"
        temporary_path: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())

            _verify_csv(temporary_path, len(materialized))
            os.replace(temporary_path, destination)
            temporary_path = None
        except CsvExportError:
            raise
        except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as exc:
            raise CsvExportError(f"{destination.name} を出力できませんでした") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return destination


def _job_to_csv_row(job: Job) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": job.schema_version,
        "internal_job_id": job.internal_job_id,
        "source": job.source.value,
        "source_job_id": job.source_job_id,
        "source_url": job.source_url or "",
        "batch_id": job.batch_id,
        "job_status": job.job_status.value,
        "job_title": job.job_title,
        "company_name": job.company_name,
        "industry": job.industry,
        "occupation": job.occupation,
        "employment_type": job.employment_type,
        "location": job.location,
        "salary_text": job.salary_text,
        "salary_min": "" if job.salary_min is None else job.salary_min,
        "salary_max": "" if job.salary_max is None else job.salary_max,
        "salary_currency": job.salary_currency.value,
        "salary_period": job.salary_period.value,
        "salary_parse_status": job.salary_parse_status.value,
        "job_description": job.job_description,
        "required_conditions": job.required_conditions,
        "preferred_conditions": job.preferred_conditions,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "experience": job.experience,
        "qualification": job.qualification,
        "education": job.education,
        "age_requirement": job.age_requirement,
        "working_hours": job.working_hours,
        "holidays": job.holidays,
        "benefits": job.benefits,
        "selection_process": job.selection_process,
        "recruitment_reason": job.recruitment_reason,
        "number_of_positions": job.number_of_positions,
        "commission": job.commission,
        "success_fee_conditions": job.success_fee_conditions,
        "referral_reward_your_company": job.referral_reward_your_company,
        "referral_reward_total": job.referral_reward_total,
        "refund_policy": job.refund_policy,
        "remarks": job.remarks,
        "acquired_at": format_rfc3339(job.acquired_at),
        "last_verified_at": format_rfc3339(job.last_verified_at),
        "extractor_version": job.extractor_version,
    }
    for column in _TEXT_COLUMNS:
        value = record[column]
        if not isinstance(value, str):
            raise TypeError(f"{column} must be a string before CSV escaping")
        record[column] = "'" + value
    return record


def _verify_csv(path: Path, expected_rows: int) -> None:
    try:
        if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
            raise CsvExportError("一時CSVにUTF-8 BOMがありません")
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise CsvExportError("一時CSVが空です") from exc
            if tuple(header) != CSV_COLUMNS:
                raise CsvExportError("一時CSVのヘッダーが不正です")
            row_count = 0
            for row in reader:
                if len(row) != len(CSV_COLUMNS):
                    raise CsvExportError("一時CSVの列数が不正です")
                for index, column in enumerate(CSV_COLUMNS):
                    if column in _TEXT_COLUMNS and not row[index].startswith("'"):
                        raise CsvExportError("一時CSVのテキストセルが未処理です")
                row_count += 1
            if row_count != expected_rows:
                raise CsvExportError("一時CSVの行数が不正です")
    except CsvExportError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CsvExportError("一時CSVを検証できませんでした") from exc
