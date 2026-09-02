"""RawJobを共通Jobへ意味を変えずに正規化する。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .errors import NormalizationError
from .models import (
    SCHEMA_VERSION,
    Job,
    JobSource,
    JobStatus,
    RawJob,
    SalaryCurrency,
    SalaryParseStatus,
    SalaryPeriod,
    to_utc,
)


_TEXT_FIELDS = (
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
)
_RESERVED_INPUT_FIELDS = frozenset((*_TEXT_FIELDS, "job_status"))
_ALL_SPACE = re.compile(r"\s+")
_AMOUNT = re.compile(
    r"(?<![\d万千])(?:"
    r"(?P<man>\d{1,3}(?:,\d{3})*|\d+)万"
    r"(?:(?P<thousands>\d)千)?(?P<remainder>\d{1,4})?円"
    r"|(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>万円|千円|円))"
    r"(?![\d万千円])"
)
_RANGE_SEPARATOR = re.compile(r"\s*(?:[〜～~\-－–—]|から)\s*")
_LEADING_RANGE_MARKER = re.compile(r"^\s*(?:[〜～~\-－–—]|から)")
_SINGLE_LINE_FIELDS = frozenset(
    {
        "job_title",
        "company_name",
        "industry",
        "occupation",
        "employment_type",
        "location",
        "salary_text",
        "age_requirement",
        "number_of_positions",
        "commission",
        "referral_reward_your_company",
        "referral_reward_total",
        "job_status",
    }
)


class Normalizer:
    """Provider共通名へマッピング済みのfieldsをJobへ変換する。"""

    def normalize(self, raw_job: RawJob) -> Job:
        if not isinstance(raw_job, RawJob):
            raise TypeError("raw_job must be RawJob")

        try:
            source = (
                raw_job.source
                if isinstance(raw_job.source, JobSource)
                else JobSource(raw_job.source)
            )
        except (TypeError, ValueError) as exc:
            raise NormalizationError("未対応の媒体です") from exc

        source_job_id = _clean_optional(
            raw_job.source_job_id, "source_job_id", single_line=True
        )
        if not source_job_id:
            raise NormalizationError("安定した媒体求人IDが必要です")
        batch_id = _clean_required(raw_job.batch_id, "batch_id", single_line=True)
        source_url = (
            _clean_optional(raw_job.source_url, "source_url", single_line=True)
            or None
        )
        extractor_version = _clean_required(
            raw_job.extractor_version,
            "extractor_version",
            allow_empty=True,
            single_line=True,
        )

        fields = _normalize_fields(raw_job.fields)
        acquired_at = _normalize_datetime(raw_job.acquired_at, "acquired_at")
        last_verified_at = _normalize_datetime(
            raw_job.last_verified_at or raw_job.acquired_at,
            "last_verified_at",
        )

        salary_text = fields.get("salary_text", "")
        salary_min, salary_max, salary_currency, salary_period, parse_status = (
            _parse_salary(salary_text)
        )
        provider_specific = {
            key: value
            for key, value in fields.items()
            if key not in _RESERVED_INPUT_FIELDS
        }

        return Job(
            schema_version=SCHEMA_VERSION,
            internal_job_id=f"{source.value}:{source_job_id}",
            source=source,
            source_job_id=source_job_id,
            source_url=source_url,
            batch_id=batch_id,
            job_status=_parse_job_status(fields.get("job_status", "")),
            job_title=fields.get("job_title", ""),
            company_name=fields.get("company_name", ""),
            industry=fields.get("industry", ""),
            occupation=fields.get("occupation", ""),
            employment_type=fields.get("employment_type", ""),
            location=fields.get("location", ""),
            salary_text=salary_text,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            salary_parse_status=parse_status,
            job_description=fields.get("job_description", ""),
            required_conditions=fields.get("required_conditions", ""),
            preferred_conditions=fields.get("preferred_conditions", ""),
            required_skills=fields.get("required_skills", ""),
            preferred_skills=fields.get("preferred_skills", ""),
            experience=fields.get("experience", ""),
            qualification=fields.get("qualification", ""),
            education=fields.get("education", ""),
            age_requirement=fields.get("age_requirement", ""),
            working_hours=fields.get("working_hours", ""),
            holidays=fields.get("holidays", ""),
            benefits=fields.get("benefits", ""),
            selection_process=fields.get("selection_process", ""),
            recruitment_reason=fields.get("recruitment_reason", ""),
            number_of_positions=fields.get("number_of_positions", ""),
            commission=fields.get("commission", ""),
            success_fee_conditions=fields.get("success_fee_conditions", ""),
            referral_reward_your_company=fields.get(
                "referral_reward_your_company",
                "",
            ),
            referral_reward_total=fields.get("referral_reward_total", ""),
            refund_policy=fields.get("refund_policy", ""),
            remarks=fields.get("remarks", ""),
            acquired_at=acquired_at,
            last_verified_at=last_verified_at,
            extractor_version=extractor_version,
            provider_specific=provider_specific,
        )


def _normalize_fields(fields: object) -> dict[str, str]:
    if not hasattr(fields, "items"):
        raise NormalizationError("fields must be a mapping of strings")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in fields.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise NormalizationError("fieldsのキーと値は文字列である必要があります")
        key = raw_key.strip()
        if not key or "\x00" in key:
            raise NormalizationError("fieldsに空または不正なキーがあります")
        if key in normalized:
            raise NormalizationError("fieldsの正規化後キーが重複しています")
        normalized[key] = _clean_required(
            raw_value,
            key,
            allow_empty=True,
            single_line=key in _SINGLE_LINE_FIELDS,
        )
    return normalized


def _clean_optional(
    value: str | None, field_name: str, *, single_line: bool = False
) -> str:
    if value is None:
        return ""
    return _clean_required(
        value, field_name, allow_empty=True, single_line=single_line
    )


def _clean_required(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
    single_line: bool = False,
) -> str:
    if not isinstance(value, str):
        raise NormalizationError(f"{field_name} must be a string")
    if "\x00" in value:
        raise NormalizationError(f"{field_name} contains a null character")
    unified = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _ALL_SPACE.sub(" ", unified).strip() if single_line else unified.strip()
    if not cleaned and not allow_empty:
        raise NormalizationError(f"{field_name} must not be empty")
    return cleaned


def _normalize_datetime(value, field_name: str):
    try:
        return to_utc(value, field_name=field_name)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(f"{field_name} must be timezone-aware") from exc


def _parse_job_status(value: str) -> JobStatus:
    normalized = value.strip().lower()
    if normalized in {"active", "open", "募集中", "掲載中"}:
        return JobStatus.ACTIVE
    if normalized in {"closed", "終了", "募集終了", "掲載終了"}:
        return JobStatus.CLOSED
    return JobStatus.UNKNOWN


def _parse_salary(
    salary_text: str,
) -> tuple[
    int | None,
    int | None,
    SalaryCurrency,
    SalaryPeriod,
    SalaryParseStatus,
]:
    if not salary_text:
        return (
            None,
            None,
            SalaryCurrency.UNKNOWN,
            SalaryPeriod.UNKNOWN,
            SalaryParseStatus.EMPTY,
        )

    period = _salary_period(salary_text)
    matches = list(_AMOUNT.finditer(salary_text))
    if not matches:
        return (
            None,
            None,
            SalaryCurrency.UNKNOWN,
            period,
            SalaryParseStatus.UNPARSED,
        )

    # 金額だけでは年額・月額等を安全に比較できない。単位が円でも、
    # 期間を明示できない値は原文だけを残し、数値列へ推測値を入れない。
    if period is SalaryPeriod.UNKNOWN:
        return (
            None,
            None,
            SalaryCurrency.JPY,
            SalaryPeriod.UNKNOWN,
            SalaryParseStatus.UNPARSED,
        )

    try:
        amounts = [_amount_as_yen(match) for match in matches]
    except (InvalidOperation, ValueError):
        return (
            None,
            None,
            SalaryCurrency.JPY,
            period,
            SalaryParseStatus.UNPARSED,
        )

    if len(amounts) == 1:
        amount = amounts[0]
        if re.search(r"(?:以上|から)", salary_text[matches[0].end() :]):
            return amount, None, SalaryCurrency.JPY, period, SalaryParseStatus.PARTIAL
        if (
            re.search(r"(?:以下|まで)", salary_text[matches[0].end() :])
            or _LEADING_RANGE_MARKER.match(salary_text)
        ):
            return None, amount, SalaryCurrency.JPY, period, SalaryParseStatus.PARTIAL
        return amount, amount, SalaryCurrency.JPY, period, SalaryParseStatus.PARSED

    if (
        len(amounts) == 2
        and _RANGE_SEPARATOR.fullmatch(
            salary_text[matches[0].end() : matches[1].start()]
        )
        and amounts[0] <= amounts[1]
    ):
        return (
            amounts[0],
            amounts[1],
            SalaryCurrency.JPY,
            period,
            SalaryParseStatus.PARSED,
        )

    return (
        None,
        None,
        SalaryCurrency.JPY,
        period,
        SalaryParseStatus.UNPARSED,
    )


def _amount_as_yen(match: re.Match[str]) -> int:
    man = match.group("man")
    if man is not None:
        yen = int(man.replace(",", "")) * 10_000
        thousands = match.group("thousands")
        remainder = match.group("remainder")
        if thousands is not None:
            yen += int(thousands) * 1_000
        if remainder is not None:
            remainder_yen = int(remainder)
            if thousands is not None and remainder_yen >= 1_000:
                raise ValueError("salary remainder overlaps the thousands unit")
            yen += remainder_yen
        return yen

    number = Decimal(match.group("number").replace(",", ""))
    multiplier = {"万円": 10_000, "千円": 1_000, "円": 1}[match.group("unit")]
    yen = number * multiplier
    if yen != yen.to_integral_value() or yen < 0:
        raise ValueError("salary amount is not a non-negative integer yen value")
    return int(yen)


def _salary_period(text: str) -> SalaryPeriod:
    if "年収" in text or "年俸" in text:
        return SalaryPeriod.ANNUAL
    if "月給" in text:
        return SalaryPeriod.MONTHLY
    if "日給" in text:
        return SalaryPeriod.DAILY
    if "時給" in text:
        return SalaryPeriod.HOURLY
    return SalaryPeriod.UNKNOWN
