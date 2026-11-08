"""既存の媒体別CSVを厳格に検証し、比較用データへ変換する。"""

from __future__ import annotations

import csv
import io
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from recruit_job_tool.csv_exporter import CSV_COLUMNS
from recruit_job_tool.constants import MAX_CURRENT_JOBS
from recruit_job_tool.errors import PromptInputError
from recruit_job_tool.models import (
    JobSource,
    JobStatus,
    SalaryCurrency,
    SalaryParseStatus,
    SalaryPeriod,
    SCHEMA_VERSION,
)

from .models import (
    PromptJob,
    PromptReferralReward,
    PromptRequirements,
    PromptSalary,
)


DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_ROWS = MAX_CURRENT_JOBS
_BOM = b"\xef\xbb\xbf"
_NUMERIC_COLUMNS = frozenset({"schema_version", "salary_min", "salary_max"})
_TEXT_COLUMNS = frozenset(CSV_COLUMNS) - _NUMERIC_COLUMNS
_JOBINS_SOURCE_PATH = re.compile(r"^/agent/jobs/[^/]+$")
_CAREERBANK_SOURCE_PATH = re.compile(r"^/jobs/(?P<source_job_id>[0-9]+)$")


def load_prompt_jobs(
    path: Path,
    *,
    expected_source: JobSource,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[PromptJob]:
    """正式CSVを全件検証し、求人の表示順を維持して返す。"""

    source = _coerce_source(expected_source)
    input_path = Path(path)
    if input_path.suffix.lower() != ".csv":
        raise PromptInputError("入力ファイルの拡張子が不正です")
    if max_bytes < 1 or max_rows < 1:
        raise ValueError("max_bytes and max_rows must be positive")

    try:
        file_stat = input_path.stat()
    except OSError as exc:
        raise PromptInputError("入力CSVを参照できません") from exc
    if not input_path.is_file():
        raise PromptInputError("入力CSVが通常ファイルではありません")
    if file_stat.st_size < 1 or file_stat.st_size > max_bytes:
        raise PromptInputError("入力CSVのサイズが許容範囲外です")

    try:
        raw = input_path.read_bytes()
    except OSError as exc:
        raise PromptInputError("入力CSVを読み込めません") from exc
    return load_prompt_jobs_from_bytes(
        raw, expected_source=source, max_bytes=max_bytes, max_rows=max_rows,
    )


def load_prompt_jobs_from_bytes(
    raw: bytes, *, expected_source: JobSource,
    max_bytes: int = DEFAULT_MAX_BYTES, max_rows: int = DEFAULT_MAX_ROWS,
) -> list[PromptJob]:
    """ファイル入力と同じ規則で、メモリ上の正式CSVを検証する。"""
    source = _coerce_source(expected_source)
    if max_bytes < 1 or max_rows < 1:
        raise ValueError("max_bytes and max_rows must be positive")
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= max_bytes:
        raise PromptInputError("入力CSVのサイズが許容範囲外です")
    if not raw.startswith(_BOM):
        raise PromptInputError("入力CSVにUTF-8 BOMがありません")
    if b"\x00" in raw:
        raise PromptInputError("入力CSVにNULバイトが含まれています")
    try:
        decoded = raw.decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise PromptInputError("入力CSVの文字コードが不正です") from exc

    try:
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        header = next(reader, None)
        if header is None or tuple(header) != CSV_COLUMNS:
            raise PromptInputError("入力CSVのヘッダーが不正です")

        jobs: list[PromptJob] = []
        seen_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(CSV_COLUMNS):
                raise PromptInputError(
                    f"入力CSVの列数が不正です（行{row_number}）"
                )
            if len(jobs) >= max_rows:
                raise PromptInputError("入力CSVの求人数が上限を超えています")
            values = _decode_row(row, row_number=row_number)
            job = _to_prompt_job(
                values,
                expected_source=source,
                row_number=row_number,
            )
            if job.job_id in seen_ids:
                raise PromptInputError("入力CSVに重複する求人IDがあります")
            seen_ids.add(job.job_id)
            jobs.append(job)
    except PromptInputError:
        raise
    except csv.Error as exc:
        raise PromptInputError("入力CSVの構造が不正です") from exc

    if not jobs:
        raise PromptInputError("入力CSVに求人がありません")
    return jobs


def _decode_row(row: list[str], *, row_number: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for column, value in zip(CSV_COLUMNS, row, strict=True):
        if column in _TEXT_COLUMNS:
            if not value.startswith("'"):
                raise PromptInputError(
                    f"入力CSVのテキスト形式が不正です（行{row_number}）"
                )
            value = value[1:]
        values[column] = value
    return values


def _to_prompt_job(
    row: dict[str, str],
    *,
    expected_source: JobSource,
    row_number: int,
) -> PromptJob:
    if row["schema_version"] != str(SCHEMA_VERSION):
        raise PromptInputError(
            f"入力CSVのschema versionが不正です（行{row_number}）"
        )

    source = _parse_enum(JobSource, row["source"], row_number=row_number)
    _parse_enum(JobStatus, row["job_status"], row_number=row_number)
    currency = _parse_enum(
        SalaryCurrency,
        row["salary_currency"],
        row_number=row_number,
    )
    period = _parse_enum(SalaryPeriod, row["salary_period"], row_number=row_number)
    parse_status = _parse_enum(
        SalaryParseStatus,
        row["salary_parse_status"],
        row_number=row_number,
    )
    if source is not expected_source:
        raise PromptInputError(f"入力CSVの媒体が一致しません（行{row_number}）")

    source_job_id = row["source_job_id"]
    internal_job_id = row["internal_job_id"]
    if not source_job_id:
        raise PromptInputError(f"入力CSVの求人IDが空です（行{row_number}）")
    if internal_job_id != f"{source.value}:{source_job_id}":
        raise PromptInputError(
            f"入力CSVの内部求人IDが不正です（行{row_number}）"
        )
    if not row["job_title"]:
        raise PromptInputError(f"入力CSVの求人名が空です（行{row_number}）")

    source_url = _parse_source_url(
        row["source_url"],
        source=source,
        source_job_id=source_job_id,
        row_number=row_number,
    )

    minimum = _parse_optional_uint(row["salary_min"], row_number=row_number)
    maximum = _parse_optional_uint(row["salary_max"], row_number=row_number)
    _validate_salary(
        text=row["salary_text"],
        minimum=minimum,
        maximum=maximum,
        status=parse_status,
        row_number=row_number,
    )

    return PromptJob(
        job_id=internal_job_id,
        source_url=source_url,
        company=_optional_text(row["company_name"]),
        job_title=row["job_title"],
        industry=_optional_text(row["industry"]),
        location=_optional_text(row["location"]),
        salary=PromptSalary(
            text=_optional_text(row["salary_text"]),
            minimum=minimum,
            maximum=maximum,
            currency=currency.value,
            period=period.value,
            parse_status=parse_status.value,
        ),
        application_requirements=PromptRequirements(
            required=_optional_text(row["required_conditions"]),
            preferred=_optional_text(row["preferred_conditions"]),
            education=_optional_text(row["education"]),
            age=_optional_text(row["age_requirement"]),
        ),
        referral_reward=PromptReferralReward(
            your_company=_optional_text(row["referral_reward_your_company"]),
            total=_optional_text(row["referral_reward_total"]),
        ),
    )


def _parse_source_url(
    value: str,
    *,
    source: JobSource,
    source_job_id: str,
    row_number: int,
) -> str | None:
    if value == "":
        return None

    invalid_message = f"入力CSVの求人URLが不正です（行{row_number}）"
    if any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    ):
        raise PromptInputError(invalid_message)

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise PromptInputError(invalid_message) from exc

    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PromptInputError(invalid_message)

    if source is JobSource.JOBINS:
        path_match = _JOBINS_SOURCE_PATH.fullmatch(parsed.path)
        expected_host = "jobins.jp"
    else:
        path_match = _CAREERBANK_SOURCE_PATH.fullmatch(parsed.path)
        expected_host = "careerbank-jobsearch.com"

    if hostname != expected_host or path_match is None:
        raise PromptInputError(invalid_message)
    if parsed.netloc != expected_host:
        raise PromptInputError(invalid_message)
    if value != f"https://{expected_host}{parsed.path}":
        raise PromptInputError(invalid_message)
    if (
        source is JobSource.CAREERBANK
        and path_match.group("source_job_id") != source_job_id
    ):
        raise PromptInputError(invalid_message)
    return value


def _parse_optional_uint(value: str, *, row_number: int) -> int | None:
    if value == "":
        return None
    if not value.isascii() or not value.isdecimal():
        raise PromptInputError(f"入力CSVの給与数値が不正です（行{row_number}）")
    return int(value)


def _validate_salary(
    *,
    text: str,
    minimum: int | None,
    maximum: int | None,
    status: SalaryParseStatus,
    row_number: int,
) -> None:
    if minimum is not None and maximum is not None and minimum > maximum:
        raise PromptInputError(f"入力CSVの給与範囲が不正です（行{row_number}）")

    valid = {
        SalaryParseStatus.EMPTY: not text and minimum is None and maximum is None,
        SalaryParseStatus.UNPARSED: minimum is None and maximum is None,
        SalaryParseStatus.PARTIAL: (minimum is None) != (maximum is None),
        SalaryParseStatus.PARSED: minimum is not None and maximum is not None,
    }[status]
    if not valid:
        raise PromptInputError(f"入力CSVの給与状態が不正です（行{row_number}）")


def _parse_enum(enum_type, value: str, *, row_number: int):
    try:
        return enum_type(value)
    except ValueError as exc:
        raise PromptInputError(
            f"入力CSVの列挙値が不正です（行{row_number}）"
        ) from exc


def _optional_text(value: str) -> str | None:
    return value or None


def _coerce_source(value: JobSource) -> JobSource:
    if isinstance(value, JobSource):
        return value
    try:
        return JobSource(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected_source must be JobSource") from exc
