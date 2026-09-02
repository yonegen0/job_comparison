"""共通Jobの実行時検証。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from .models import (
    SCHEMA_VERSION,
    Job,
    JobSource,
    JobStatus,
    SalaryCurrency,
    SalaryParseStatus,
    SalaryPeriod,
)


@dataclass(slots=True)
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Validator:
    """永続化・CSV出力前にJobの型と不変条件を検証する。"""

    def validate(self, job: Job) -> ValidationResult:
        if not isinstance(job, Job):
            return ValidationResult(False, ["job_type_invalid"], [])

        errors: list[str] = []
        warnings: list[str] = []

        if isinstance(job.schema_version, bool) or job.schema_version != SCHEMA_VERSION:
            errors.append("schema_version_unsupported")
        if not isinstance(job.source, JobSource):
            errors.append("source_unknown")
        if not _non_empty_string(job.source_job_id):
            errors.append("source_job_id_empty")
        if not _non_empty_string(job.internal_job_id):
            errors.append("internal_job_id_empty")
        elif isinstance(job.source, JobSource) and _non_empty_string(job.source_job_id):
            expected_id = f"{job.source.value}:{job.source_job_id}"
            if job.internal_job_id != expected_id:
                errors.append("internal_job_id_mismatch")
        if not _non_empty_string(job.batch_id):
            errors.append("batch_id_empty")
        if not _non_empty_string(job.job_title):
            errors.append("job_title_empty")
        if not isinstance(job.job_status, JobStatus):
            errors.append("job_status_unknown")
        elif job.job_status is JobStatus.UNKNOWN:
            warnings.append("job_status_unknown")

        for field_name in _TEXT_FIELDS:
            if not isinstance(getattr(job, field_name), str):
                errors.append(f"{field_name}_type_invalid")
        if job.source_url is not None:
            if not isinstance(job.source_url, str):
                errors.append("source_url_type_invalid")
            elif job.source_url:
                try:
                    parsed_url = urlsplit(job.source_url)
                    valid_url = (
                        parsed_url.scheme in {"http", "https"}
                        and bool(parsed_url.netloc)
                        and parsed_url.hostname is not None
                    )
                except ValueError:
                    valid_url = False
                if not valid_url:
                    errors.append("source_url_invalid")
        else:
            warnings.append("source_url_empty")

        _validate_datetime(job.acquired_at, "acquired_at", errors)
        _validate_datetime(job.last_verified_at, "last_verified_at", errors)
        if (
            _is_aware(job.acquired_at)
            and _is_aware(job.last_verified_at)
            and job.last_verified_at < job.acquired_at
        ):
            errors.append("last_verified_before_acquired")

        for field_name in ("salary_min", "salary_max"):
            value = getattr(job, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                errors.append(f"{field_name}_invalid")
        if (
            isinstance(job.salary_min, int)
            and not isinstance(job.salary_min, bool)
            and isinstance(job.salary_max, int)
            and not isinstance(job.salary_max, bool)
            and job.salary_min > job.salary_max
        ):
            errors.append("salary_range_invalid")
        if not isinstance(job.salary_currency, SalaryCurrency):
            errors.append("salary_currency_unknown")
        if not isinstance(job.salary_period, SalaryPeriod):
            errors.append("salary_period_unknown")
        elif job.salary_period is SalaryPeriod.UNKNOWN and job.salary_text:
            warnings.append("salary_period_unknown")
        if not isinstance(job.salary_parse_status, SalaryParseStatus):
            errors.append("salary_parse_status_unknown")
        else:
            _validate_salary_state(job, errors)

        if not isinstance(job.provider_specific, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in job.provider_specific.items()
        ):
            errors.append("provider_specific_invalid")

        if not job.company_name:
            warnings.append("company_name_empty")
        if not job.location:
            warnings.append("location_empty")
        if not job.salary_text:
            warnings.append("salary_empty")

        return ValidationResult(not errors, errors, warnings)


_TEXT_FIELDS = (
    "internal_job_id",
    "source_job_id",
    "batch_id",
    "job_title",
    "company_name",
    "industry",
    "occupation",
    "employment_type",
    "location",
    "salary_text",
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
    "extractor_version",
)


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _validate_datetime(value: object, field_name: str, errors: list[str]) -> None:
    if not _is_aware(value):
        errors.append(f"{field_name}_not_aware")


def _validate_salary_state(job: Job, errors: list[str]) -> None:
    if job.salary_parse_status is SalaryParseStatus.EMPTY:
        if job.salary_text or job.salary_min is not None or job.salary_max is not None:
            errors.append("salary_empty_state_inconsistent")
    elif job.salary_parse_status is SalaryParseStatus.UNPARSED:
        if job.salary_min is not None or job.salary_max is not None:
            errors.append("salary_unparsed_state_inconsistent")
    elif job.salary_parse_status is SalaryParseStatus.PARTIAL:
        if (job.salary_min is None) == (job.salary_max is None):
            errors.append("salary_partial_state_inconsistent")
    elif job.salary_parse_status is SalaryParseStatus.PARSED:
        if job.salary_min is None or job.salary_max is None:
            errors.append("salary_parsed_state_inconsistent")
