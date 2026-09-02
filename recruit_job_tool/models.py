"""Provider非依存の求人データモデルとJSON表現。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Mapping


SCHEMA_VERSION = 3


class JobSource(str, Enum):
    JOBINS = "JOBINS"
    CAREERBANK = "CAREERBANK"


class JobStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SalaryCurrency(str, Enum):
    JPY = "JPY"
    UNKNOWN = "unknown"


class SalaryPeriod(str, Enum):
    ANNUAL = "annual"
    MONTHLY = "monthly"
    DAILY = "daily"
    HOURLY = "hourly"
    UNKNOWN = "unknown"


class SalaryParseStatus(str, Enum):
    PARSED = "parsed"
    PARTIAL = "partial"
    UNPARSED = "unparsed"
    EMPTY = "empty"


def to_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    """aware datetimeだけを受け付け、UTCへ正規化する。"""

    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def format_rfc3339(value: datetime) -> str:
    return to_utc(value).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty RFC 3339 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not valid RFC 3339") from exc
    return to_utc(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawJob:
    """Providerから取得した未正規化データ。"""

    source: JobSource | str
    source_job_id: str | None
    fields: Mapping[str, str]
    acquired_at: datetime
    batch_id: str
    source_url: str | None = None
    last_verified_at: datetime | None = None
    extractor_version: str = ""


@dataclass(slots=True, kw_only=True)
class Job:
    """媒体間で共通利用する求人の現行スナップショット。"""

    SERIALIZED_FIELDS: ClassVar[tuple[str, ...]] = (
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
        "provider_specific",
    )
    SERIALIZED_FIELDS_V2: ClassVar[tuple[str, ...]] = tuple(
        field_name
        for field_name in SERIALIZED_FIELDS
        if field_name
        not in {"referral_reward_your_company", "referral_reward_total"}
    )
    SERIALIZED_FIELDS_V1: ClassVar[tuple[str, ...]] = tuple(
        field_name
        for field_name in SERIALIZED_FIELDS_V2
        if field_name not in {"age_requirement", "success_fee_conditions"}
    )

    schema_version: int
    internal_job_id: str
    source: JobSource
    source_job_id: str
    source_url: str | None
    batch_id: str
    job_status: JobStatus
    job_title: str
    company_name: str = ""
    industry: str = ""
    occupation: str = ""
    employment_type: str = ""
    location: str = ""
    salary_text: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: SalaryCurrency = SalaryCurrency.UNKNOWN
    salary_period: SalaryPeriod = SalaryPeriod.UNKNOWN
    salary_parse_status: SalaryParseStatus = SalaryParseStatus.EMPTY
    job_description: str = ""
    required_conditions: str = ""
    preferred_conditions: str = ""
    required_skills: str = ""
    preferred_skills: str = ""
    experience: str = ""
    qualification: str = ""
    education: str = ""
    age_requirement: str = ""
    working_hours: str = ""
    holidays: str = ""
    benefits: str = ""
    selection_process: str = ""
    recruitment_reason: str = ""
    number_of_positions: str = ""
    commission: str = ""
    success_fee_conditions: str = ""
    referral_reward_your_company: str = ""
    referral_reward_total: str = ""
    refund_policy: str = ""
    remarks: str = ""
    acquired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    extractor_version: str = ""
    provider_specific: dict[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, object]:
        """JSONへ安全に書ける固定スキーマの辞書を返す。"""

        return {
            "schema_version": SCHEMA_VERSION,
            "internal_job_id": self.internal_job_id,
            "source": self.source.value,
            "source_job_id": self.source_job_id,
            "source_url": self.source_url,
            "batch_id": self.batch_id,
            "job_status": self.job_status.value,
            "job_title": self.job_title,
            "company_name": self.company_name,
            "industry": self.industry,
            "occupation": self.occupation,
            "employment_type": self.employment_type,
            "location": self.location,
            "salary_text": self.salary_text,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_currency": self.salary_currency.value,
            "salary_period": self.salary_period.value,
            "salary_parse_status": self.salary_parse_status.value,
            "job_description": self.job_description,
            "required_conditions": self.required_conditions,
            "preferred_conditions": self.preferred_conditions,
            "required_skills": self.required_skills,
            "preferred_skills": self.preferred_skills,
            "experience": self.experience,
            "qualification": self.qualification,
            "education": self.education,
            "age_requirement": self.age_requirement,
            "working_hours": self.working_hours,
            "holidays": self.holidays,
            "benefits": self.benefits,
            "selection_process": self.selection_process,
            "recruitment_reason": self.recruitment_reason,
            "number_of_positions": self.number_of_positions,
            "commission": self.commission,
            "success_fee_conditions": self.success_fee_conditions,
            "referral_reward_your_company": self.referral_reward_your_company,
            "referral_reward_total": self.referral_reward_total,
            "refund_policy": self.refund_policy,
            "remarks": self.remarks,
            "acquired_at": format_rfc3339(self.acquired_at),
            "last_verified_at": format_rfc3339(self.last_verified_at),
            "extractor_version": self.extractor_version,
            "provider_specific": dict(self.provider_specific),
        }

    @classmethod
    def from_record(cls, record: object) -> Job:
        """型の暗黙変換を行わず、固定スキーマのJSON辞書を復元する。"""

        if not isinstance(record, dict):
            raise ValueError("job record must be an object")
        if "schema_version" not in record:
            raise ValueError("job record is missing schema_version")
        schema_version = _required_int(record, "schema_version")
        actual = set(record)
        if schema_version == 1:
            expected = set(cls.SERIALIZED_FIELDS_V1)
        elif schema_version == 2:
            expected = set(cls.SERIALIZED_FIELDS_V2)
        elif schema_version == SCHEMA_VERSION:
            expected = set(cls.SERIALIZED_FIELDS)
        else:
            raise ValueError("schema_version is unsupported")
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(f"job record fields mismatch: missing={missing}, unknown={unknown}")

        if schema_version in {1, 2}:
            record = dict(record)
            record["schema_version"] = SCHEMA_VERSION
            record["referral_reward_your_company"] = ""
            record["referral_reward_total"] = ""
        if schema_version == 1:
            record["age_requirement"] = ""
            record["success_fee_conditions"] = ""
        if schema_version in {1, 2}:
            schema_version = SCHEMA_VERSION
        salary_min = _optional_int(record, "salary_min")
        salary_max = _optional_int(record, "salary_max")
        source_url = _optional_str(record, "source_url")
        provider_specific = record["provider_specific"]
        if not isinstance(provider_specific, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in provider_specific.items()
        ):
            raise ValueError("provider_specific must be an object of strings")

        return cls(
            schema_version=schema_version,
            internal_job_id=_required_str(record, "internal_job_id"),
            source=_enum_value(JobSource, record["source"], "source"),
            source_job_id=_required_str(record, "source_job_id"),
            source_url=source_url,
            batch_id=_required_str(record, "batch_id"),
            job_status=_enum_value(JobStatus, record["job_status"], "job_status"),
            job_title=_required_str(record, "job_title"),
            company_name=_required_str(record, "company_name"),
            industry=_required_str(record, "industry"),
            occupation=_required_str(record, "occupation"),
            employment_type=_required_str(record, "employment_type"),
            location=_required_str(record, "location"),
            salary_text=_required_str(record, "salary_text"),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=_enum_value(
                SalaryCurrency, record["salary_currency"], "salary_currency"
            ),
            salary_period=_enum_value(
                SalaryPeriod, record["salary_period"], "salary_period"
            ),
            salary_parse_status=_enum_value(
                SalaryParseStatus,
                record["salary_parse_status"],
                "salary_parse_status",
            ),
            job_description=_required_str(record, "job_description"),
            required_conditions=_required_str(record, "required_conditions"),
            preferred_conditions=_required_str(record, "preferred_conditions"),
            required_skills=_required_str(record, "required_skills"),
            preferred_skills=_required_str(record, "preferred_skills"),
            experience=_required_str(record, "experience"),
            qualification=_required_str(record, "qualification"),
            education=_required_str(record, "education"),
            age_requirement=_required_str(record, "age_requirement"),
            working_hours=_required_str(record, "working_hours"),
            holidays=_required_str(record, "holidays"),
            benefits=_required_str(record, "benefits"),
            selection_process=_required_str(record, "selection_process"),
            recruitment_reason=_required_str(record, "recruitment_reason"),
            number_of_positions=_required_str(record, "number_of_positions"),
            commission=_required_str(record, "commission"),
            success_fee_conditions=_required_str(
                record,
                "success_fee_conditions",
            ),
            referral_reward_your_company=_required_str(
                record,
                "referral_reward_your_company",
            ),
            referral_reward_total=_required_str(
                record,
                "referral_reward_total",
            ),
            refund_policy=_required_str(record, "refund_policy"),
            remarks=_required_str(record, "remarks"),
            acquired_at=parse_rfc3339(record["acquired_at"], field_name="acquired_at"),
            last_verified_at=parse_rfc3339(
                record["last_verified_at"], field_name="last_verified_at"
            ),
            extractor_version=_required_str(record, "extractor_version"),
            provider_specific=dict(provider_specific),
        )


def _required_str(record: dict[object, object], name: str) -> str:
    value = record[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_str(record: dict[object, object], name: str) -> str | None:
    value = record[name]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _required_int(record: dict[object, object], name: str) -> int:
    value = record[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_int(record: dict[object, object], name: str) -> int | None:
    value = record[name]
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{name} must be an integer or null")
    return value


def _enum_value(enum_type: type[Enum], value: object, name: str):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{name} has an unsupported value") from exc
