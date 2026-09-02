from __future__ import annotations

from datetime import datetime, timedelta, timezone

from recruit_job_tool import CandidateProfile
from recruit_job_tool.models import Job, JobSource, RawJob
from recruit_job_tool.normalizer import Normalizer


BASE_TIME = datetime(2026, 8, 8, 3, 4, 5, 123456, tzinfo=timezone.utc)


def make_candidate_profile(**overrides: str) -> CandidateProfile:
    values = {
        "age": "34歳",
        "gender": "回答しない",
        "education": "4年制大学卒業",
        "work_history": "法人営業3年",
        "qualifications": "普通運転免許",
    }
    values.update(overrides)
    return CandidateProfile(**values)


def make_raw_job(
    *,
    source: JobSource = JobSource.JOBINS,
    source_job_id: str | None = "123",
    title: str = "法人営業",
    acquired_at: datetime = BASE_TIME,
    last_verified_at: datetime | None = None,
    batch_id: str = "batch-1",
    source_url: str | None = "https://example.invalid/jobs/123",
    fields: dict[str, str] | None = None,
) -> RawJob:
    values = {
        "job_title": title,
        "company_name": "株式会社サンプル",
        "location": "東京都",
        "salary_text": "年収500万円～700万円",
        "job_status": "募集中",
        "job_description": "1行目\n2行目",
    }
    if fields:
        values.update(fields)
    return RawJob(
        source=source,
        source_job_id=source_job_id,
        source_url=source_url,
        fields=values,
        acquired_at=acquired_at,
        last_verified_at=last_verified_at or acquired_at,
        batch_id=batch_id,
        extractor_version="fixture-1",
    )


def make_job(**kwargs) -> Job:
    return Normalizer().normalize(make_raw_job(**kwargs))


def later(minutes: int = 1) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes)
